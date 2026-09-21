"""D1-D4 (#3, #4, #5, #15, #16): the PreToolUse gate (`gate-agent.py`) against the real
script, as a subprocess -- the same way Claude Code actually invokes it.

The board's decision (PLAYBOOK.md, "skill publish-hardening"): the gate is a guard-rail
the user can skip, not a security boundary. Codex findings #3 and #5 (the four skip
paths below) are declared not bugs and are covered here only as documentation of
intended behaviour (`test_skip_path_*`), not as things to close.

Before this sprint:
  D2 (#4): with no decision id in the agent prompt, the gate fell back to the globally
      LAST decision in the shared log if it was recent enough, even when it was logged by
      a different project or session. Reproduced in
      test_no_id_recent_decision_from_another_project_is_denied (which fails on the old
      code: the other project's decision gets used instead of being rejected).
  D3 (#15, plus one CTO finding): malformed stdin, a non-object payload or tool_input, a
      garbage or unreadable log, a bad timestamp, and a non-numeric
      COMPLEXITY_GATE_WINDOW_MIN could each crash the hook with an uncaught exception --
      COMPLEXITY_GATE_WINDOW_MIN especially, since `int(...)` on it ran at *import* time,
      before any of the hook's own error handling could run at all. Also: the id regex
      only ever matched a 6-character id, so a real (12-character) decision id in a
      prompt was silently ignored and the hook fell through to the last-decision
      fallback instead of finding the referenced decision.
  D4 (#16): the fallback did not check whether a decision's timestamp was in the future
      (clock skew, or a corrupted record), so a future-dated decision could satisfy the
      "recent" check forever.

Every test here drives the real `gate-agent.py` as a subprocess, with a hard `timeout=`
on the call and an explicit, minimal environment (COMPLEXITY_LOG always inside tmp_path).
No test in this file touches the network.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = REPO_ROOT / "skills" / "complexity" / "scripts" / "hooks" / "gate-agent.py"
ROUTE_PY = REPO_ROOT / "skills" / "complexity" / "scripts" / "route.py"


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def base_env(tmp_path: Path, **overrides: str) -> dict:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
        "HOME": os.environ.get("HOME", str(tmp_path)),
        "COMPLEXITY_LOG": str(tmp_path / "decisions.jsonl"),
    }
    env.update(overrides)
    return env


def run_gate(payload, env: dict, cwd: Path, timeout: float = 10.0) -> subprocess.CompletedProcess:
    """Feed `payload` (a dict -> JSON-encoded, or raw bytes/str used verbatim) to the
    real gate on stdin and return the completed process. Always has a hard timeout so
    a hang fails this test, not the whole suite."""
    if isinstance(payload, (bytes, bytearray)):
        data = bytes(payload)
    elif isinstance(payload, str):
        data = payload.encode("utf-8")
    else:
        data = json.dumps(payload).encode("utf-8")
    return subprocess.run(
        [sys.executable, str(GATE)],
        input=data,
        capture_output=True,
        env=env,
        cwd=str(cwd),
        timeout=timeout,
    )


def run_route(*args: str, cwd: Path, log_file: Path) -> subprocess.CompletedProcess:
    """Invoke the real route.py so a test can get a genuine, freshly-minted 12-hex id
    with a real `cwd` field, rather than hand-crafting one."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
        "HOME": os.environ.get("HOME", str(cwd)),
        "COMPLEXITY_LOG": str(log_file),
    }
    return subprocess.run(
        [sys.executable, str(ROUTE_PY), *args],
        capture_output=True, text=True, timeout=10, cwd=str(cwd), env=env,
    )


def agent_payload(prompt: str = "", model: str | None = None, subagent_type: str | None = None,
                   cwd: str | None = None, tool_name: str = "Agent") -> dict:
    tool_input: dict = {"prompt": prompt}
    if model is not None:
        tool_input["model"] = model
    if subagent_type is not None:
        tool_input["subagent_type"] = subagent_type
    payload = {
        "session_id": "test-session",
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
    }
    if cwd is not None:
        payload["cwd"] = cwd
    return payload


