#!/usr/bin/env bash
# UserPromptSubmit hook: score the prompt out of band and inject one routing line as context.
# Reads the hook payload on stdin. The real UserPromptSubmit payload's field is "prompt" (see
# https://docs.claude.com/en/docs/claude-code/hooks); a missing or empty payload, a non-object
# payload (null, [], garbage, empty stdin), a missing/non-string/empty "prompt", a slash
# command, or a prompt under the length threshold all mean "nothing to route": exit 0, no
# output.
#
# Everything past that belongs to one Python process: JSON parsing, bounding the prompt,
# calling route.py with real per-call timeouts, and validating the printed line before it ever
# reaches the model. Keeping the budget there (rather than bash `cmd | filter || true`) means it
# works on stock macOS, which has no GNU `timeout(1)`, and it sidesteps the exact trap that bit
# this hook once before: `set -o pipefail` silently defeats that fallback idiom unless the
# pipeline is captured inside an `if` test.
#
# Uses Jev only when COMPLEXITY_HOOK_JEV is explicitly set to on/1/true/yes (case-insensitive;
# anything else, including unset, means off -- board decision, PLAYBOOK.md "skill
# publish-hardening": a key sitting in a project's .env for some OTHER purpose must not make
# prompts start going to a third party without the user asking). With the switch off, this hook
# never even checks whether a key is available and never calls route.py with --jev: it always
# scores locally with the heuristic and makes no network call. With the switch on AND a key
# available (the environment, or a non-empty line in .env in the cwd), it takes the Jev path.
# Presence is checked in-process by loading route.py and calling its own get_api_key(), so there
# is exactly one place that parses .env, not a second one in bash that can silently drift out of
# sync with it (which is what happened before: the hook's grep check and route.py's parser
# disagreed on `export<TAB>KEY=...` lines). If the Jev call fails or times out for any reason
# (bad key, network, malformed response), the hook falls back silently to the heuristic scorer.
# On any failure the hook prints nothing and always exits 0: a broken hook must never break the
# prompt it was trying to route. Direct use (`route.py score --jev`) is unaffected by this
# switch; it stays explicit and needs no environment variable.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROUTE="$HERE/../route.py"

payload="$(cat)"

command -v python3 >/dev/null 2>&1 || exit 0

MIN_CHARS_RAW="${COMPLEXITY_HOOK_MIN_CHARS:-80}"

read -r -d '' PYCODE <<'PYEOF' || true
import importlib.util
import json
import os
import re
import subprocess
import sys
import time

ROUTE_PY, MIN_CHARS_RAW = sys.argv[1], sys.argv[2]

# Forwarded to route.py; independent of route.py's own HEURISTIC_TASK_MAX_CHARS bound, and
# small enough that this never approaches any platform's exec/argv limit.
HOOK_MAX_PROMPT_CHARS = 4000
# Short: an unreachable or slow Jev API must not stall the prompt it's trying to route.
HOOK_JEV_TIMEOUT = "3"
# The hook's whole time budget, split across the Jev attempt (if any) and the heuristic fallback.
HOOK_WALL_BUDGET = 8.0
# "[c-<6 to 12 hex> · ... ]" -- Task C's longer ids are still 12 hex or fewer.
DECISION_LINE_RE = re.compile(r"^\[c-[0-9a-f]{6,12} · .+\]$")


def load_route_module():
    spec = importlib.util.spec_from_file_location("complexity_route_hook", ROUTE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    try:
        min_chars = int(MIN_CHARS_RAW)
    except ValueError:
        min_chars = 80

    data = json.load(sys.stdin)
    if not isinstance(data, dict):
        return
    prompt = data.get("prompt")
    if not isinstance(prompt, str) or prompt.lstrip().startswith("/"):
        return
    if len(prompt) < min_chars:
        return
    prompt = prompt[:HOOK_MAX_PROMPT_CHARS]

    deadline = time.monotonic() + HOOK_WALL_BUDGET

    def remaining():
        return max(0.5, deadline - time.monotonic())

    def run(extra_args):
        proc = subprocess.run(
            [sys.executable, ROUTE_PY, "score", "--task", prompt, "--route", *extra_args],
            capture_output=True, text=True, timeout=remaining(),
        )
        if proc.returncode != 0:
            return None
        lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        return lines[-1] if lines else None

    # F13 (board-approved COMPLEXITY_HOOK_JEV): with the switch off, get_api_key() is never
    # even called for this decision -- no key check, no --jev attempt, no network path at all.
    jev_switch = os.environ.get("COMPLEXITY_HOOK_JEV", "").strip().lower() in ("on", "1", "true", "yes")

    out = None
    if jev_switch:
        # Presence check only, never the value itself: never printed, logged, or forwarded anywhere.
        have_key = False
        try:
            have_key = bool(load_route_module().get_api_key())
        except Exception:
            have_key = False
        if have_key:
            try:
                out = run(["--jev", "--jev-timeout", HOOK_JEV_TIMEOUT])
            except Exception:
                out = None  # Jev call failed or timed out: fall through to the heuristic.
    if out is None:
        try:
            out = run([])
        except Exception:
            out = None

    if out and DECISION_LINE_RE.match(out):
        sys.stdout.write(out + "\n")


try:
    main()
except Exception:
    pass  # never let a bug here reach the model as a diagnostic; see the module docstring
PYEOF

printf '%s' "$payload" | python3 -c "$PYCODE" "$ROUTE" "$MIN_CHARS_RAW" 2>/dev/null || true
exit 0
