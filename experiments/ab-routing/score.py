#!/usr/bin/env python3
"""score.py: run each task's hidden tests against each arm's copy, and compute
the pass bar (does Arm A pass at least 90% of what Arm B passes?).

Usage:
  score.py --run <id> [--runs-root <path>] [--venv <path>]
  score.py --validate [--venv <path>]

--run clones each (arm, task) copy's CURRENT WORKING TREE (a plain directory
copy, uncommitted changes included -- agents are not expected to have
committed anything; never `git clone`) to a throwaway temp directory, runs
that task's hidden suite there with the shared interpreter under a timeout,
and writes <run>/results.json: per arm, per task, per test (name, mark,
outcome), counts, and the pass bar (tests_passed_by_both / tests_passed_by_B,
overall and per task, each with a >= 0.90 boolean). Nothing ever runs inside
the original task copy; after scoring, each is asserted byte-for-byte
untouched (HEAD plus `git status --porcelain --ignored`, before vs after).

A suite that errors at collection reports no individual test results; any
test from hidden_tests/inventory.json (the pristine-collected inventory) not
found among the actual results is counted as failed, so nothing silently
drops out of the pass-bar denominator.

--validate builds its own throwaway copies from the pristine (git-tracked)
fixture and checks: (a) every target test fails and every guard test passes
on pristine, exactly matching inventory.json's marks; (b) with each task's
reference/taskN.patch applied, 100% of that task's hidden tests pass. Exits
non-zero with a readable expectation-vs-outcome diff otherwise.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
HIDDEN_TESTS = HERE / "hidden_tests"
REFERENCE_DIR = HERE / "reference"
FIXTURE_REL = "evals/fixture/shopapi"
DEFAULT_RUNS_ROOT = Path("~/.cache/ab-routing/runs").expanduser()
DEFAULT_VENV = Path("~/.cache/shopapi-dev/venv").expanduser()
TASKS = (1, 2, 3, 4)
ARMS = ("A", "B")
TEST_TIMEOUT_SECONDS = 120
PASS_BAR = 0.90


# ----------------------------------------------------------------------------
# Paths / repo
# ----------------------------------------------------------------------------
def find_repo_root() -> Path:
    candidate = HERE.parents[1]
    if (candidate / FIXTURE_REL).is_dir():
        return candidate
    out = subprocess.run(["git", "-C", str(HERE), "rev-parse", "--show-toplevel"],
                          check=True, capture_output=True, text=True).stdout.strip()
    return Path(out)


def venv_python(venv_dir: Path) -> Path:
    return venv_dir / "bin" / "python"


def tracked_fixture_files(repo_root: Path) -> list[str]:
    out = subprocess.run(["git", "-C", str(repo_root), "ls-files", FIXTURE_REL],
                          check=True, capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line.strip()]


def copy_pristine_fixture(repo_root: Path, tracked: list[str], dest: Path) -> None:
    """A plain copy of the git-tracked fixture files only (no shop.db, caches,
    or .git), stripped of the evals/fixture/shopapi/ prefix. Never writes
    inside evals/fixture/ itself -- only ever reads from it."""
    prefix = FIXTURE_REL + "/"
    dest.mkdir(parents=True, exist_ok=True)
    for rel in tracked:
        if not rel.startswith(prefix):
            continue
        out = dest / rel[len(prefix):]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes((repo_root / rel).read_bytes())


def clone_working_tree(src: Path, dest: Path) -> None:
    """A plain directory copy of src's CURRENT working tree (uncommitted
    changes included) -- never `git clone`, since an agent's copy is not
    expected to be committed. Excludes .git and any caches."""
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))


# ----------------------------------------------------------------------------
# Inventory / JUnit
# ----------------------------------------------------------------------------
def load_inventory() -> dict:
    with open(HIDDEN_TESTS / "inventory.json", encoding="utf-8") as f:
        data = json.load(f)
    return {task: tests for task, tests in data.items() if not task.startswith("_")}


def read_junit_cases(junit_path: Path) -> list[dict]:
    tree = ET.parse(junit_path)
    root = tree.getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    cases = []
    for suite in suites:
        for tc in suite.findall("testcase"):
            cases.append({
                "classname": tc.get("classname") or "",
                "name": tc.get("name"),
                "failed": tc.find("failure") is not None or tc.find("error") is not None,
            })
    return cases


def actual_outcomes(junit_path: Path) -> dict:
    """{"file.py::test_name": failed_bool}. A whole-module collection failure
    reports one synthetic entry (classname="", name="<dotted module>") that
    matches no inventory key, so every real test for that module is simply
    absent here -- callers must treat a missing key as failed."""
    if not junit_path.exists():
        return {}
    outcomes = {}
    for c in read_junit_cases(junit_path):
        if not c["classname"]:
            continue  # collection-failure placeholder; contributes nothing
        file_stem = c["classname"].rsplit(".", 1)[-1]
        outcomes[f"{file_stem}.py::{c['name']}"] = c["failed"]
    return outcomes


# ----------------------------------------------------------------------------
# Running a suite
# ----------------------------------------------------------------------------
def run_hidden_suite(py: Path, task_n: int, target_dir: Path, junit_path: Path,
                      timeout: int = TEST_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    # --target=<path> (equals form): a space-separated "--target <path>" is
    # misparsed by pytest's early, pre-plugin argv scan for conftest.py files
    # to preload, which treats ANY existing-path token in argv as a place to
    # look for one -- including the target's own tests/conftest.py, before
    # this process has chdir'd/sys.path'd into it. The Wave 1B harness build
    # hit this directly (a reference solution's own conftest.py failed to
    # import "app" when passed as "--target <path>"); the "=" form sidesteps
    # it since the value is never a bare argv token.
    cmd = [str(py), "-m", "pytest", "-q", f"--target={target_dir}", f"--junitxml={junit_path}",
           str(HIDDEN_TESTS / f"task{task_n}")]
    try:
        return subprocess.run(cmd, cwd=str(HIDDEN_TESTS), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        stdout = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr = (e.stderr or b"").decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        return subprocess.CompletedProcess(cmd, 124, stdout, stderr + f"\ntimed out after {timeout}s")


def score_against(py: Path, task_n: int, source_dir: Path, work_root: Path, label: str,
                   inventory_for_task: dict) -> dict:
    """Clones source_dir (never runs anything inside it) and scores it against
    task_n's hidden suite. Returns {"file.py::test": {"mark", "outcome"}} for
    every test in the inventory (missing/errored -> "fail")."""
    clone = work_root / f"{label}_clone"
    clone_working_tree(source_dir, clone)
    junit_path = work_root / f"{label}.xml"
    run_hidden_suite(py, task_n, clone, junit_path)
    actual = actual_outcomes(junit_path)

    per_test = {}
    for key, mark in inventory_for_task.items():
        failed = actual.get(key, True)
        per_test[key] = {"mark": mark, "outcome": "fail" if failed else "pass"}
    return per_test


def summarize_counts(per_test: dict) -> dict:
    counts = {"target": {"passed": 0, "total": 0}, "guard": {"passed": 0, "total": 0}}
    for v in per_test.values():
        bucket = counts[v["mark"]]
        bucket["total"] += 1
        if v["outcome"] == "pass":
            bucket["passed"] += 1
    return counts


def bar_ratio(both_count: int, b_count: int) -> dict:
    """{"tests_passed_by_B", "tests_passed_by_both", "ratio", "meets_bar"} for
    one denominator/numerator pair. B passing zero tests makes the ratio
    undefined (None) rather than a division by zero -- that never counts as
    meeting the bar."""
    ratio = (both_count / b_count) if b_count else None
    return {
        "tests_passed_by_B": b_count,
        "tests_passed_by_both": both_count,
        "ratio": ratio,
        "meets_bar": ratio is not None and ratio >= PASS_BAR,
    }


def compute_pass_bar(results_by_task_arm: dict) -> dict:
    """results_by_task_arm: {(arm, task_n): {"file.py::test": {"mark","outcome"}}}
    for every arm/task pair. Returns the full pass_bar block: overall plus
    per-task ratios of tests_passed_by_both / tests_passed_by_B."""
    per_task_bar = {}
    total_b_passed = 0
    total_both_passed = 0
    tasks = sorted({n for (_, n) in results_by_task_arm})
    for n in tasks:
        a_tests = results_by_task_arm[("A", n)]
        b_tests = results_by_task_arm[("B", n)]
        b_passed = {k for k, v in b_tests.items() if v["outcome"] == "pass"}
        both_passed = {k for k in b_passed if a_tests[k]["outcome"] == "pass"}
        total_b_passed += len(b_passed)
        total_both_passed += len(both_passed)
        per_task_bar[str(n)] = bar_ratio(len(both_passed), len(b_passed))
    return {
        "threshold": PASS_BAR,
        "overall": bar_ratio(total_both_passed, total_b_passed),
        "per_task": per_task_bar,
    }


# ----------------------------------------------------------------------------
# --run
# ----------------------------------------------------------------------------
def snapshot_task_copy(task_dir: Path) -> tuple[str, str]:
    head = subprocess.run(["git", "-C", str(task_dir), "rev-parse", "HEAD"],
                           check=True, capture_output=True, text=True).stdout.strip()
    status = subprocess.run(["git", "-C", str(task_dir), "status", "--porcelain", "--ignored"],
                             check=True, capture_output=True, text=True).stdout
    return head, status


def cmd_run(args: argparse.Namespace) -> int:
    runs_root = Path(args.runs_root).expanduser() if args.runs_root else DEFAULT_RUNS_ROOT
    run_dir = runs_root / args.run
    if not run_dir.is_dir():
        print(f"score: run {args.run!r} not found at {run_dir}", file=sys.stderr)
        return 1

    venv_dir = Path(args.venv).expanduser() if args.venv else DEFAULT_VENV
    py = venv_python(venv_dir)
    if not py.exists():
        print(f"score: shared interpreter not found at {py} (run make_env.py first)", file=sys.stderr)
        return 1

    inventory = load_inventory()

    task_dirs = {(arm, n): run_dir / arm / f"task{n}" for arm in ARMS for n in TASKS}
    for (arm, n), d in task_dirs.items():
        if not d.is_dir():
            print(f"score: {d} does not exist", file=sys.stderr)
            return 1
    before = {key: snapshot_task_copy(d) for key, d in task_dirs.items()}

    results_by_task_arm = {}
    with tempfile.TemporaryDirectory(prefix="ab-routing-score-") as tmp:
        work_root = Path(tmp)
        for (arm, n), d in task_dirs.items():
            label = f"{arm}_task{n}"
            results_by_task_arm[(arm, n)] = score_against(py, n, d, work_root, label, inventory[f"task{n}"])

    after = {key: snapshot_task_copy(d) for key, d in task_dirs.items()}
    mutated = [key for key in task_dirs if before[key] != after[key]]
    if mutated:
        print("score: the following task copies were modified by scoring (this must never happen): "
              + ", ".join(f"{arm}/task{n}" for arm, n in mutated), file=sys.stderr)
        return 1

    tasks_out = {
        str(n): {
            "A": {"tests": results_by_task_arm[("A", n)], "counts": summarize_counts(results_by_task_arm[("A", n)])},
            "B": {"tests": results_by_task_arm[("B", n)], "counts": summarize_counts(results_by_task_arm[("B", n)])},
        }
        for n in TASKS
    }
    pass_bar = compute_pass_bar(results_by_task_arm)
    report = {"run": args.run, "tasks": tasks_out, "pass_bar": pass_bar}

    out_path = run_dir / "results.json"
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    overall = pass_bar["overall"]
    verdict = "MEETS" if overall["meets_bar"] else "does NOT meet"
    print(f"score: wrote {out_path} (overall {verdict} the {PASS_BAR:.0%} bar: "
          f"{overall['tests_passed_by_both']}/{overall['tests_passed_by_B']})")
    return 0


# ----------------------------------------------------------------------------
# --validate
# ----------------------------------------------------------------------------
def cmd_validate(args: argparse.Namespace) -> int:
    venv_dir = Path(args.venv).expanduser() if args.venv else DEFAULT_VENV
    py = venv_python(venv_dir)
    if not py.exists():
        print(f"score --validate: shared interpreter not found at {py} (run make_env.py first)", file=sys.stderr)
        return 1

    repo_root = find_repo_root()
    tracked = tracked_fixture_files(repo_root)
    if not tracked:
        print(f"score --validate: no tracked files found under {FIXTURE_REL}", file=sys.stderr)
        return 1
    inventory = load_inventory()
    problems: list[str] = []

    with tempfile.TemporaryDirectory(prefix="ab-routing-validate-") as tmp:
        work_root = Path(tmp)

        for n in TASKS:
            # (a) pristine: every target fails, every guard passes, exactly per inventory.
            pristine_dir = work_root / f"pristine_task{n}"
            copy_pristine_fixture(repo_root, tracked, pristine_dir)
            junit = work_root / f"pristine_task{n}.xml"
            run_hidden_suite(py, n, pristine_dir, junit)
            actual = actual_outcomes(junit)
            for key, mark in inventory[f"task{n}"].items():
                if key not in actual:
                    problems.append(f"task{n} pristine: {key} ({mark}) did not run at all "
                                     f"(collection problem?)")
                    continue
                failed = actual[key]
                expected_pass = (mark == "guard")
                got_pass = not failed
                if expected_pass != got_pass:
                    problems.append(
                        f"task{n} pristine: {key} marked {mark!r}, expected to "
                        f"{'pass' if expected_pass else 'fail'} but {'passed' if got_pass else 'failed'}")

            # (b) reference patch applied: 100% pass for that task.
            ref_dir = work_root / f"reference_task{n}"
            copy_pristine_fixture(repo_root, tracked, ref_dir)
            patch = REFERENCE_DIR / f"task{n}.patch"
            if not patch.exists():
                problems.append(f"task{n} reference: {patch} does not exist")
                continue
            apply_result = subprocess.run(["git", "apply", str(patch)], cwd=str(ref_dir),
                                           capture_output=True, text=True)
            if apply_result.returncode != 0:
                problems.append(f"task{n} reference: patch did not apply: "
                                 f"{apply_result.stderr.strip()}")
                continue
            junit2 = work_root / f"reference_task{n}.xml"
            run_hidden_suite(py, n, ref_dir, junit2)
            actual2 = actual_outcomes(junit2)
            for key, mark in inventory[f"task{n}"].items():
                if key not in actual2 or actual2[key]:
                    problems.append(f"task{n} reference: {key} ({mark}) did not pass")

            # (c) regression: a solution may add its own tests (the briefs never
            # forbid it). Task 1's checks are the only ones that literally run
            # the solution's own `pytest`, so it's the only task where this
            # could bite -- a solution-authored extra test file must not make
            # the hidden suite fail or miscount. (Fixed 2026-09-19: two task 1
            # checks asserted an exact collected-test count instead of "the
            # expected tests are present and passed".)
            if n == 1:
                extra_dir = work_root / f"reference_task{n}_with_extra_test"
                copy_pristine_fixture(repo_root, tracked, extra_dir)
                subprocess.run(["git", "apply", str(patch)], cwd=str(extra_dir), check=True,
                                capture_output=True, text=True)
                (extra_dir / "tests" / "test_solution_authored.py").write_text(
                    "def test_something_the_solution_added():\n    assert True\n", encoding="utf-8")
                junit3 = work_root / f"reference_task{n}_with_extra_test.xml"
                run_hidden_suite(py, n, extra_dir, junit3)
                actual3 = actual_outcomes(junit3)
                for key, mark in inventory[f"task{n}"].items():
                    if key not in actual3 or actual3[key]:
                        problems.append(f"task{n} reference+solution-authored-test: "
                                         f"{key} ({mark}) did not pass")

    if problems:
        print(f"score --validate: FAILED ({len(problems)} problem(s))", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    print("score --validate: OK -- pristine marks match inventory.json exactly, "
          "and every reference patch makes its task's hidden tests pass 100%.")
    return 0


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run", help="run id to score")
    mode.add_argument("--validate", action="store_true", help="validate marks + reference patches, no run needed")
    p.add_argument("--runs-root", help=f"default: {DEFAULT_RUNS_ROOT}")
    p.add_argument("--venv", help=f"default: {DEFAULT_VENV}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.validate:
        return cmd_validate(args)
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
