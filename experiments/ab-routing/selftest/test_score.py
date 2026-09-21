"""selftest for score.py: JUnit result parsing (including the collection-error
placeholder), the pass-bar arithmetic (a normal split, B passing zero tests,
and a collection error folded into the same computation), and the untouched-
copy assertion. score.py --validate is the integration test for the real
hidden-suite/reference-patch behavior; it is not duplicated here -- this file
covers the pure functions with synthetic data only, no pytest subprocesses."""
from __future__ import annotations

import subprocess
from pathlib import Path

import score

JUNIT_NORMAL = """<?xml version="1.0" encoding="utf-8"?>
<testsuites name="pytest tests"><testsuite name="pytest" errors="0" failures="1" skipped="0" tests="2">
<testcase classname="task9.test_things" name="test_a" time="0.01" />
<testcase classname="task9.test_things" name="test_b" time="0.01"><failure message="boom">AssertionError</failure></testcase>
</testsuite></testsuites>
"""

JUNIT_COLLECTION_ERROR = """<?xml version="1.0" encoding="utf-8"?>
<testsuites name="pytest tests"><testsuite name="pytest" errors="1" failures="0" skipped="0" tests="1">
<testcase classname="" name="task9.test_things" time="0.0"><error message="collection failure">ImportError</error></testcase>
</testsuite></testsuites>
"""


def test_read_junit_cases_parses_pass_and_failure(tmp_path):
    junit = tmp_path / "r.xml"
    junit.write_text(JUNIT_NORMAL, encoding="utf-8")
    cases = score.read_junit_cases(junit)
    assert {c["name"]: c["failed"] for c in cases} == {"test_a": False, "test_b": True}


def test_actual_outcomes_maps_classname_to_inventory_style_keys(tmp_path):
    junit = tmp_path / "r.xml"
    junit.write_text(JUNIT_NORMAL, encoding="utf-8")
    outcomes = score.actual_outcomes(junit)
    assert outcomes == {"test_things.py::test_a": False, "test_things.py::test_b": True}


def test_actual_outcomes_ignores_the_collection_failure_placeholder(tmp_path):
    """A whole-module collection failure reports ONE entry with an empty
    classname and the dotted MODULE as the name -- it must not be mistaken for
    a real test result (it matches no inventory key either way)."""
    junit = tmp_path / "r.xml"
    junit.write_text(JUNIT_COLLECTION_ERROR, encoding="utf-8")
    assert score.actual_outcomes(junit) == {}


def test_actual_outcomes_missing_file_is_empty(tmp_path):
    assert score.actual_outcomes(tmp_path / "does_not_exist.xml") == {}


def test_summarize_counts():
    per_test = {
        "a": {"mark": "target", "outcome": "pass"},
        "b": {"mark": "target", "outcome": "fail"},
        "c": {"mark": "guard", "outcome": "pass"},
    }
    assert score.summarize_counts(per_test) == {
        "target": {"passed": 1, "total": 2},
        "guard": {"passed": 1, "total": 1},
    }


def _tests(passing: set, all_keys: set, mark: str = "target") -> dict:
    return {k: {"mark": mark, "outcome": "pass" if k in passing else "fail"} for k in all_keys}


def test_compute_pass_bar_normal_split():
    keys = {"t1", "t2", "t3", "t4"}
    results = {
        ("B", 1): _tests({"t1", "t2", "t3", "t4"}, keys),  # B passes all 4
        ("A", 1): _tests({"t1", "t2", "t3"}, keys),        # A passes 3 of those 4
    }
    bar = score.compute_pass_bar(results)
    assert bar["per_task"]["1"] == {
        "tests_passed_by_B": 4, "tests_passed_by_both": 3, "ratio": 0.75, "meets_bar": False,
    }
    assert bar["overall"]["ratio"] == 0.75
    assert bar["overall"]["meets_bar"] is False  # 0.75 < 0.90


def test_compute_pass_bar_b_passes_zero_tests_is_undefined_not_a_crash():
    keys = {"t1", "t2"}
    results = {
        ("B", 1): _tests(set(), keys),         # B passes nothing
        ("A", 1): _tests({"t1", "t2"}, keys),  # A passes everything (irrelevant: denominator is 0)
    }
    bar = score.compute_pass_bar(results)
    assert bar["per_task"]["1"] == {
        "tests_passed_by_B": 0, "tests_passed_by_both": 0, "ratio": None, "meets_bar": False,
    }
    assert bar["overall"]["ratio"] is None
    assert bar["overall"]["meets_bar"] is False


def test_compute_pass_bar_folds_in_a_collection_error_task():
    """Task 1: a normal split. Task 2: Arm A had a collection error, so
    actual_outcomes would have returned {} for it upstream -- every one of
    task 2's tests already arrives here as "fail" for A. B is fine on task 2."""
    keys1 = {"t1", "t2"}
    keys2 = {"u1", "u2", "u3"}
    results = {
        ("B", 1): _tests({"t1", "t2"}, keys1),
        ("A", 1): _tests({"t1", "t2"}, keys1),
        ("B", 2): _tests({"u1", "u2", "u3"}, keys2),
        ("A", 2): _tests(set(), keys2),  # collection error -> nothing passed
    }
    bar = score.compute_pass_bar(results)
    assert bar["per_task"]["2"] == {
        "tests_passed_by_B": 3, "tests_passed_by_both": 0, "ratio": 0.0, "meets_bar": False,
    }
    # overall: B passed 2 (task1) + 3 (task2) = 5; both passed 2 (task1) + 0 (task2) = 2
    assert bar["overall"]["tests_passed_by_B"] == 5
    assert bar["overall"]["tests_passed_by_both"] == 2
    assert bar["overall"]["ratio"] == 0.4
    assert bar["overall"]["meets_bar"] is False


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True)
    (path / "f.txt").write_text("hello\n", encoding="utf-8")
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.name", "t"],
        ["git", "config", "user.email", "t@t"],
        ["git", "add", "-A"],
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "baseline"],
    ):
        subprocess.run(cmd, cwd=str(path), check=True, capture_output=True, text=True)


def test_snapshot_task_copy_unchanged_between_two_calls(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    assert score.snapshot_task_copy(repo) == score.snapshot_task_copy(repo)


def test_snapshot_task_copy_detects_a_mutation(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    before = score.snapshot_task_copy(repo)
    (repo / "f.txt").write_text("mutated\n", encoding="utf-8")
    after = score.snapshot_task_copy(repo)
    assert before != after


def test_snapshot_task_copy_detects_an_untracked_file(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    before = score.snapshot_task_copy(repo)
    (repo / "new_stray_file.txt").write_text("oops\n", encoding="utf-8")
    after = score.snapshot_task_copy(repo)
    assert before != after
