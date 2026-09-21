#!/usr/bin/env python3
"""PreToolUse hook for the Agent tool: a guard-rail that catches an unrouted or
under-tiered Agent call by default. It is not a security boundary -- it runs inside the
same trust boundary as the session it is guarding, and it is meant to be skippable (see
"How to skip the gate" in references/placements.md: COMPLEXITY_GATE=off, a read-only
marker in the prompt, a sizing probe, or an Agent call with no `model` field).

Reads the hook payload on stdin. Finds the decision this Agent call runs under: a decision
id (c-xxxxxxxxxxxx, 12 hex characters, or the legacy c-xxxxxx, 6) anywhere in the call's
prompt wins, from any project, and never expires; otherwise the most recent decision in the
log that was made in this same project -- the payload's `cwd`, or an ancestor or descendant
of it -- within WINDOW_MIN minutes, ignoring anything timestamped more than two minutes in
the future (clock skew). A decision logged without a `cwd` (an older log) is never used by
this fallback. Then:

  deny   no decision found (an Agent call with no routing behind it)
  deny   decision has K >= 2 and the call's model is the bottom tier (haiku)
  ask    decision has K == 3 and this looks like an implementing agent: the harness shows the
         human a permission prompt. A human click is the gate; a note in a log is not.
  allow  everything else, silently

Read-only agents (Explore, or a prompt that says read-only / do not edit / size this task /
independent review) are never asked, because they cannot do the irreversible thing.

Fails open, quietly: malformed stdin, a payload or tool_input that isn't the expected
shape, an unreadable or malformed log, a bad timestamp, or a bad
COMPLEXITY_GATE_WINDOW_MIN all exit 0 with no output and no traceback, rather than deny --
"fails open" means that when the gate cannot tell what applies, it does not block. The two
blocks that stay deliberate: an explicit id the prompt names that is not in the log, and an
Agent call with no applicable decision at all.

COMPLEXITY_GATE=off disables it. COMPLEXITY_GATE_MODE=rewrite makes the K>=2/haiku case rewrite
the model to the floor via updatedInput instead of denying -- EXCEPT when the decision is also
K==3 and not read-only: the K3 approval is checked first and always wins over silently rewriting
the model, so rewrite mode there asks (still raising the model in updatedInput) rather than
allowing outright; a human approving is the K3 gate, not a model bump. Documented for hooks in
general but verify updatedInput takes effect on the Agent tool's model field in your build before
relying on it.
"""
import datetime as dt
import json
import math
import os
import re
import sys

MODE = os.environ.get("COMPLEXITY_GATE_MODE", "deny").lower()
BOTTOM_TIER = "haiku"
FLOOR_TIER = "sonnet"
READ_ONLY_MARKERS = ("read-only", "read only", "do not edit", "size this task", "independent review")

# 12 hex characters (current) or the legacy 6 (older logs); longest alternative first so a
# 12-character id is never half-matched as a 6-character one. Case-insensitive (F6): logged
# ids are always lowercase (uuid4().hex), but a user retyping or a terminal auto-capitalizing
# one shouldn't make the gate miss it -- the match is lower-cased before lookup below.
ID_RE = re.compile(r"\bc-(?:[0-9a-f]{12}|[0-9a-f]{6})\b", re.IGNORECASE)

# Duplicated from route.py's own constant and reader (same window size), rather than
# imported: this hook must keep working when copied out of this repo on its own.
LOG_TAIL_BYTES = 1_048_576

CLOCK_SKEW_MIN = 2  # a decision timestamped further than this into the future than "now"
# is not treated as recent by the fallback (D4) -- clock skew or a corrupted record,
# either way not something to route this Agent call under.


class LogUnreadable(Exception):
    """The log path exists but couldn't be opened or read (permission denied, a
    directory, or some other OS-level failure) -- as opposed to simply not existing yet,
    which is the normal state before the first decision is ever logged. `main` catches
    this and fails open (no output) rather than deny an Agent call just because the log
    itself couldn't be inspected."""


def log_path() -> str:
    return os.path.expanduser(os.environ.get("COMPLEXITY_LOG",
                              os.path.join("~", ".claude", "complexity-router", "decisions.jsonl")))


