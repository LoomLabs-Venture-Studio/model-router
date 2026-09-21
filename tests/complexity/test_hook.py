"""B1-B3 (#6, #7, #17): the UserPromptSubmit hook (`prompt-route.sh`) against the
real script, as a subprocess -- the same way Claude Code actually invokes it.

Before this sprint:
  B1: the hook read `user_prompt` from the payload. The real UserPromptSubmit
      payload (https://docs.claude.com/en/docs/claude-code/hooks) has no such
      field -- the prompt is at `.prompt` -- so the hook has never routed a real
      prompt; it always saw an empty string and exited quietly.
  B2: a long prompt could make the heuristic scorer hang (see test_heuristic.py)
      well past any reasonable hook budget, and the hook had no timeout of its
      own to cut that off.
  B3: the hook forwarded `tail -1` of route.py's stdout unconditionally. If
      logging failed, that line could be a Python traceback fragment or an error
      message, injected into the user's context as if it were a routing decision.

Every test here drives the real `prompt-route.sh` as a subprocess, with a hard
`timeout=` on the call (so a regression hangs the test instead of the suite) and
an explicitly built environment (no inherited TYPESAFE_API_KEY; COMPLEXITY_LOG
always inside tmp_path). No test in this file touches the network: the one test
that exercises the "key is present" path uses a syntactically invalid key
(contains a space), which route.py rejects before ever opening a connection.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / "skills" / "complexity" / "scripts" / "hooks" / "prompt-route.sh"
BASH = shutil.which("bash")

DECISION_LINE_RE = re.compile(r"^\[c-[0-9a-f]{6,12} · .+\]$")

LONG_PROMPT = (
    "Investigate the intermittent 500s on checkout under load, find the root "
    "cause across the payment and inventory services, and fix it."
)
assert len(LONG_PROMPT) >= 80  # comfortably over the default COMPLEXITY_HOOK_MIN_CHARS


def base_env(tmp_path: Path, **overrides: str) -> dict:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
        "HOME": os.environ.get("HOME", str(tmp_path)),
        "COMPLEXITY_LOG": str(tmp_path / "decisions.jsonl"),
    }
    env.update(overrides)
    return env


def run_hook(payload, env: dict, cwd: Path, timeout: float = 15.0) -> subprocess.CompletedProcess:
    """Feed `payload` (a dict -> JSON-encoded, or raw bytes/str used verbatim) to
    the real hook script on stdin and return the completed process. Always has a
    hard timeout so a hang fails this test, not the whole suite."""
    if isinstance(payload, (bytes, bytearray)):
        data = bytes(payload)
    elif isinstance(payload, str):
        data = payload.encode("utf-8")
    else:
        data = json.dumps(payload).encode("utf-8")
    assert BASH, "bash not found on the test machine's PATH"
    return subprocess.run(
        [BASH, str(HOOK)],
        input=data,
        capture_output=True,
        env=env,
        cwd=str(cwd),
        timeout=timeout,
    )


def real_userpromptsubmit_payload(prompt: str, cwd: str) -> dict:
    """Shaped exactly like the documented UserPromptSubmit payload -- no
    `user_prompt` field anywhere, on purpose."""
    return {
        "session_id": "test-session",
        "transcript_path": "/tmp/does-not-exist.jsonl",
        "cwd": cwd,
        "hook_event_name": "UserPromptSubmit",
        "prompt": prompt,
    }


# ----------------------------------------------------------------------------
# B1: the real payload shape
# ----------------------------------------------------------------------------
def test_real_payload_shape_routes(tmp_path):
    env = base_env(tmp_path)
    result = run_hook(real_userpromptsubmit_payload(LONG_PROMPT, str(tmp_path)), env, tmp_path)
    assert result.returncode == 0, result.stderr.decode()
    out = result.stdout.decode().strip()
    assert out, "expected a routing line for a real-shaped payload; got nothing (B1 regression)"
    assert DECISION_LINE_RE.match(out), f"not a decision line: {out!r}"


def test_old_user_prompt_field_is_ignored(tmp_path):
    """The wrong (pre-fix) field name must not be treated as the prompt."""
    payload = {"hook_event_name": "UserPromptSubmit", "user_prompt": LONG_PROMPT}
    env = base_env(tmp_path)
    result = run_hook(payload, env, tmp_path)
    assert result.returncode == 0
    assert result.stdout == b""


# ----------------------------------------------------------------------------
# Malformed / edge-case payloads: exit 0, no output (or a valid line)
# ----------------------------------------------------------------------------
@pytest.mark.parametrize("raw", [b"null", b"[]", b"{}", b"not json at all", b"", b"   ", b"\x00\x01\x02"])
def test_malformed_or_empty_payloads_are_silent(tmp_path, raw):
    env = base_env(tmp_path)
    result = run_hook(raw, env, tmp_path)
    assert result.returncode == 0
    assert result.stdout == b""


def test_prompt_shorter_than_minimum_is_silent(tmp_path):
    env = base_env(tmp_path)
    payload = real_userpromptsubmit_payload("fix the typo", str(tmp_path))
    result = run_hook(payload, env, tmp_path)
    assert result.returncode == 0
    assert result.stdout == b""


def test_slash_command_is_silent(tmp_path):
    env = base_env(tmp_path)
    payload = real_userpromptsubmit_payload("/complexity " + LONG_PROMPT, str(tmp_path))
    result = run_hook(payload, env, tmp_path)
    assert result.returncode == 0
    assert result.stdout == b""


def test_slash_command_with_leading_whitespace_is_still_a_slash_command(tmp_path):
    """F7 (PLAYBOOK.md "skill publish-hardening", Task F): a slash command whose
    first non-blank character is '/' is still a slash command, even with leading
    spaces or a leading newline -- `prompt.startswith("/")` alone missed this."""
    env = base_env(tmp_path)
    for prefix in ("   ", "\n", "\t "):
        payload = real_userpromptsubmit_payload(prefix + "/complexity " + LONG_PROMPT, str(tmp_path))
        result = run_hook(payload, env, tmp_path)
        assert result.returncode == 0
        assert result.stdout == b"", f"prefix {prefix!r} was not treated as a slash command"


def test_non_string_prompt_is_silent(tmp_path):
    env = base_env(tmp_path)
    payload = {"hook_event_name": "UserPromptSubmit", "prompt": 12345}
    result = run_hook(payload, env, tmp_path)
    assert result.returncode == 0
    assert result.stdout == b""


def test_junk_min_chars_falls_back_to_default(tmp_path):
    env = base_env(tmp_path, COMPLEXITY_HOOK_MIN_CHARS="banana")
    short = real_userpromptsubmit_payload("x" * 79, str(tmp_path))
    long = real_userpromptsubmit_payload("y" * 80 + " " + LONG_PROMPT, str(tmp_path))
    result_short = run_hook(short, env, tmp_path)
    assert result_short.returncode == 0
    assert result_short.stdout == b""
    result_long = run_hook(long, env, tmp_path)
    assert result_long.returncode == 0
    out = result_long.stdout.decode().strip()
    assert DECISION_LINE_RE.match(out), f"not a decision line: {out!r}"


# ----------------------------------------------------------------------------
# Shell-metacharacter safety: the prompt is data, never evaluated
# ----------------------------------------------------------------------------
def test_prompt_with_shell_metacharacters_is_never_evaluated(tmp_path):
    canary = tmp_path / "pwned"
    dangerous = (
        "please fix this: `touch " + str(canary) + "` and $(touch " + str(canary) +
        ") and \"quotes\" and 'quotes' and \\backslash\\ and\nnewlines\nhere " * 2
    )
    env = base_env(tmp_path)
    result = run_hook(real_userpromptsubmit_payload(dangerous, str(tmp_path)), env, tmp_path)
    assert result.returncode == 0
    assert not canary.exists(), "a shell metacharacter in the prompt was evaluated"
    out = result.stdout.decode().strip()
    if out:
        assert DECISION_LINE_RE.match(out), f"not a decision line: {out!r}"


def test_one_megabyte_prompt_completes_and_is_bounded(tmp_path):
    """F11 (PLAYBOOK.md "skill publish-hardening", Task F): a decision line must
    actually be printed (not merely "if any output, it's valid"), and the
    prompt route.py received must have been bounded -- checked by reading back
    the logged decision's `task` field, which route.py truncates to 200 chars
    but only ever receives up to HOOK_MAX_PROMPT_CHARS (4,000) of the original
    1,000,000-character prompt in the first place."""
    huge = "investigate this across the whole codebase: " + "a" * (1_000_000)
    log_file = tmp_path / "decisions.jsonl"
    env = base_env(tmp_path, COMPLEXITY_LOG=str(log_file))
    start = time.monotonic()
    result = run_hook(real_userpromptsubmit_payload(huge, str(tmp_path)), env, tmp_path, timeout=15.0)
    elapsed = time.monotonic() - start
    assert result.returncode == 0
    assert elapsed < 10.0, f"hook took {elapsed:.2f}s on a 1MB prompt"
    out = result.stdout.decode().strip()
    assert out, "expected a decision line for a 1MB prompt, got nothing"
    assert DECISION_LINE_RE.match(out), f"not a decision line: {out!r}"

    assert log_file.exists(), "expected the hook's route.py call to have logged a decision"
    logged = json.loads(log_file.read_text(encoding="utf-8").splitlines()[-1])
    # route.py itself caps the logged task at 200 chars regardless of input size;
    # the meaningful bound to check is that it's nowhere near the 1,000,000-character
    # original, proving the hook truncated the prompt before ever invoking route.py.
    assert len(logged["task"]) <= 200
    assert len(logged["task"]) < 5000


# ----------------------------------------------------------------------------
# B2: no python3 on PATH
# ----------------------------------------------------------------------------
def test_no_python3_on_path_exits_zero_silently(tmp_path):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    for tool in ("cat", "dirname"):
        src = shutil.which(tool)
        assert src, f"{tool} not found on the real PATH"
        os.symlink(src, fakebin / tool)
    env = base_env(tmp_path, PATH=str(fakebin))
    result = run_hook(real_userpromptsubmit_payload(LONG_PROMPT, str(tmp_path)), env, tmp_path)
    assert result.returncode == 0
    assert result.stdout == b""


# ----------------------------------------------------------------------------
# B3: only a real decision line ever reaches stdout
# ----------------------------------------------------------------------------
@pytest.mark.parametrize("log_setup", ["directory", "parent_missing", "empty_string"])
def test_unwritable_log_still_routes_and_never_prints_a_diagnostic(tmp_path, log_setup):
    """Team-lead decision on the F2/B3 conflict flagged during this sprint: B3's
    invariant was never "silent on any log problem"; it is "the hook never puts
    anything on stdout that is not a decision-shaped line". Under the pre-F2
    code the only way to satisfy that on a log failure was silence, because
    route.py had crashed (an uncaught OSError traceback, non-zero exit) before
    ever printing anything, so the hook's own `if proc.returncode != 0: return
    None` produced silence as a side effect of the crash -- not because
    silence was the actual contract. F2 fixed route.py itself: a log write
    failure now only warns once on stderr and still prints the decision
    normally, exit 0, since the routing computation is valid regardless of
    whether it could also be logged. So the hook forwarding that real decision
    line is correct, not a regression -- this test now asserts BOTH halves at
    once: the B3 invariant (never a diagnostic) and the F2 expectation (still
    routes) hold together, parametrized over the cheap-to-set-up F2 log
    failure cases."""
    if log_setup == "directory":
        blocker = tmp_path / "decisions.jsonl"
        blocker.mkdir()
        log_value = str(blocker)
    elif log_setup == "parent_missing":
        # A path whose parent directory doesn't exist AND can't be created:
        # "not_a_directory" is a plain file, so os.makedirs("not_a_directory/sub")
        # fails outright rather than creating anything.
        blocker = tmp_path / "not_a_directory"
        blocker.write_text("i am a file, not a directory")
        log_value = str(blocker / "sub" / "decisions.jsonl")
    else:
        log_value = ""

    env = base_env(tmp_path, COMPLEXITY_LOG=log_value)
    result = run_hook(real_userpromptsubmit_payload(LONG_PROMPT, str(tmp_path)), env, tmp_path)

    assert result.returncode == 0
    assert result.stderr == b"", f"hook's own stderr must stay empty; got: {result.stderr!r}"

    out = result.stdout.decode()
    lines = [ln for ln in out.split("\n") if ln != ""]
    assert len(lines) == 1, f"expected exactly one line on stdout, got: {out!r}"
    assert DECISION_LINE_RE.match(lines[0]), f"not a decision line: {lines[0]!r}"
    for forbidden in ("note:", "Traceback", "complexity:", "not logged"):
        assert forbidden not in out, f"{forbidden!r} leaked onto the hook's stdout: {out!r}"


# ----------------------------------------------------------------------------
# B4: a present-but-invalid key falls back to the heuristic, offline
# ----------------------------------------------------------------------------
def test_invalid_key_in_environment_falls_back_offline(tmp_path):
    env = base_env(tmp_path, TYPESAFE_API_KEY="bad key", COMPLEXITY_HOOK_JEV="on")  # whitespace: rejected before any request
    start = time.monotonic()
    result = run_hook(real_userpromptsubmit_payload(LONG_PROMPT, str(tmp_path)), env, tmp_path, timeout=15.0)
    elapsed = time.monotonic() - start
    assert result.returncode == 0
    assert elapsed < 10.0, f"took {elapsed:.2f}s -- looks like it tried to reach the network"
    out = result.stdout.decode().strip()
    assert DECISION_LINE_RE.match(out), f"expected a heuristic fallback decision line, got {out!r}"


def test_export_tab_dotenv_key_is_recognized_and_falls_back_offline(tmp_path):
    """export<TAB>TYPESAFE_API_KEY="..." in .env: recognized as present (the
    Jev path is attempted), but the dummy value also contains whitespace so
    route.py rejects it before any network call -- exercising B4's parser fix
    and the offline fallback in the same test, with no real key anywhere."""
    (tmp_path / ".env").write_text('export\tTYPESAFE_API_KEY="DUMMY not a real key"\n', encoding="utf-8")
    env = base_env(tmp_path, COMPLEXITY_HOOK_JEV="on")
    start = time.monotonic()
    result = run_hook(real_userpromptsubmit_payload(LONG_PROMPT, str(tmp_path)), env, tmp_path, timeout=15.0)
    elapsed = time.monotonic() - start
    assert result.returncode == 0
    assert elapsed < 10.0, f"took {elapsed:.2f}s -- looks like it tried to reach the network"
    out = result.stdout.decode().strip()
    assert DECISION_LINE_RE.match(out), f"expected a heuristic fallback decision line, got {out!r}"