def make_decision(k: int = 1, cwd: str | None = "__default__", ts: str | None = None,
                   decision_id: str | None = None) -> dict:
    """A decision-record-shaped dict with sensible defaults, matching what
    route.py's decide() produces. `cwd=None` omits the field entirely (an
    older-log record, for D2's "no cwd -> never a fallback candidate" case)."""
    rec = {
        "event": "decision",
        "id": decision_id or ("c-" + uuid.uuid4().hex[:12]),
        "ts": ts or dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "kind": "implement",
        "scores_given": {"S": 1, "R": 1, "A": 1, "K": k, "I": 1, "P": 1, "V": 1},
        "unsure": [],
        "scores_used": {"S": 1, "R": 1, "A": 1, "K": k, "I": 1, "P": 1, "V": 1},
        "difficulty": 3, "tier": 0, "model": "haiku", "model_fallback": None,
        "mode": "inline", "mode_why": "x", "probe": False, "probe_why": [],
        "agent_type": "general-purpose", "shards": None, "shard_concurrency": None,
        "shard_model": None, "reduce": None, "verifier": None, "gate": None,
        "override": None, "after_probe": False, "notes": [], "project": "p", "task": "t",
    }
    if cwd != "__default__":
        if cwd is not None:
            rec["cwd"] = cwd
    else:
        rec["cwd"] = str(Path.cwd())
    return rec


def write_log(log_file: Path, *records: dict) -> None:
    log_file.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def hook_output(result: subprocess.CompletedProcess) -> dict:
    return json.loads(result.stdout)["hookSpecificOutput"]


def assert_silent_allow(result: subprocess.CompletedProcess) -> None:
    assert result.returncode == 0, result.stderr
    assert result.stdout == b"", f"expected silence, got: {result.stdout!r}"


def assert_no_crash(result: subprocess.CompletedProcess) -> None:
    """The D3 invariant: exit 0, empty stdout, no traceback -- regardless of why."""
    assert result.returncode == 0, result.stderr
    assert result.stdout == b"", f"expected silence, got: {result.stdout!r}"
    assert b"Traceback" not in result.stderr, result.stderr


