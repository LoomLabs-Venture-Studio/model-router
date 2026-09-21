#!/usr/bin/env python3
"""route_arm_a.py: Arm A of the routing experiment -- score one task's brief with
route.py's Jev scorer, unedited, and log the full decision.

Builds --context by script from the task's own fixture copy (tracked file count,
total Python line count, the test command, and whether tests currently pass), then
runs `route.py score --task <brief, verbatim> --context <context> --route --jev
--json` as a subprocess from the repo root (so route.py's own `.env` lookup finds
TYPESAFE_API_KEY), with COMPLEXITY_LOG redirected into this run's own directory so
the experiment never touches the user's real decision log.

There is no flag to supply or edit scores: they only ever come from parsing
route.py's own stdout. On failure, the decision is still recorded (as a failure)
and this script exits non-zero with route.py's one-line message.

Usage: route_arm_a.py --run <id> --task <1-4> --brief <path> [--runs-root <path>]
                       [--after-probe --probe-output <path>]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DIMS = ["S", "R", "A", "K", "I", "P", "V"]
DEFAULT_RUNS_ROOT = Path("~/.cache/ab-routing/runs").expanduser()
ROUTE_TIMEOUT_SECONDS = 45  # route.py's own Jev HTTP timeout is 20s; leave headroom
PYTEST_TIMEOUT_SECONDS = 60


class TaskCopyMutated(Exception):
    """Raised when the task copy is no longer pristine after building context for
    it -- e.g. an import-time side effect (a fixture module that opens its sqlite
    file on import) left shop.db, __pycache__, or .pytest_cache behind. Arm A and
    Arm B must start from the same state, so this is a hard stop, not a warning."""


# ----------------------------------------------------------------------------
# Repo / paths
# ----------------------------------------------------------------------------
def find_repo_root() -> Path:
    here = Path(__file__).resolve()
    candidate = here.parents[2]
    if (candidate / "skills" / "complexity" / "scripts" / "route.py").is_file():
        return candidate
    out = subprocess.run(["git", "-C", str(here.parent), "rev-parse", "--show-toplevel"],
                          check=True, capture_output=True, text=True).stdout.strip()
    return Path(out)


# ----------------------------------------------------------------------------
# Context building (script-generated, never human-edited)
# ----------------------------------------------------------------------------
def run_pytest(task_dir: Path, timeout: int = PYTEST_TIMEOUT_SECONDS) -> dict:
    """Runs pytest against an ISOLATED COPY of task_dir, never task_dir itself.
    Some fixture modules have import-time side effects (e.g. opening their sqlite
    file on import), which would otherwise leave shop.db, __pycache__, or
    .pytest_cache inside the copy the agent is about to see -- a state Arm B's
    copy never gets. The copy (and everything pytest wrote into it) is discarded
    once the run finishes.

    A failing/erroring test suite is an expected state of the fixture, not a
    failure of this function; only a timeout or a failure to launch pytest at
    all is reported as such."""
    with tempfile.TemporaryDirectory(prefix="ab-routing-context-") as tmp:
        copy_dir = Path(tmp) / "copy"
        shutil.copytree(task_dir, copy_dir, ignore=shutil.ignore_patterns(".git"))
        try:
            proc = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=str(copy_dir),
                                   capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "summary": f"pytest did not finish within {timeout}s",
                    "passed": 0, "failed": 0, "errors": 0}
        except OSError as e:
            return {"status": "error", "summary": f"could not run pytest: {e}",
                    "passed": 0, "failed": 0, "errors": 0}

        lines = [l for l in proc.stdout.strip().splitlines() if l.strip()]
        summary = lines[-1] if lines else ""
        counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
        for n, label in re.findall(r"(\d+)\s+(passed|failed|errors?|skipped)", summary):
            key = "errors" if label.startswith("error") else label
            counts[key] += int(n)
        return {"status": "pass" if proc.returncode == 0 else "fail", "returncode": proc.returncode,
                "summary": summary, **counts}


def assert_pristine(task_dir: Path) -> None:
    """Defense in depth: after building context, confirm the task copy itself
    picked up nothing -- untracked or ignored files, or working-tree changes.
    Raises TaskCopyMutated (never silently continues) if it did."""
    out = subprocess.run(["git", "-C", str(task_dir), "status", "--porcelain", "--ignored"],
                          check=True, capture_output=True, text=True).stdout
    if out.strip():
        stray = [line[3:] if len(line) > 3 else line for line in out.strip().splitlines()]
        raise TaskCopyMutated(
            f"task copy {task_dir} is no longer pristine after building context "
            f"(nothing may run inside a task copy before the agent starts): {', '.join(stray)}")


def build_context(task_dir: Path) -> dict:
    tracked = subprocess.run(["git", "-C", str(task_dir), "ls-files"],
                              check=True, capture_output=True, text=True).stdout.splitlines()
    tracked = [t for t in tracked if t.strip()]
    py_lines = 0
    for rel in tracked:
        if not rel.endswith(".py"):
            continue
        try:
            with open(task_dir / rel, encoding="utf-8") as f:
                py_lines += sum(1 for _ in f)
        except OSError:
            pass
    tests = run_pytest(task_dir)
    assert_pristine(task_dir)
    return {
        "tracked_files": len(tracked),
        "python_lines": py_lines,
        "test_command": "pytest",
        "tests": tests,
    }


def context_to_text(ctx: dict) -> str:
    t = ctx["tests"]
    if t["status"] in ("timeout", "error"):
        test_desc = t["summary"]
    else:
        test_desc = (f"{'PASS' if t['status'] == 'pass' else 'FAIL'} "
                     f"({t['passed']} passed, {t['failed']} failed, {t['errors']} errors)")
    return (f"Fixture snapshot: {ctx['tracked_files']} tracked files, {ctx['python_lines']} total "
            f"Python lines. Test command: `{ctx['test_command']}`. Tests currently: {test_desc}.")


# ----------------------------------------------------------------------------
# route.py subprocess + output parsing
# ----------------------------------------------------------------------------
def run_route_score(repo_root: Path, brief_text: str, context_str: str, complexity_log: Path,
                     after_probe: bool, timeout: int = ROUTE_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    route_script = repo_root / "skills" / "complexity" / "scripts" / "route.py"
    cmd = [sys.executable, str(route_script), "score",
           "--task", brief_text, "--context", context_str, "--route", "--jev", "--json"]
    if after_probe:
        cmd.append("--after-probe")
    env = dict(os.environ)
    env["COMPLEXITY_LOG"] = str(complexity_log)
    return subprocess.run(cmd, cwd=str(repo_root), env=env, capture_output=True, text=True, timeout=timeout)


def parse_route_output(stdout: str) -> dict:
    """Reads route.py's `score --route --json` stdout. Accepts two shapes:

      - the current single JSON document (route.py's "skill publish-hardening"
        sprint, C6): the whole of stdout is one object with `scores_line`, `raw`
        (null for the heuristic scorer), `decision`, and `one_liner` among its
        keys;
      - the shape route.py printed before that fix: a human 'scores (...)' line,
        then a bare JSON block (the Jev `raw` answers), then the one-liner, mixed
        on stdout -- kept working here because fixtures recorded from real runs
        before the fix (see FAKE_ROUTE_STDOUT in the selftest) still use it.

    Either way, returns the same four keys: scores_line, raw, one_liner,
    decision_id. Raises ValueError with a clear reason if the input matches
    neither shape or is missing something both require."""
    stripped = stdout.strip()
    if stripped.startswith("{"):
        try:
            doc = json.loads(stripped)
        except json.JSONDecodeError:
            doc = None
        if isinstance(doc, dict) and "scores_line" in doc:
            return _parse_route_output_document(doc)
    return _parse_route_output_mixed(stdout)


def _parse_route_output_document(doc: dict) -> dict:
    one_liner = doc.get("one_liner")
    if one_liner is None:
        raise ValueError("route.py --json output has no 'one_liner' (was --route passed?)")
    m = re.match(r"\[(c-[0-9a-fA-F]+)", one_liner)
    return {"scores_line": doc["scores_line"], "raw": doc.get("raw"), "one_liner": one_liner,
            "decision_id": m.group(1) if m else None}


def _parse_route_output_mixed(stdout: str) -> dict:
    """The pre-C6 shape: a human 'scores (...)' line, a bare JSON block, and a
    trailing one-liner, mixed on stdout. Raises ValueError with a clear reason if
    any of the three is missing or malformed."""
    lines = stdout.splitlines()

    scores_line = next((l for l in lines if l.startswith("scores (")), None)
    if scores_line is None:
        raise ValueError("route.py output is missing the 'scores (...)' line")

    start = next((i for i, l in enumerate(lines) if l.strip() == "{"), None)
    if start is None:
        raise ValueError("route.py output is missing the --json block")
    raw, end = None, None
    for j in range(start, len(lines)):
        try:
            raw = json.loads("\n".join(lines[start:j + 1]))
            end = j
            break
        except json.JSONDecodeError:
            continue
    if raw is None:
        raise ValueError("route.py output's --json block did not parse as JSON")

    one_liner = next((l.strip() for l in lines[end + 1:] if l.strip().startswith("[")), None)
    if one_liner is None:
        raise ValueError("route.py output is missing the one-liner")
    m = re.match(r"\[(c-[0-9a-fA-F]+)", one_liner)

    return {"scores_line": scores_line.strip(), "raw": raw, "one_liner": one_liner,
            "decision_id": m.group(1) if m else None}


def require_jev_raw(raw: dict | None) -> dict:
    """`parsed["raw"]` from parse_route_output, but required to be present: this
    script always calls route.py with --jev (see run_route_score), so a null
    `raw` means route.py fell back to the heuristic scorer after all (the Jev
    call failed, or the score didn't come with --route), and there are no Jev
    answers to record.

    F12 (PLAYBOOK.md "skill publish-hardening", Task F): without this check,
    `raw[d]["score"]` a few lines down in do_route raised a bare
    "TypeError: 'NoneType' object is not subscriptable" -- correct in that the
    run does fail, but opaque about why. Raises a clear ValueError instead,
    which do_route catches the same way it already catches a parse_route_output
    failure (record the error, print it, exit 1)."""
    if raw is None:
        raise ValueError("route.py returned no Jev scores ('raw' is null); route_arm_a requires --jev")
    return raw


# ----------------------------------------------------------------------------
# Decision log
# ----------------------------------------------------------------------------
def append_decision(path: Path, record: dict) -> None:
    data = []
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, list):
                data = existing
        except json.JSONDecodeError:
            pass
    data.append(record)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run", required=True)
    p.add_argument("--task", required=True, type=int, choices=(1, 2, 3, 4))
    p.add_argument("--brief", required=True, help="path to the brief file; its text is sent verbatim")
    p.add_argument("--runs-root", help=f"default: {DEFAULT_RUNS_ROOT}")
    p.add_argument("--after-probe", action="store_true",
                    help="this is the re-route after a probe: appends --probe-output's text "
                         "to the context and passes --after-probe through to route.py")
    p.add_argument("--probe-output", help="path to the probe's output text (required with --after-probe)")
    return p


def do_route(args: argparse.Namespace) -> int:
    runs_root = Path(args.runs_root).expanduser() if args.runs_root else DEFAULT_RUNS_ROOT
    run_dir = runs_root / args.run
    task_dir = run_dir / "A" / f"task{args.task}"
    if not task_dir.is_dir():
        print(f"route_arm_a: {task_dir} does not exist (run make_run.py first)", file=sys.stderr)
        return 1

    decisions_path = run_dir / "decisions.json"
    ts = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    record = {
        "task": args.task,
        "arm": "A",
        "ts": ts,
        "call_type": "after_probe" if args.after_probe else "initial",
    }

    brief_path = Path(args.brief)
    record["brief_path"] = str(brief_path)
    try:
        brief_text = brief_path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"route_arm_a: could not read --brief {brief_path}: {e}", file=sys.stderr)
        return 1

    try:
        context_str = context_to_text(build_context(task_dir))
    except TaskCopyMutated as e:
        record["error"] = str(e)
        append_decision(decisions_path, record)
        print(str(e), file=sys.stderr)
        return 1

    if args.after_probe:
        try:
            probe_text = Path(args.probe_output).read_text(encoding="utf-8")
        except OSError as e:
            print(f"route_arm_a: could not read --probe-output {args.probe_output}: {e}", file=sys.stderr)
            return 1
        context_str += "\n\nProbe output:\n" + probe_text.strip()
    record["context"] = context_str

    repo_root = find_repo_root()
    complexity_log = run_dir / "decisions.jsonl"

    try:
        proc = run_route_score(repo_root, brief_text, context_str, complexity_log, args.after_probe)
    except subprocess.TimeoutExpired:
        record["error"] = f"route.py did not finish within {ROUTE_TIMEOUT_SECONDS}s"
        append_decision(decisions_path, record)
        print(record["error"], file=sys.stderr)
        return 1

    if proc.returncode != 0:
        msg = (proc.stderr or "").strip() or f"route.py exited {proc.returncode}"
        record["error"] = msg
        append_decision(decisions_path, record)
        print(msg, file=sys.stderr)
        return 1

    try:
        parsed = parse_route_output(proc.stdout)
        raw = require_jev_raw(parsed["raw"])
    except ValueError as e:
        record["error"] = f"could not parse route.py output: {e}"
        append_decision(decisions_path, record)
        print(record["error"], file=sys.stderr)
        return 1

    record.update({
        "scores": {d: raw[d]["score"] for d in DIMS if d in raw},
        "confidences": {d: raw[d].get("confidence") for d in DIMS if d in raw},
        "kind": raw.get("kind", {}).get("choice"),
        "kind_confidence": raw.get("kind", {}).get("confidence"),
        "usage": raw.get("usage"),
        "decision_id": parsed["decision_id"],
        "scores_line": parsed["scores_line"],
        "one_liner": parsed["one_liner"],
    })
    append_decision(decisions_path, record)
    print(parsed["one_liner"])
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.after_probe and not args.probe_output:
        parser.error("--after-probe requires --probe-output")
    return do_route(args)


if __name__ == "__main__":
    sys.exit(main())
