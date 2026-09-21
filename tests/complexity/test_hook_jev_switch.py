"""F13 (board-approved new environment variable COMPLEXITY_HOOK_JEV,
PLAYBOOK.md "skill publish-hardening", Task F): the prompt hook takes the Jev
path only when COMPLEXITY_HOOK_JEV is explicitly on (on/1/true/yes,
case-insensitive) AND a key is available -- never just because a key happens
to be present, which is what let a key sitting in a project's .env for some
other purpose send prompts to TypeSafe without anyone asking.

Pre-fix failure (reproduced by these tests against the pre-F13 hook): with no
COMPLEXITY_HOOK_JEV at all, a present TYPESAFE_API_KEY still made the hook
call get_api_key() and attempt route.py with --jev on every qualifying
prompt -- there was no way to have a key configured for something else (or
for direct `route.py score --jev` use) without the hook silently using it too.

Since route.py itself is invoked via `sys.executable` (an absolute path) from
inside the hook's own Python code, a PATH-based fake `python3` cannot
intercept that second-hop subprocess call (sys.executable already resolved to
the real interpreter by then). So these tests instead run a COPY of the real
prompt-route.sh from a temp `scripts/hooks/` directory next to a STUB
`scripts/route.py` -- prompt-route.sh resolves ROUTE relative to its own
location (`$HERE/../route.py`), so the copy picks up the stub automatically.
The stub records every argv it's called with (proving whether --jev was ever
passed) and returns a canned decision line, exiting non-zero when --jev is
passed with a key that fails the same ASCII check the real route.py uses (so
the hook's real fallback-to-heuristic code path is genuinely exercised, not
faked).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_HOOK = REPO_ROOT / "skills" / "complexity" / "scripts" / "hooks" / "prompt-route.sh"
BASH = shutil.which("bash")

DECISION_LINE = "[c-000000000000 · S1R1A1K1I1P1V1 · inline]"

STUB_ROUTE_PY = '''#!/usr/bin/env python3
import json, os, sys

def get_api_key():
    v = os.environ.get("TYPESAFE_API_KEY")
    return v.strip() if v else None

if __name__ == "__main__":
    with open(os.environ["STUB_ARGV_LOG"], "a", encoding="utf-8") as f:
        f.write(json.dumps(sys.argv[1:]) + "\\n")
    if "--jev" in sys.argv:
        key = get_api_key()
        if not key or not all(33 <= ord(c) <= 126 for c in key):
            sys.exit("jev: TypeSafe rejected the API key (stub)")
    print("scores (heuristic)  S=1,R=1,A=1,K=1,I=1,P=1,V=1  kind=implement")
    print(''' + repr(DECISION_LINE) + ''')
'''

LONG_PROMPT = (
    "Investigate the intermittent 500s on checkout under load, find the root "
    "cause across the payment and inventory services, and fix it."
)
assert len(LONG_PROMPT) >= 80


def _build_stub_tree(tmp_path: Path) -> tuple[Path, Path]:
    """A temp scripts/hooks/prompt-route.sh (copy of the real hook) next to a
    stub scripts/route.py. Returns (hook_path, argv_log_path)."""
    hooks_dir = tmp_path / "scripts" / "hooks"
    hooks_dir.mkdir(parents=True)
    hook_copy = hooks_dir / "prompt-route.sh"
    hook_copy.write_text(REAL_HOOK.read_text(encoding="utf-8"), encoding="utf-8")
    hook_copy.chmod(0o755)
    (tmp_path / "scripts" / "route.py").write_text(STUB_ROUTE_PY, encoding="utf-8")
    argv_log = tmp_path / "argv_log.jsonl"
    argv_log.write_text("", encoding="utf-8")
    return hook_copy, argv_log


def _run(hook_path: Path, argv_log: Path, cwd: Path, extra_env: dict) -> subprocess.CompletedProcess:
    assert BASH, "bash not found on the test machine's PATH"
    payload = {
        "session_id": "s", "hook_event_name": "UserPromptSubmit",
        "cwd": str(cwd), "prompt": LONG_PROMPT,
    }
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
        "HOME": str(cwd),
        "STUB_ARGV_LOG": str(argv_log),
    }
    env.update(extra_env)
    return subprocess.run(
        [BASH, str(hook_path)], input=json.dumps(payload).encode("utf-8"),
        capture_output=True, env=env, cwd=str(cwd), timeout=15,
    )


def _argv_calls(argv_log: Path) -> list[list[str]]:
    lines = [ln for ln in argv_log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


def test_switch_unset_never_attempts_jev_even_with_a_key_present(tmp_path):
    hook_path, argv_log = _build_stub_tree(tmp_path)
    result = _run(hook_path, argv_log, tmp_path, {"TYPESAFE_API_KEY": "bad key"})
    assert result.returncode == 0, result.stderr
    assert result.stdout.decode().strip() == DECISION_LINE

    calls = _argv_calls(argv_log)
    assert len(calls) == 1, f"expected exactly one route.py call (no --jev retry), got {calls}"
    assert "--jev" not in calls[0], f"Jev was attempted although COMPLEXITY_HOOK_JEV was unset: {calls}"


def test_switch_on_with_invalid_key_attempts_jev_then_falls_back(tmp_path):
    hook_path, argv_log = _build_stub_tree(tmp_path)
    result = _run(hook_path, argv_log, tmp_path,
                   {"TYPESAFE_API_KEY": "bad key", "COMPLEXITY_HOOK_JEV": "on"})
    assert result.returncode == 0, result.stderr
    assert result.stdout.decode().strip() == DECISION_LINE

    calls = _argv_calls(argv_log)
    assert len(calls) == 2, f"expected a --jev attempt then a fallback call, got {calls}"
    assert "--jev" in calls[0], f"expected the first call to attempt Jev: {calls}"
    assert "--jev" not in calls[1], f"expected the fallback call to skip Jev: {calls}"


def test_switch_on_with_no_key_goes_straight_to_heuristic(tmp_path):
    hook_path, argv_log = _build_stub_tree(tmp_path)
    result = _run(hook_path, argv_log, tmp_path, {"COMPLEXITY_HOOK_JEV": "on"})
    assert result.returncode == 0, result.stderr
    assert result.stdout.decode().strip() == DECISION_LINE

    calls = _argv_calls(argv_log)
    assert len(calls) == 1, f"expected exactly one call (no key, so no --jev attempt): {calls}"
    assert "--jev" not in calls[0]


def test_switch_set_to_junk_is_treated_as_off(tmp_path):
    hook_path, argv_log = _build_stub_tree(tmp_path)
    result = _run(hook_path, argv_log, tmp_path,
                   {"TYPESAFE_API_KEY": "bad key", "COMPLEXITY_HOOK_JEV": "banana"})
    assert result.returncode == 0, result.stderr
    assert result.stdout.decode().strip() == DECISION_LINE

    calls = _argv_calls(argv_log)
    assert len(calls) == 1
    assert "--jev" not in calls[0]


def test_switch_accepts_the_documented_case_insensitive_spellings(tmp_path):
    # Distinct subdirectory names (not the spelling itself): "ON" and "on"
    # would collide on a case-insensitive filesystem (default on macOS).
    for i, spelling in enumerate(("ON", "1", "True", "YES", "on")):
        case_dir = tmp_path / f"case{i}"
        hook_path, argv_log = _build_stub_tree(case_dir)
        result = _run(hook_path, argv_log, case_dir,
                       {"TYPESAFE_API_KEY": "bad key", "COMPLEXITY_HOOK_JEV": spelling})
        assert result.returncode == 0, result.stderr
        calls = _argv_calls(argv_log)
        assert calls and "--jev" in calls[0], f"{spelling!r} should have enabled the Jev path: {calls}"