def _parse_log_lines(lines) -> list[dict]:
    """A line that isn't valid JSON, or that parses to something other than a JSON
    object (null, a list, a bare number or string -- all valid JSON, none of them a
    decision record), is skipped rather than raised on. Mirrors route.py's own reader."""
    out = []
    for raw_line in lines:
        line = raw_line.strip() if isinstance(raw_line, str) else raw_line.strip().decode("utf-8", "replace")
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out


def _read_log_tail(path: str, max_bytes: int = LOG_TAIL_BYTES) -> list[dict]:
    """At most the last `max_bytes` of the log, dropping a first line that is very
    likely split in half by the seek -- same approach as route.py's read_log_tail,
    duplicated here rather than imported."""
    if not os.path.exists(path):
        return []
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            offset = max(0, size - max_bytes)
            f.seek(offset)
            data = f.read()
    except OSError as e:
        raise LogUnreadable from e
    lines = data.split(b"\n")
    if offset > 0:
        lines = lines[1:]
    return _parse_log_lines(lines)


def _read_log_full(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    try:
        # errors="replace": a torn or non-UTF-8 byte anywhere in the log must not
        # raise UnicodeDecodeError mid-read (which "except OSError" below would not
        # catch) -- the line it lands in either still parses, with a replacement
        # character in place of the bad byte, or is skipped by _parse_log_lines
        # like any other malformed line, same as the (already binary-mode, already
        # replacement-decoded) tail reader above.
        with open(path, encoding="utf-8", errors="replace") as f:
            return _parse_log_lines(f)
    except OSError as e:
        raise LogUnreadable from e


def _decisions(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("event") == "decision"]


def _parse_ts(value) -> "dt.datetime | None":
    if not isinstance(value, str) or not value:
        return None
    try:
        ts = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if ts.tzinfo is None:
        return None  # naive timestamp: not comparable to an aware "now"; treat as unusable
    return ts


def _window_minutes() -> float:
    """COMPLEXITY_GATE_WINDOW_MIN, or the 30-minute default when it's missing,
    non-numeric, non-finite (nan, inf, -inf), or not a positive number (zero or
    negative would make the fallback either never or always match -- neither is
    "recent")."""
    try:
        value = float(os.environ.get("COMPLEXITY_GATE_WINDOW_MIN", "30"))
    except (TypeError, ValueError):
        return 30.0
    if not math.isfinite(value) or value <= 0:
        return 30.0
    return value


def _cwd_related(a: str, b: str) -> bool:
    """True if `a` and `b` resolve to the same directory, or one is an ancestor of
    the other. Both are resolved with realpath first so a symlink doesn't defeat the
    comparison. When both also exist, exact equality additionally tries
    os.path.samefile (an inode/device comparison, not a string one), so two
    differently-cased paths naming the same directory on a case-insensitive
    filesystem (default on macOS: `/Users/x/Proj` and `/users/x/proj`) still
    match; realpath alone does not correct case, so without this a case
    mismatch could otherwise make an in-project decision look like it came from
    somewhere else. Falls back to the plain string comparison when either path
    doesn't exist (already gone, or a race)."""
    try:
        ra = os.path.realpath(a)
        rb = os.path.realpath(b)
    except (OSError, ValueError, TypeError):
        return False
    if ra == rb:
        return True
    if os.path.exists(ra) and os.path.exists(rb):
        try:
            if os.path.samefile(ra, rb):
                return True
        except OSError:
            pass
    ra_p = ra.rstrip(os.sep) + os.sep
    rb_p = rb.rstrip(os.sep) + os.sep
    return ra_p.startswith(rb_p) or rb_p.startswith(ra_p)


def _find_recent_same_project(rows: list[dict], hook_cwd: str, window_min: float) -> "dict | None":
    """The most recent decision in `rows` that: has an id, was logged from a directory
    related to `hook_cwd` (D2), has a usable timestamp, isn't timestamped in the future
    beyond ordinary clock skew (D4), and falls within `window_min` minutes. A record
    missing `cwd` (an older log) is never a candidate."""
    now = dt.datetime.now(dt.timezone.utc)
    best = None
    for r in rows:
        rid = r.get("id")
        if not isinstance(rid, str) or not rid:
            continue
        cwd = r.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            continue
        if not _cwd_related(cwd, hook_cwd):
            continue
        ts = _parse_ts(r.get("ts"))
        if ts is None:
            continue
        age_min = (now - ts).total_seconds() / 60
        if age_min < -CLOCK_SKEW_MIN:
            continue
        if age_min > window_min:
            continue
        best = r
    return best


def _score_k(d: dict) -> int:
    scores_used = d.get("scores_used")
    if not isinstance(scores_used, dict):
        return 0
    try:
        return int(scores_used.get("K", 0))
    except (TypeError, ValueError):
        return 0


def respond(decision: str, reason: str, updated_input: "dict | None" = None) -> None:
    out = {"hookEventName": "PreToolUse", "permissionDecision": decision, "permissionDecisionReason": reason}
    if updated_input is not None:
        out["updatedInput"] = updated_input
    print(json.dumps({"hookSpecificOutput": out}))
    sys.exit(0)


def main() -> None:
    if os.environ.get("COMPLEXITY_GATE", "on").lower() == "off":
        return
    try:
        payload = json.load(sys.stdin)
    except ValueError:  # json.JSONDecodeError and any stray UnicodeDecodeError
        return
    if not isinstance(payload, dict):
        return
    if payload.get("tool_name") != "Agent":
        return
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return
    model = str(tool_input.get("model") or "").lower()
    prompt = str(tool_input.get("prompt") or "")
    agent_type = str(tool_input.get("subagent_type") or "")

    hook_cwd = payload.get("cwd")
    if not isinstance(hook_cwd, str) or not hook_cwd:
        hook_cwd = os.getcwd()

    path = log_path()
    d = None
    m = ID_RE.search(prompt)
    try:
        if m:
            wanted = m.group(0).lower()  # F6: case-insensitive match; logged ids are lowercase
            hits = [r for r in _decisions(_read_log_tail(path)) if r.get("id") == wanted]
            if not hits:
                hits = [r for r in _decisions(_read_log_full(path)) if r.get("id") == wanted]
            if not hits:
                respond("deny", f"Prompt references decision {wanted} but it is not in the log ({path}).")
            d = hits[-1]
        else:
            rows = _decisions(_read_log_tail(path))
            d = _find_recent_same_project(rows, hook_cwd, _window_minutes())
    except LogUnreadable:
        return  # can't verify anything about the log: fail open, silently

    if d is None:
        respond("deny", "No complexity decision for this Agent call. Score and route the task first "
                        "(skill: complexity), then put the decision id in the agent prompt.")

    k = _score_k(d)
    did = d.get("id", "?")
    read_only = agent_type.lower() == "explore" or any(w in prompt.lower() for w in READ_ONLY_MARKERS)
    # A sizing probe is exempt from the tier floor: it cannot cause the incident the floor guards
    # against. A read-only *review* shard is not exempt: a cheap model missing the gap is the incident.
    is_probe = agent_type.lower() == "explore" or "size this task" in prompt.lower()

    # K3 is checked FIRST (F4, PLAYBOOK.md "skill publish-hardening"): before this fix,
    # the K>=2/haiku floor check below ran first and, in `rewrite` mode, exited via
    # `respond("allow", ...)` -- ending the process before the K3 ask below ever ran, so a
    # K3 decision with a haiku model skipped the human approval entirely. K3 must never be
    # satisfied by silently raising the model; a human confirming it is the gate this risk
    # level asks for, not a model bump.
    if k == 3 and not read_only:
        if model == BOTTOM_TIER and not is_probe:
            if MODE == "rewrite":
                respond("ask", f"Decision {did} is K3 (irreversible) and risk K{k} floors agents "
                               f"at {FLOOR_TIER}; model raised to {FLOOR_TIER}, and this still "
                               f"needs approval. Approve to proceed.",
                        updated_input={**tool_input, "model": FLOOR_TIER})
            respond("deny", f"Decision {did} has risk K{k}; the policy floors agents at {FLOOR_TIER}. "
                            f"Re-issue with model: {FLOOR_TIER} or higher.")
        respond("ask", f"Decision {did} is K3 (irreversible). This agent can change things. Approve to proceed.")

    if k >= 2 and model == BOTTOM_TIER and not is_probe:
        if MODE == "rewrite":
            respond("allow", f"Decision {did} is K{k}: model raised to {FLOOR_TIER} by policy.",
                    updated_input={**tool_input, "model": FLOOR_TIER})
        respond("deny", f"Decision {did} has risk K{k}; the policy floors agents at {FLOOR_TIER}. "
                        f"Re-issue with model: {FLOOR_TIER} or higher.")


if __name__ == "__main__":
    main()