# ----------------------------------------------------------------------------
# Explicit decision id: exact match, 12- and 6-hex, never expires
# ----------------------------------------------------------------------------
def test_valid_twelve_hex_id_is_allowed_silently(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    route_result = run_route("route", "--scores", "S1 R1 A1 K1 I1 P1 V1", "--kind", "implement",
                              "--task", "t", cwd=tmp_path, log_file=log_file)
    assert route_result.returncode == 0, route_result.stderr
    m = re.match(r"^\[(c-[0-9a-f]{12})", route_result.stdout.strip())
    assert m, route_result.stdout
    decision_id = m.group(1)

    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    payload = agent_payload(prompt=f"please continue {decision_id}", cwd="/somewhere/else")
    result = run_gate(payload, env, tmp_path)
    assert_silent_allow(result)


def test_valid_legacy_six_hex_id_is_allowed(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    write_log(log_file, make_decision(k=1, decision_id="c-abc123"))
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    payload = agent_payload(prompt="please continue c-abc123", cwd="/somewhere/else")
    result = run_gate(payload, env, tmp_path)
    assert_silent_allow(result)


def test_explicit_unknown_id_is_denied(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    payload = agent_payload(prompt="please continue c-000000000000", cwd=str(tmp_path))
    result = run_gate(payload, env, tmp_path)
    assert result.returncode == 0, result.stderr
    out = hook_output(result)
    assert out["permissionDecision"] == "deny"
    assert "c-000000000000" in out["permissionDecisionReason"]


def test_twelve_hex_prompt_reference_not_half_matched_as_six_hex_log(tmp_path):
    """A 12-hex id in the prompt must not be truncated to its first 6 characters and
    matched against an unrelated 6-hex logged id (D3, exact-match requirement)."""
    log_file = tmp_path / "decisions.jsonl"
    write_log(log_file, make_decision(k=1, decision_id="c-abcdef"))  # 6 hex, logged
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    payload = agent_payload(prompt="please continue c-abcdef012345", cwd=str(tmp_path))  # 12 hex, different
    result = run_gate(payload, env, tmp_path)
    assert result.returncode == 0, result.stderr
    out = hook_output(result)
    assert out["permissionDecision"] == "deny"
    assert "c-abcdef012345" in out["permissionDecisionReason"]


def test_six_hex_prompt_reference_not_prefix_matched_against_twelve_hex_log(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    write_log(log_file, make_decision(k=1, decision_id="c-abcdef012345"))  # 12 hex, logged
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    payload = agent_payload(prompt="please continue c-abcdef", cwd=str(tmp_path))  # 6 hex, a prefix only
    result = run_gate(payload, env, tmp_path)
    assert result.returncode == 0, result.stderr
    out = hook_output(result)
    assert out["permissionDecision"] == "deny"
    assert "c-abcdef" in out["permissionDecisionReason"]


def test_full_scan_locates_old_id_beyond_tail_window(tmp_path):
    """An id logged long before the bounded 1 MiB tail window must still be found by a
    full scan, not treated as 'not in the log'."""
    log_file = tmp_path / "decisions.jsonl"
    old = make_decision(k=1, decision_id="c-facefaceface")
    old["pad"] = "x" * 2000
    filler = [make_decision(k=1, decision_id=f"c-{i:012d}") for i in range(1000)]
    for r in filler:
        r["pad"] = "y" * 2000
    write_log(log_file, old, *filler)
    assert log_file.stat().st_size > 1_048_576, "test setup didn't exceed the tail window"

    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    payload = agent_payload(prompt="please continue c-facefaceface", cwd="/anywhere")
    result = run_gate(payload, env, tmp_path)
    assert_silent_allow(result)


def test_explicit_id_never_expires_even_though_old(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    write_log(log_file, make_decision(k=1, decision_id="c-abc123abc123", ts="2020-01-01T00:00:00+00:00"))
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    payload = agent_payload(prompt="please continue c-abc123abc123", cwd="/somewhere/else")
    result = run_gate(payload, env, tmp_path)
    assert_silent_allow(result)


# ----------------------------------------------------------------------------
# D2: no id -> fallback bound to the same project
# ----------------------------------------------------------------------------
def test_no_id_recent_same_project_decision_is_allowed(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    proj = tmp_path / "proj"
    proj.mkdir()
    write_log(log_file, make_decision(k=1, cwd=str(proj)))
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    payload = agent_payload(prompt="continue the work", cwd=str(proj))
    result = run_gate(payload, env, tmp_path)
    assert_silent_allow(result)


def test_no_id_recent_decision_from_another_project_is_denied(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    proj_a = tmp_path / "project-a"
    proj_b = tmp_path / "project-b"
    proj_a.mkdir()
    proj_b.mkdir()
    write_log(log_file, make_decision(k=1, cwd=str(proj_b)))
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    payload = agent_payload(prompt="continue the work", cwd=str(proj_a))
    result = run_gate(payload, env, tmp_path)
    assert result.returncode == 0, result.stderr
    assert hook_output(result)["permissionDecision"] == "deny"


def test_no_id_record_without_cwd_is_denied(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    proj = tmp_path / "proj"
    proj.mkdir()
    write_log(log_file, make_decision(k=1, cwd=None))
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    payload = agent_payload(prompt="continue the work", cwd=str(proj))
    result = run_gate(payload, env, tmp_path)
    assert result.returncode == 0, result.stderr
    assert hook_output(result)["permissionDecision"] == "deny"


def test_no_id_ancestor_decision_matches_descendant_hook_cwd(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    proj = tmp_path / "proj"
    sub = proj / "sub"
    sub.mkdir(parents=True)
    write_log(log_file, make_decision(k=1, cwd=str(proj)))
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    payload = agent_payload(prompt="continue the work", cwd=str(sub))
    result = run_gate(payload, env, tmp_path)
    assert_silent_allow(result)


def test_no_id_descendant_decision_matches_ancestor_hook_cwd(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    proj = tmp_path / "proj"
    sub = proj / "sub"
    sub.mkdir(parents=True)
    write_log(log_file, make_decision(k=1, cwd=str(sub)))
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    payload = agent_payload(prompt="continue the work", cwd=str(proj))
    result = run_gate(payload, env, tmp_path)
    assert_silent_allow(result)


def test_no_id_falls_back_to_process_cwd_when_payload_has_none(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    proj = tmp_path / "proj"
    proj.mkdir()
    write_log(log_file, make_decision(k=1, cwd=str(proj)))
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    payload = agent_payload(prompt="continue the work")  # no "cwd" key at all
    result = run_gate(payload, env, proj)  # the gate process itself runs from `proj`
    assert_silent_allow(result)


# ----------------------------------------------------------------------------
# D4: future-dated decisions are ignored (2-minute clock-skew allowance); the window
# ----------------------------------------------------------------------------
def test_no_id_future_dated_decision_is_denied(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    proj = tmp_path / "proj"
    proj.mkdir()
    future_ts = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10)).isoformat(timespec="seconds")
    write_log(log_file, make_decision(k=1, cwd=str(proj), ts=future_ts))
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    payload = agent_payload(prompt="continue the work", cwd=str(proj))
    result = run_gate(payload, env, tmp_path)
    assert result.returncode == 0, result.stderr
    assert hook_output(result)["permissionDecision"] == "deny"


def test_no_id_decision_within_clock_skew_is_allowed(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    proj = tmp_path / "proj"
    proj.mkdir()
    skewed_ts = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=1)).isoformat(timespec="seconds")
    write_log(log_file, make_decision(k=1, cwd=str(proj), ts=skewed_ts))
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    payload = agent_payload(prompt="continue the work", cwd=str(proj))
    result = run_gate(payload, env, tmp_path)
    assert_silent_allow(result)


def test_no_id_stale_decision_beyond_window_is_denied(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    proj = tmp_path / "proj"
    proj.mkdir()
    stale_ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=45)).isoformat(timespec="seconds")
    write_log(log_file, make_decision(k=1, cwd=str(proj), ts=stale_ts))
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    payload = agent_payload(prompt="continue the work", cwd=str(proj))
    result = run_gate(payload, env, tmp_path)
    assert result.returncode == 0, result.stderr
    assert hook_output(result)["permissionDecision"] == "deny"


def test_no_id_decision_within_custom_window_is_allowed(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    proj = tmp_path / "proj"
    proj.mkdir()
    ts45 = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=45)).isoformat(timespec="seconds")
    write_log(log_file, make_decision(k=1, cwd=str(proj), ts=ts45))
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file), COMPLEXITY_GATE_WINDOW_MIN="60")
    payload = agent_payload(prompt="continue the work", cwd=str(proj))
    result = run_gate(payload, env, tmp_path)
    assert_silent_allow(result)


# ----------------------------------------------------------------------------
# Floor / ask logic, through the (now project-bound) lookup
# ----------------------------------------------------------------------------
def test_k2_haiku_model_is_denied(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    write_log(log_file, make_decision(k=2, decision_id="c-222222222222"))
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    payload = agent_payload(prompt="c-222222222222 implement the fix", model="haiku",
                             subagent_type="general-purpose", cwd=str(tmp_path))
    result = run_gate(payload, env, tmp_path)
    assert result.returncode == 0, result.stderr
    out = hook_output(result)
    assert out["permissionDecision"] == "deny"
    assert "c-222222222222" in out["permissionDecisionReason"]


def test_k2_haiku_model_rewrite_mode_updates_input(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    write_log(log_file, make_decision(k=2, decision_id="c-222222222223"))
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file), COMPLEXITY_GATE_MODE="rewrite")
    payload = agent_payload(prompt="c-222222222223 implement the fix", model="haiku",
                             subagent_type="general-purpose", cwd=str(tmp_path))
    result = run_gate(payload, env, tmp_path)
    assert result.returncode == 0, result.stderr
    out = hook_output(result)
    assert out["permissionDecision"] == "allow"
    assert out["updatedInput"]["model"] == "sonnet"


def test_k3_general_purpose_is_asked(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    write_log(log_file, make_decision(k=3, decision_id="c-333333333333"))
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    payload = agent_payload(prompt="c-333333333333 implement the change", model="opus",
                             subagent_type="general-purpose", cwd=str(tmp_path))
    result = run_gate(payload, env, tmp_path)
    assert result.returncode == 0, result.stderr
    out = hook_output(result)
    assert out["permissionDecision"] == "ask"
    assert "c-333333333333" in out["permissionDecisionReason"]


# ----------------------------------------------------------------------------
# F4 (PLAYBOOK.md "skill publish-hardening", Task F): K3 is checked before the
# K>=2/haiku floor. Before this fix, in `rewrite` mode the floor check ran
# first and exited via respond("allow", ...) -- ending the process before the
# K3 ask ever ran -- so a K3 decision with a haiku model was silently allowed
# (with the model merely raised to sonnet), skipping the human approval K3
# requires. Reproduced directly in
# test_k3_haiku_rewrite_mode_used_to_allow_now_asks_and_still_raises_model.
# ----------------------------------------------------------------------------
def test_k3_haiku_rewrite_mode_used_to_allow_now_asks_and_still_raises_model(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    write_log(log_file, make_decision(k=3, decision_id="c-333333333334"))
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file), COMPLEXITY_GATE_MODE="rewrite")
    payload = agent_payload(prompt="c-333333333334 implement the fix", model="haiku",
                             subagent_type="general-purpose", cwd=str(tmp_path))
    result = run_gate(payload, env, tmp_path)
    assert result.returncode == 0, result.stderr
    out = hook_output(result)
    assert out["permissionDecision"] == "ask"  # not "allow" -- the pre-fix bug
    assert "c-333333333334" in out["permissionDecisionReason"]
    # the model is still raised, even though the response is "ask" not "allow":
    # a human approving a K3 call should not then also have to catch a haiku model.
    assert out["updatedInput"]["model"] == "sonnet"


def test_k3_haiku_deny_mode_still_denies_with_the_floor_message(tmp_path):
    """deny mode's behavior for this combination is unchanged by the reordering:
    K3 + haiku still denies, with the same floor message as a plain K2 case."""
    log_file = tmp_path / "decisions.jsonl"
    write_log(log_file, make_decision(k=3, decision_id="c-333333333335"))
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    payload = agent_payload(prompt="c-333333333335 implement the fix", model="haiku",
                             subagent_type="general-purpose", cwd=str(tmp_path))
    result = run_gate(payload, env, tmp_path)
    assert result.returncode == 0, result.stderr
    out = hook_output(result)
    assert out["permissionDecision"] == "deny"
    assert "floors agents at sonnet" in out["permissionDecisionReason"]


def test_k3_sonnet_rewrite_mode_asks_without_touching_the_model(tmp_path):
    """K3 + a model already at or above the floor: no floor concern at all, so
    this is the plain K3 ask, unchanged, with no updatedInput."""
    log_file = tmp_path / "decisions.jsonl"
    write_log(log_file, make_decision(k=3, decision_id="c-333333333336"))
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file), COMPLEXITY_GATE_MODE="rewrite")
    payload = agent_payload(prompt="c-333333333336 implement the fix", model="sonnet",
                             subagent_type="general-purpose", cwd=str(tmp_path))
    result = run_gate(payload, env, tmp_path)
    assert result.returncode == 0, result.stderr
    out = hook_output(result)
    assert out["permissionDecision"] == "ask"
    assert "updatedInput" not in out


def test_tool_name_other_than_agent_is_silent(tmp_path):
    env = base_env(tmp_path)
    payload = agent_payload(prompt="run some tests", cwd=str(tmp_path), tool_name="Bash")
    result = run_gate(payload, env, tmp_path)
    assert_silent_allow(result)


# ----------------------------------------------------------------------------
# The four skip paths -- declared not bugs (board decision, PLAYBOOK.md), exercised
# here as documentation of intended behaviour.
# ----------------------------------------------------------------------------
def test_skip_path_gate_off(tmp_path):
    log_file = tmp_path / "decisions.jsonl"  # empty/missing log: would otherwise deny
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file), COMPLEXITY_GATE="off")
    payload = agent_payload(prompt="implement anything at all", model="haiku", cwd=str(tmp_path))
    result = run_gate(payload, env, tmp_path)
    assert_silent_allow(result)


def test_skip_path_read_only_marker_skips_k3_prompt(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    write_log(log_file, make_decision(k=3, decision_id="c-aaaaaaaaaaaa"))
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    payload = agent_payload(prompt="c-aaaaaaaaaaaa independent review of the change",
                             model="opus", subagent_type="general-purpose", cwd=str(tmp_path))
    result = run_gate(payload, env, tmp_path)
    assert_silent_allow(result)


def test_skip_path_sizing_probe_skips_haiku_floor(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    write_log(log_file, make_decision(k=2, decision_id="c-bbbbbbbbbbbb"))
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    payload = agent_payload(prompt="c-bbbbbbbbbbbb size this task", model="haiku",
                             subagent_type="general-purpose", cwd=str(tmp_path))
    result = run_gate(payload, env, tmp_path)
    assert_silent_allow(result)


def test_skip_path_omitted_model_passes_floor(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    write_log(log_file, make_decision(k=2, decision_id="c-cccccccccccc"))
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    payload = agent_payload(prompt="c-cccccccccccc implement the fix",
                             subagent_type="general-purpose", cwd=str(tmp_path))  # no model at all
    assert "model" not in payload["tool_input"]
    result = run_gate(payload, env, tmp_path)
    assert_silent_allow(result)


# ----------------------------------------------------------------------------
# D3: fail open, quietly -- malformed stdin
# ----------------------------------------------------------------------------
@pytest.mark.parametrize("raw", [b"null", b"[]", b"42", b"not json at all", b"", b"   ", b"\x00\x01\x02"])
def test_malformed_stdin_fails_open(tmp_path, raw):
    env = base_env(tmp_path)
    result = run_gate(raw, env, tmp_path)
    assert_no_crash(result)


@pytest.mark.parametrize("bad_tool_input", ["a string", ["a", "list"], 42, True, None])
def test_tool_input_not_an_object_fails_open(tmp_path, bad_tool_input):
    payload = {"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": "Agent",
               "tool_input": bad_tool_input}
    env = base_env(tmp_path)
    result = run_gate(payload, env, tmp_path)
    assert_no_crash(result)


# ----------------------------------------------------------------------------
# D3: fail open, quietly -- the log
# ----------------------------------------------------------------------------
def test_log_with_garbage_lines_and_missing_fields_still_allows(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    good = make_decision(k=1, cwd=str(tmp_path))
    lines = [
        "null", "[]", "42", "garbage not json {",
        json.dumps({"event": "decision"}),                         # missing id, ts, scores_used
        json.dumps({"event": "decision", "id": "c-nofieldsxxxx"}),  # missing ts, scores_used
        json.dumps(good),
    ]
    log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    payload = agent_payload(prompt="please help", cwd=str(tmp_path))
    result = run_gate(payload, env, tmp_path)
    assert_no_crash(result)


def test_unreadable_log_file_fails_open(tmp_path):
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root can read anything; this case can't be exercised as root")
    log_file = tmp_path / "decisions.jsonl"
    write_log(log_file, make_decision(k=1, cwd=str(tmp_path)))
    log_file.chmod(0o000)
    try:
        env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
        payload = agent_payload(prompt="please help", cwd=str(tmp_path))
        result = run_gate(payload, env, tmp_path)
        assert_no_crash(result)
    finally:
        log_file.chmod(0o644)  # so tmp_path teardown can remove it


def test_log_path_is_a_directory_fails_open(tmp_path):
    log_dir = tmp_path / "decisions.jsonl"
    log_dir.mkdir()
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_dir))
    payload = agent_payload(prompt="please help", cwd=str(tmp_path))
    result = run_gate(payload, env, tmp_path)
    assert_no_crash(result)


def test_ts_that_does_not_parse_is_ignored(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    bad = make_decision(k=1, cwd=str(tmp_path), ts="not-a-timestamp")
    good = make_decision(k=1, cwd=str(tmp_path))
    write_log(log_file, bad, good)
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    payload = agent_payload(prompt="please help", cwd=str(tmp_path))
    result = run_gate(payload, env, tmp_path)
    assert_no_crash(result)


def test_ts_without_timezone_is_ignored(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    naive_ts = dt.datetime.now().isoformat()  # no tzinfo
    bad = make_decision(k=1, cwd=str(tmp_path), ts=naive_ts)
    good = make_decision(k=1, cwd=str(tmp_path))
    write_log(log_file, bad, good)
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    payload = agent_payload(prompt="please help", cwd=str(tmp_path))
    result = run_gate(payload, env, tmp_path)
    assert_no_crash(result)


# ----------------------------------------------------------------------------
# D3: fail open, quietly -- bad environment / bad fields
# ----------------------------------------------------------------------------
def test_bad_window_min_env_var_falls_open(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    write_log(log_file, make_decision(k=1, cwd=str(tmp_path)))
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file), COMPLEXITY_GATE_WINDOW_MIN="banana")
    payload = agent_payload(prompt="please help", cwd=str(tmp_path))
    result = run_gate(payload, env, tmp_path)
    assert_no_crash(result)


def test_scores_used_k_not_integer_falls_open(tmp_path):
    rec = make_decision(k=1, cwd=str(tmp_path))
    rec["scores_used"]["K"] = "high"  # not an integer
    log_file = tmp_path / "decisions.jsonl"
    write_log(log_file, rec)
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    payload = agent_payload(prompt="please help", model="haiku", cwd=str(tmp_path))
    result = run_gate(payload, env, tmp_path)
    assert_no_crash(result)


# ----------------------------------------------------------------------------
# F3 (PLAYBOOK.md "skill publish-hardening", Task F): a torn or non-UTF-8 byte
# anywhere in the log used to crash the gate's full scan with UnicodeDecodeError
# (_read_log_full opened the log in text mode with the default strict decoder,
# which "except OSError" does not catch). Fixed: errors="replace", same as the
# already-safe binary-mode tail reader.
# ----------------------------------------------------------------------------
def test_torn_byte_in_log_does_not_crash_the_tail_read(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    good = make_decision(k=1, cwd=str(tmp_path))
    log_file.write_bytes(
        json.dumps(good).encode("utf-8") + b"\n" + b'{"event":"decision","id":"c-bad","t":"\xc3"}\n'
    )
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    payload = agent_payload(prompt="please help", cwd=str(tmp_path))
    result = run_gate(payload, env, tmp_path)
    assert_no_crash(result)


def test_torn_byte_in_log_does_not_crash_the_full_scan_for_an_explicit_id(tmp_path):
    """Forces the full-scan fallback (_read_log_full) by asking for an id that
    isn't in the bounded tail window at all -- the exact path that used to raise
    UnicodeDecodeError before the errors="replace" fix."""
    log_file = tmp_path / "decisions.jsonl"
    log_file.write_bytes(b'{"event":"decision","id":"c-bad","t":"\xc3"}\n')
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    payload = agent_payload(prompt="please continue c-000000000000", cwd=str(tmp_path))
    result = run_gate(payload, env, tmp_path)
    assert result.returncode == 0, result.stderr
    assert b"Traceback" not in result.stderr
    out = hook_output(result)
    assert out["permissionDecision"] == "deny"  # the explicit id genuinely isn't in the log


# ----------------------------------------------------------------------------
# F6 (PLAYBOOK.md "skill publish-hardening", Task F): id matching is
# case-insensitive; a non-finite or non-positive COMPLEXITY_GATE_WINDOW_MIN
# falls back to the default; project matching uses os.path.samefile when both
# paths exist.
# ----------------------------------------------------------------------------
def test_uppercase_id_in_prompt_matches_the_lowercase_logged_id(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    write_log(log_file, make_decision(k=1, decision_id="c-abcdef012345"))
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    payload = agent_payload(prompt="please continue C-ABCDEF012345", cwd="/somewhere/else")
    result = run_gate(payload, env, tmp_path)
    assert_silent_allow(result)


def test_mixed_case_id_in_prompt_matches(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    write_log(log_file, make_decision(k=1, decision_id="c-abcdef012345"))
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    payload = agent_payload(prompt="please continue c-AbCdEf012345", cwd="/somewhere/else")
    result = run_gate(payload, env, tmp_path)
    assert_silent_allow(result)


@pytest.mark.parametrize("window", ["nan", "inf", "-inf", "0", "-5"])
def test_non_positive_or_non_finite_window_min_falls_back_to_default(tmp_path, window):
    """A decision 45 minutes old is outside the (fallback) 30-minute default, so
    it must still be denied for staleness when the configured window is
    nonsense -- proving the fallback kicked in, not merely that nothing crashed
    (test_bad_window_min_env_var_falls_open already covers non-numeric junk;
    this covers values that DO parse as float but must still be rejected)."""
    log_file = tmp_path / "decisions.jsonl"
    proj = tmp_path / "proj"
    proj.mkdir()
    stale_ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=45)).isoformat(timespec="seconds")
    write_log(log_file, make_decision(k=1, cwd=str(proj), ts=stale_ts))
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file), COMPLEXITY_GATE_WINDOW_MIN=window)
    payload = agent_payload(prompt="continue the work", cwd=str(proj))
    result = run_gate(payload, env, tmp_path)
    assert result.returncode == 0, result.stderr
    assert hook_output(result)["permissionDecision"] == "deny"


def test_positive_infinite_window_min_would_otherwise_match_anything_but_falls_back(tmp_path):
    """Without the isfinite guard, COMPLEXITY_GATE_WINDOW_MIN=inf makes
    `age_min > window_min` False for every age, so even a very old decision
    would match. Confirms the fallback closes that specifically."""
    log_file = tmp_path / "decisions.jsonl"
    proj = tmp_path / "proj"
    proj.mkdir()
    very_old_ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=365)).isoformat(timespec="seconds")
    write_log(log_file, make_decision(k=1, cwd=str(proj), ts=very_old_ts))
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file), COMPLEXITY_GATE_WINDOW_MIN="inf")
    payload = agent_payload(prompt="continue the work", cwd=str(proj))
    result = run_gate(payload, env, tmp_path)
    assert result.returncode == 0, result.stderr
    assert hook_output(result)["permissionDecision"] == "deny"


