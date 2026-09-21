"""selftest for route_arm_a.py: context building against a real (tiny, local)
fixture copy, and stdout parsing / decision recording with route.py's own
subprocess call stubbed out. No live call to api.typesafe.ai happens here --
the only subprocess.run invocation that reaches the network path (the one whose
argv ends in route.py) is intercepted; everything else (git, pytest) runs for
real against throwaway temp directories."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

import make_run
import route_arm_a


def _init_task_repo(task_dir) -> None:
    task_dir.mkdir(parents=True)
    (task_dir / "app").mkdir()
    (task_dir / "app" / "main.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (task_dir / "tests").mkdir()
    (task_dir / "tests" / "test_ok.py").write_text("def test_a():\n    assert True\n", encoding="utf-8")
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.name", "ab-harness"],
        ["git", "config", "user.email", "ab-harness@localhost"],
        ["git", "add", "-A"],
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "baseline"],
    ):
        subprocess.run(cmd, cwd=str(task_dir), check=True, capture_output=True, text=True)


def test_build_context_reports_files_lines_and_pass(tmp_path):
    task_dir = tmp_path / "A" / "task1"
    _init_task_repo(task_dir)

    ctx = route_arm_a.build_context(task_dir)

    assert ctx["tracked_files"] == 2
    assert ctx["python_lines"] == 4  # 2 lines in each of the 2 tracked .py files
    assert ctx["test_command"] == "pytest"
    assert ctx["tests"]["status"] == "pass"
    assert ctx["tests"]["passed"] == 1
    assert ctx["tests"]["failed"] == 0

    text = route_arm_a.context_to_text(ctx)
    assert "2 tracked files" in text
    assert "4 total Python lines" in text
    assert "PASS" in text


def test_build_context_never_mutates_the_task_copy(tmp_path):
    """Regression for F1: app/core/db.py opens shop.db at import time, so running
    pytest directly inside the task copy (rather than an isolated copy of it)
    left shop.db, __pycache__, and .pytest_cache behind -- state Arm B's copy
    never got. This runs the real fixture's pytest (via build_context) against a
    real make_run.py copy and asserts the task copy is provably untouched after."""
    runs_root = tmp_path / "runs"
    assert make_run.main(["--run", "pristine1", "--runs-root", str(runs_root)]) == 0
    task_dir = runs_root / "pristine1" / "A" / "task1"

    route_arm_a.build_context(task_dir)  # real fixture, real (isolated) pytest run

    status = subprocess.run(["git", "-C", str(task_dir), "status", "--porcelain", "--ignored"],
                             check=True, capture_output=True, text=True).stdout
    assert status == ""
    assert not (task_dir / "shop.db").exists()
    assert not any(task_dir.rglob("__pycache__"))
    assert not any(task_dir.rglob(".pytest_cache"))


def test_assert_pristine_raises_on_a_mutated_copy(tmp_path):
    task_dir = tmp_path / "A" / "task1"
    _init_task_repo(task_dir)
    (task_dir / "shop.db").write_bytes(b"not actually sqlite, just a stray file")

    with pytest.raises(route_arm_a.TaskCopyMutated, match="shop.db"):
        route_arm_a.assert_pristine(task_dir)


FAKE_ROUTE_STDOUT = """scores (jev)  S=1,R=2,A=0,K=2,I=3,P=2,V=1  kind=implement
{
  "S": {"score": 1.0, "confidence": 0.92},
  "R": {"score": 2.0, "confidence": 0.81},
  "A": {"score": 0.0, "confidence": 0.95},
  "K": {"score": 2.0, "confidence": 0.9},
  "I": {"score": 3.0, "confidence": 0.88},
  "P": {"score": 2.0, "confidence": 0.7},
  "V": {"score": 1.0, "confidence": 0.6},
  "kind": {"choice": "implement", "confidence": 0.93},
  "usage": {"input_tokens": 512, "output_tokens": 128}
}
[c-a1b2c3 · S1R2A0K2I3P2V1 · fan-out 6xhaiku · verify:sonnet]
"""


def test_parse_route_output_extracts_scores_json_and_id():
    parsed = route_arm_a.parse_route_output(FAKE_ROUTE_STDOUT)
    assert parsed["decision_id"] == "c-a1b2c3"
    assert parsed["raw"]["usage"] == {"input_tokens": 512, "output_tokens": 128}
    assert parsed["raw"]["S"]["score"] == 1.0
    assert parsed["scores_line"].startswith("scores (jev)")
    assert parsed["one_liner"].startswith("[c-a1b2c3")


# ----------------------------------------------------------------------------
# F12 (PLAYBOOK.md "skill publish-hardening", Task F): require_jev_raw. Before
# this fix, a null `raw` (route.py fell back to the heuristic scorer) reached
# `raw[d]["score"]` in do_route and failed with a bare
# "TypeError: 'NoneType' object is not subscriptable" -- correct in effect but
# opaque about why.
# ----------------------------------------------------------------------------
def test_require_jev_raw_passes_a_real_dict_through_unchanged():
    raw = {"S": {"score": 1.0, "confidence": 0.9}}
    assert route_arm_a.require_jev_raw(raw) is raw


def test_require_jev_raw_raises_a_clear_value_error_when_null():
    with pytest.raises(ValueError, match=r"(?i)raw.*null|jev"):
        route_arm_a.require_jev_raw(None)


FAKE_ROUTE_STDOUT_NO_JEV = json.dumps({
    "scorer": "heuristic",
    "scores_line": "scores (heuristic)  S=1,R=1,A=1,K=1,I=1,P=1,V=1  kind=implement",
    "scores": {"S": 1, "R": 1, "A": 1, "K": 1, "I": 1, "P": 1, "V": 1},
    "unsure": [],
    "kind": "implement",
    "raw": None,
    "note": "note: keyword heuristic; your own read of the task and repo should override these.",
    "decision": {"id": "c-abc123abc123"},
    "one_liner": "[c-abc123abc123 · S1R1A1K1I1P1V1 · inline]",
})


def test_do_route_with_null_raw_records_a_clean_error_not_a_crash(tmp_path, monkeypatch):
    """End to end through do_route (not just require_jev_raw in isolation): a
    route.py call that came back with `raw: null` -- e.g. the Jev call failed
    and route.py fell back to the heuristic scorer -- must record a clean error
    and exit 1, never an uncaught TypeError."""
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "r3"
    task_dir = run_dir / "A" / "task3"
    _init_task_repo(task_dir)

    brief_path = tmp_path / "brief3.txt"
    brief_path.write_text("Some task with no Jev scores available.", encoding="utf-8")

    real_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if any(str(c).endswith("route.py") for c in cmd):
            return subprocess.CompletedProcess(cmd, 0, stdout=FAKE_ROUTE_STDOUT_NO_JEV, stderr="")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(route_arm_a.subprocess, "run", fake_run)

    args = route_arm_a.build_arg_parser().parse_args([
        "--run", "r3", "--task", "3", "--brief", str(brief_path), "--runs-root", str(runs_root),
    ])
    rc = route_arm_a.do_route(args)
    assert rc == 1

    decisions = json.loads((run_dir / "decisions.json").read_text(encoding="utf-8"))
    assert len(decisions) == 1
    assert "raw" in decisions[0]["error"] or "jev" in decisions[0]["error"].lower()


def test_do_route_stubbed_records_full_decision(tmp_path, monkeypatch):
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "r1"
    task_dir = run_dir / "A" / "task1"
    _init_task_repo(task_dir)

    brief_path = tmp_path / "brief.txt"
    brief_text = "Add a refund endpoint. Verbatim marker: ZZTOP-42."
    brief_path.write_text(brief_text, encoding="utf-8")

    captured = {}
    real_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if any(str(c).endswith("route.py") for c in cmd):
            captured["cmd"] = cmd
            captured["env"] = kwargs.get("env")
            captured["cwd"] = kwargs.get("cwd")
            return subprocess.CompletedProcess(cmd, 0, stdout=FAKE_ROUTE_STDOUT, stderr="")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(route_arm_a.subprocess, "run", fake_run)

    args = route_arm_a.build_arg_parser().parse_args([
        "--run", "r1", "--task", "1", "--brief", str(brief_path), "--runs-root", str(runs_root),
    ])
    rc = route_arm_a.do_route(args)
    assert rc == 0

    # the brief reached route.py's --task argument byte-for-byte
    assert brief_text in captured["cmd"]
    # the experiment's own log, not the user's real one
    assert captured["env"]["COMPLEXITY_LOG"] == str(run_dir / "decisions.jsonl")
    assert captured["cwd"] == str(route_arm_a.find_repo_root())

    decisions = json.loads((run_dir / "decisions.json").read_text(encoding="utf-8"))
    assert len(decisions) == 1
    rec = decisions[0]
    assert rec["task"] == 1
    assert rec["arm"] == "A"
    assert rec["call_type"] == "initial"
    assert rec["decision_id"] == "c-a1b2c3"
    assert rec["usage"] == {"input_tokens": 512, "output_tokens": 128}
    assert rec["scores"]["S"] == 1.0
    assert rec["confidences"]["V"] == 0.6
    assert rec["kind"] == "implement"
    assert "fan-out 6xhaiku" in rec["one_liner"]


def test_parse_route_output_contract_with_real_route_py_heuristic_json(tmp_path):
    """Contract test (C6, PLAYBOOK.md "skill publish-hardening"): runs the REAL
    route.py -- not a fixture recording -- offline (heuristic scorer, no --jev, no
    network) and feeds its actual stdout straight into parse_route_output. This is
    the test that would have caught the C6 break: FAKE_ROUTE_STDOUT above is a
    fixture of the OLD mixed shape, so it keeps passing even if a real --json
    change breaks the CLI's actual output. cwd is pinned to tmp_path (never the
    repo root) so a local .env can't be picked up; TYPESAFE_API_KEY is stripped
    from the environment for the same reason, though --jev is never passed here
    regardless."""
    route_script = route_arm_a.find_repo_root() / "skills" / "complexity" / "scripts" / "route.py"
    complexity_log = tmp_path / "decisions.jsonl"
    env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
    env["COMPLEXITY_LOG"] = str(complexity_log)
    proc = subprocess.run(
        [sys.executable, str(route_script), "score", "--task", "rename a function",
         "--route", "--json", "--no-log"],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0, proc.stderr

    parsed = route_arm_a.parse_route_output(proc.stdout)
    assert parsed["raw"] is None  # heuristic mode: no Jev raw block
    assert parsed["scores_line"].startswith("scores (heuristic)")
    assert parsed["one_liner"].startswith("[c-")
    assert parsed["decision_id"] and parsed["decision_id"].startswith("c-")
    assert not complexity_log.exists()  # --no-log: nothing written


def test_do_route_records_failure_and_exits_nonzero_on_route_error(tmp_path, monkeypatch):
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "r2"
    task_dir = run_dir / "A" / "task2"
    _init_task_repo(task_dir)

    brief_path = tmp_path / "brief2.txt"
    brief_path.write_text("Refund double-charge edge case.", encoding="utf-8")

    real_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if any(str(c).endswith("route.py") for c in cmd):
            return subprocess.CompletedProcess(cmd, 1, stdout="",
                                                stderr="jev: TypeSafe rejected the API key (401 unauthorized).\n")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(route_arm_a.subprocess, "run", fake_run)

    args = route_arm_a.build_arg_parser().parse_args([
        "--run", "r2", "--task", "2", "--brief", str(brief_path), "--runs-root", str(runs_root),
    ])
    rc = route_arm_a.do_route(args)
    assert rc == 1

    decisions = json.loads((run_dir / "decisions.json").read_text(encoding="utf-8"))
    assert len(decisions) == 1
    assert "401" in decisions[0]["error"]