def test_positive_finite_window_min_still_works(tmp_path):
    """Contrast case: a legitimate custom window is not affected by the guard."""
    log_file = tmp_path / "decisions.jsonl"
    proj = tmp_path / "proj"
    proj.mkdir()
    ts45 = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=45)).isoformat(timespec="seconds")
    write_log(log_file, make_decision(k=1, cwd=str(proj), ts=ts45))
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file), COMPLEXITY_GATE_WINDOW_MIN="60")
    payload = agent_payload(prompt="continue the work", cwd=str(proj))
    result = run_gate(payload, env, tmp_path)
    assert_silent_allow(result)


def _load_gate_module():
    """gate-agent.py loaded directly (not as a subprocess) for a pure-function
    unit test of _cwd_related -- the only place in this file that needs to call
    into it rather than drive the script end to end."""
    spec = importlib.util.spec_from_file_location("complexity_gate_unit", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cwd_related_uses_samefile_for_a_case_variant_on_a_case_insensitive_fs(tmp_path):
    """A case-insensitive filesystem (default on macOS) accepts either case for
    an existing path, so the logged cwd and the hook's cwd can differ only in
    case while naming the same directory. os.path.realpath alone does not
    correct case, so without os.path.samefile this would look like two
    different projects. Skips cleanly on a case-sensitive filesystem (most
    Linux CI), where the upper-cased variant of an existing path doesn't exist."""
    proj = tmp_path / "proj"
    proj.mkdir()
    upper = str(proj).upper()
    if not os.path.exists(upper):
        pytest.skip("filesystem is case-sensitive; the upper-cased path doesn't exist")
    gate_mod = _load_gate_module()
    assert gate_mod._cwd_related(str(proj), upper) is True


def test_end_to_end_case_variant_project_path_is_allowed_on_case_insensitive_fs(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    upper = str(proj).upper()
    if not os.path.exists(upper):
        pytest.skip("filesystem is case-sensitive; the upper-cased path doesn't exist")
    log_file = tmp_path / "decisions.jsonl"
    write_log(log_file, make_decision(k=1, cwd=upper))
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    payload = agent_payload(prompt="continue the work", cwd=str(proj))
    result = run_gate(payload, env, tmp_path)
    assert_silent_allow(result)


# ----------------------------------------------------------------------------
# F7 nit lives in prompt-route.sh (test_hook.py); nothing gate-specific to add
# here beyond the id/window/samefile coverage above.
# ----------------------------------------------------------------------------


# ----------------------------------------------------------------------------
# F2 consequence (team-lead follow-up): a decision route.py printed but could
# not log is unknown to the gate -- an Agent call citing its id is denied via
# the existing "not in the log" path, which usefully names the broken log.
# ----------------------------------------------------------------------------
def test_decision_printed_but_not_logged_is_denied_by_the_gate(tmp_path):
    """The log path never comes to exist at all (its parent can't be created,
    since "blocker" is a plain file): route.py still prints the decision (F2)
    but can't append it anywhere. When the gate later reads that same path, a
    log that simply doesn't exist is not LogUnreadable (fail open) -- it is
    read as empty, same as before any decision was ever logged -- so an
    explicit id in the prompt correctly falls through to "not in the log",
    not a silent allow."""
    blocker = tmp_path / "not_a_directory"
    blocker.write_text("i am a file, not a directory")
    log_path = str(blocker / "sub" / "decisions.jsonl")
    route_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
        "HOME": str(tmp_path),
        "COMPLEXITY_LOG": log_path,
    }
    route_result = subprocess.run(
        [sys.executable, str(ROUTE_PY), "route", "--scores", "S1 R1 A1 K1 I1 P1 V1",
         "--kind", "implement", "--task", "t"],
        capture_output=True, text=True, timeout=10, cwd=str(tmp_path), env=route_env,
    )
    assert route_result.returncode == 0, route_result.stderr
    assert "complexity: decision not logged" in route_result.stderr
    m = re.match(r"^\[(c-[0-9a-f]{12})", route_result.stdout.strip())
    assert m, route_result.stdout
    decision_id = m.group(1)
    assert not Path(log_path).exists()  # confirms nothing was ever written

    env = base_env(tmp_path, COMPLEXITY_LOG=log_path)
    payload = agent_payload(prompt=f"please continue {decision_id}", cwd=str(tmp_path))
    result = run_gate(payload, env, tmp_path)
    assert result.returncode == 0, result.stderr
    out = hook_output(result)
    assert out["permissionDecision"] == "deny"
    assert decision_id in out["permissionDecisionReason"]
    assert "not in the log" in out["permissionDecisionReason"]
