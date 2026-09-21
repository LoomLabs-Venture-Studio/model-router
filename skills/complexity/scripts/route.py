#!/usr/bin/env python3
"""route.py: turn complexity scores into a routing decision, and keep a log.

Subcommands
  route    --scores "S=2,R=3?,A=2,K=2,I=1,P=0,V=2" --kind implement [--task "..."] [--shards N]
           [--override "..."] [--explain] [--json] [--no-log]
           Prints ONE line by default; the full card goes to the log. --explain prints the card.
  show     [ID|last]   full card for a logged decision, for when the user asks
  score    --task "..." [--context "..."] [--jev] [--route] [--explain] [--kind KIND]
  outcome  --id c-xxxxxxxxxxxx --result ok|retry|escalated|failed [--note "..."]
  stats    [--last N]
  rubric   (print the rubric levels the scorer uses)

Judgment (the seven scores) is the model's job. Policy (this file) is code, so it is
consistent, loggable, and tunable without re-prompting. Thresholds live in CONFIG.
Stdlib only, including the optional --jev scorer, which calls TypeSafe's HTTP API directly
(urllib, no SDK). It needs TYPESAFE_API_KEY, from the environment or a `.env` file in the
current directory (put one line `TYPESAFE_API_KEY=<your key>` in a file named `.env` in the
directory you run from); the environment always wins.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import datetime as _dt
import html
import http.client
import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter, defaultdict

# ----------------------------------------------------------------------------
# CONFIG: the policy. Change numbers here, not in the prose.
# ----------------------------------------------------------------------------
CONFIG = {
    # tier index -> model alias passed to the Agent tool's `model` parameter
    "tiers": ["haiku", "sonnet", "opus", "fable"],
    # if the top alias is not available on the account, use this one instead
    "fallback": {"fable": "opus"},
    # difficulty (R+A+K, +1 if S==3) -> tier index
    "bands": [(0, 2, 0), (3, 5, 1), (6, 7, 2), (8, 9, 3)],
    # test-loop discount: V<=v, K<=k, R<=r  -> one tier down
    "test_loop": {"V": 1, "K": 1, "R": 1},
    # risk floors: K>=2 -> at least tier 1 (sonnet); K==3 -> at least tier 2 (opus)
    "risk_floor": {2: 1, 3: 2},
    # verifier when (K>=2 and V>=2) or V==3; minimum verifier tier by K
    "verifier_min_tier": {"default": 1, "K3": 2},
    # fan-out
    "parallel_cap": 8,
    "shard_floor_when_risky": 1,   # K>=2 -> shards at least sonnet
    "shards_by_P": {1: 3, 2: 6, 3: 8},  # default when --shards is omitted; for P=1 also the max
    # unsure handling
    "round_up": ["R", "A", "K", "V"],
    "jev_unsure_below": 0.6,
    # log
    "log_env": "COMPLEXITY_LOG",
    "log_default": os.path.join("~", ".claude", "complexity-router", "decisions.jsonl"),
}

DIMS = ["S", "R", "A", "K", "I", "P", "V"]
KINDS = {
    "explore": "Find, locate, or understand something. Read-only.",
    "plan": "Design an approach, compare options, produce steps.",
    "implement": "Change code, data, or configuration.",
    "review": "Assess and report on existing work. No edits.",
    "answer": "Respond from knowledge; nothing to delegate.",
}

# The rubric, as the Jev scorer and `rubric` subcommand see it. Levels are ordered 0..3.
RUBRIC = {
    "S": ("How much of the codebase or material remains to be read or touched? Volume, not file "
          "count: material already in the main context counts as zero remaining scope.", [
        "One known location; no search needed",
        "A handful of files in one module, or under roughly 300 lines to read in total",
        "One subsystem, or a search is needed to find the extent (roughly 5 to 20 files, or 300 to 2,000 lines)",
        "Unknown extent, or 20+ files or 2,000+ lines across several subsystems",
    ]),
    "R": ("How deep is the reasoning the task requires?", [
        "Mechanical; a pattern to copy exists (rename, format, boilerplate, config)",
        "Known pattern with local adaptation; a bug with an obvious cause",
        "Several interacting constraints, design choices, or hypothesis-driven debugging",
        "Novel design; subtle concurrency, security, performance, or data consistency; multi-hop causal debugging",
    ]),
    "A": ("How underspecified is the task as written?", [
        "Fully specified; one right answer",
        "Minor gaps with obvious defaults",
        "Several valid readings; needs judgment or a clarifying question",
        "The goal itself is unclear; needs discovery or product judgment",
    ]),
    "K": ("What is the blast radius if the work is wrong and nobody catches it?", [
        "Local and reversible; no side effects (docs, scratch, tests)",
        "Reversible through normal code review",
        "Touches data, auth, payments, migrations, public API contracts, CI/infra, or shared libraries",
        "Irreversible or production-affecting: prod data, deploys, secrets, money, deletion, sending or publishing",
    ]),
    "I": ("Could a capable engineer do this from a one-paragraph written brief, with no conversation history?", [
        "No: needs live back-and-forth or unstated preferences from the conversation",
        "Yes, but the brief needs care: several constraints to transfer",
        "Yes, cleanly; the result is a diff or a short conclusion",
        "Yes, and the work is mostly reading; only the conclusion is needed back",
    ]),
    "P": ("How many independent pieces does the task split into, where no piece needs another's output?", [
        "One sequential thread",
        "Two or three independent pieces",
        "Four to eight independent pieces",
        "Nine or more, or a map over many files or records",
    ]),
    "V": ("How can the result be checked?", [
        "Self-evident on inspection",
        "Tests or a checker exist or are cheap to write",
        "Needs judgment review; not mechanically checkable",
        "No oracle; needs an independent reviewer, adversarial check, or human sign-off",
    ]),
}


# ----------------------------------------------------------------------------
# Parsing
# ----------------------------------------------------------------------------
_USAGE = 'expected e.g. "S=2,R=1,A=0,K=1,I=2,P=0,V=1" or "S2 R1 A0 K1 I2 P0 V1"'


def parse_scores(text: str) -> tuple[dict[str, int], set[str]]:
    """Accepts exactly two syntaxes, and consumes the whole string:
      comma-separated, one 'DIM=LEVEL' or 'DIM=LEVEL?' token per dimension, e.g.
        "S=2,R=3?,A=2,K=2,I=1,P=0,V=2"
      space-separated, one 'DIMLEVEL' or 'DIMLEVEL?' token per dimension (no '='), e.g.
        "S2 R3? A2 K2 I1 P0 V2"
    The two are not mixed: whichever separator is present picks the token shape required.
    Every dimension in DIMS must appear exactly once, with an integer level 0..3. Anything
    else (an out-of-range or non-integer level, a duplicate or unknown dimension, a missing
    dimension, or trailing junk) exits with a one-line message on stderr and a non-zero
    status -- never a traceback.
    """
    raw = text.strip()
    if not raw:
        sys.exit(f"--scores is empty ({_USAGE})")

    if "," in raw:
        syntax = "comma-separated DIM=LEVEL"
        token_re = re.compile(r"^([SRAKIPV])\s*=\s*([0-9]+)(\?)?$")
        tokens = raw.split(",")
    else:
        syntax = "space-separated DIMLEVEL"
        token_re = re.compile(r"^([SRAKIPV])([0-9]+)(\?)?$")
        tokens = raw.split()

    scores: dict[str, int] = {}
    unsure: set[str] = set()
    for tok in tokens:
        piece = tok.strip()
        if not piece:
            sys.exit(f"empty score entry in --scores ({_USAGE})")
        m = token_re.match(piece.upper())
        if not m:
            sys.exit(f"can't parse {piece!r} in --scores as {syntax} ({_USAGE})")
        dim, level_str, q = m.group(1), m.group(2), m.group(3)
        if dim in scores:
            sys.exit(f"duplicate score for {dim} in --scores")
        level = int(level_str)
        if not (0 <= level <= 3):
            sys.exit(f"score for {dim} must be 0..3, got {level_str} in --scores")
        scores[dim] = level
        if q:
            unsure.add(dim)

    missing = [d for d in DIMS if d not in scores]
    if missing:
        sys.exit(f"missing scores for: {', '.join(missing)} ({_USAGE})")
    return scores, unsure


def positive_int(max_value: int = 1000):
    """argparse type= factory for --shards, --last, and --jev-timeout: plain
    ASCII digits only (never Python's underscore-grouping form like "1_0", and
    never a leading +/- sign or surrounding whitespace `int()` would otherwise
    accept), from 1 up to `max_value` inclusive -- or a clean argparse error
    (usage line plus one-line reason, exit status 2), never a traceback and
    never an implausibly large value (999999999999 shards is not a real fan-out
    request)."""
    def _parse(text: str) -> int:
        if not re.fullmatch(r"[0-9]+", text):
            raise argparse.ArgumentTypeError(f"must be a positive integer, got {text!r}")
        value = int(text)
        if not (0 < value <= max_value):
            raise argparse.ArgumentTypeError(
                f"must be a positive integer from 1 to {max_value}, got {text!r}")
        return value
    return _parse


# ----------------------------------------------------------------------------
# Policy
# ----------------------------------------------------------------------------
def tier_for(difficulty: int) -> int:
    for lo, hi, t in CONFIG["bands"]:
        if lo <= difficulty <= hi:
            return t
    return len(CONFIG["tiers"]) - 1


def model_name(tier: int) -> tuple[str, str | None]:
    name = CONFIG["tiers"][max(0, min(tier, len(CONFIG["tiers"]) - 1))]
    return name, CONFIG["fallback"].get(name)


def decide(scores: dict[str, int], unsure: set[str], kind: str, shards: int | None = None,
           override: str | None = None, after_probe: bool = False) -> dict:
    notes: list[str] = []
    s = dict(scores)
    unsure = set(unsure)

    # One probe per task (--after-probe), and kind=answer never delegates, so neither
    # ever queues a probe reason, no matter what else is unsure (first-match inline rule).
    can_probe = kind != "answer" and not after_probe

    # Step 1: unsure handling
    for d in CONFIG["round_up"]:
        if d in unsure and s[d] < 3:
            s[d] += 1
            notes.append(f"{d} rounded up to {s[d]}: unsure")
    probe_reasons: list[str] = []
    if after_probe:
        # One probe per task. Whatever is still unsure after it gets decided, conservatively.
        # `unsure` itself is left alone here (F10, PLAYBOOK.md "skill publish-hardening",
        # Task F): S and P used to be discarded from it at this point, so a still-unsure S
        # or P vanished from the returned "unsure" list, the card, and the one-liner --
        # `S=2?` silently became `S2` even though the difficulty above was computed from
        # the rounded-up `s["S"]`, and the logged `unsure` no longer showed what was still
        # a guess. That discard had no effect on any decision field: every later read of
        # `unsure` for S or P (the probe-reason checks just below, and the probe-mode
        # branch further down) is already gated on `can_probe`, which is False whenever
        # after_probe is True -- so keeping S and P marked here changes nothing about
        # tier, mode, shards, verifier, or gate, only what gets displayed and logged.
        if "S" in unsure and s["S"] < 3:
            s["S"] += 1
            notes.append(f"S rounded up to {s['S']}: still unsure after probe")
        if "P" in unsure:
            notes.append("P taken as given: still unsure after probe")
    if can_probe and "P" in unsure and s["P"] >= 2:
        probe_reasons.append("shard count unsure: count before fanning out")
        notes.append("P unsure: probe counts shards before any fan-out")
    if "I" in unsure:
        s["I"] = 1
        notes.append("I treated as 1: unsure")
    if can_probe and "S" in unsure:
        probe_reasons.append("scope unsure")

    # Step 2: difficulty and tier
    difficulty = s["R"] + s["A"] + s["K"] + (1 if s["S"] == 3 else 0)
    difficulty = min(difficulty, 9)
    tier = tier_for(difficulty)
    tl = CONFIG["test_loop"]
    if s["V"] <= tl["V"] and s["K"] <= tl["K"] and s["R"] <= tl["R"] and tier > 0:
        tier -= 1
        notes.append("test-loop discount: tests exist, low risk, mechanical work")
    for k_level, floor in CONFIG["risk_floor"].items():
        if s["K"] >= k_level and tier < floor:
            tier = floor
            notes.append(f"risk floor: K{s['K']} -> at least {CONFIG['tiers'][floor]}")

    gate = None
    if s["K"] == 3:
        gate = "confirm with the user before any irreversible step"
        if s["V"] == 3:
            gate = "human sign-off on the result before it is used, and confirmation before any irreversible step"

    verifier = None
    if (s["K"] >= 2 and s["V"] >= 2) or s["V"] == 3:
        vt = max(tier, CONFIG["verifier_min_tier"]["default"])
        if s["K"] == 3:
            vt = max(vt, CONFIG["verifier_min_tier"]["K3"])
        verifier = model_name(vt)[0]

    effort = "default"
    if difficulty >= 9:
        effort = "max"
    elif tier >= 2:
        effort = "high"

    # Step 3: mode
    if kind == "answer":
        mode = "inline"
        why = "kind=answer: nothing to delegate"
    elif s["I"] == 0:
        mode = "inline"
        why = "I0: cannot be briefed; keep the deciding here"
        if can_probe and s["S"] >= 2:
            probe_reasons.append("I0 with S>=2: delegate the reading, not the deciding")
    elif can_probe and ("S" in unsure or ("P" in unsure and s["P"] >= 2) or (s["A"] >= 2 and s["S"] >= 2)):
        mode = "probe"
        why = "route again after the probe"
        if "S" not in unsure and not ("P" in unsure and s["P"] >= 2):
            probe_reasons.append("A>=2 with S>=2: measure before choosing a shape")
    elif s["P"] >= 2:
        mode = "fan-out"
        why = f"P{s['P']}: independent shards"
    elif s["P"] == 1:
        mode = "fan-out"
        why = "P1: two or three agents at task tier, one message"
    elif s["S"] >= 2 or s["I"] == 3 or kind == "explore":
        mode = "single"
        why = "keep the reading out of the main context (" + ", ".join(
            r for r, c in [("S>=2", s["S"] >= 2), ("I3", s["I"] == 3), ("kind=explore", kind == "explore")] if c) + ")"
    else:
        mode = "inline"
        why = "small and sequential; main thread already has the context"

    shard_count = None
    shard_concurrency = None
    shard_model = None
    reduce = None
    if mode == "fan-out":
        est = shards if shards is not None else CONFIG["shards_by_P"].get(s["P"], 3)
        if s["P"] == 1:
            p1_cap = CONFIG["shards_by_P"][1]
            if est > p1_cap:
                notes.append(f"P1 caps shards at {p1_cap} (requested {est})")
                est = p1_cap
        # Keep the total; report concurrency (the parallel cap) separately rather than
        # silently discarding shards above it.
        shard_count = est
        shard_concurrency = min(est, CONFIG["parallel_cap"])
        st = tier if s["P"] == 1 else max(0, tier - 1)
        if s["K"] >= 2:
            st = max(st, CONFIG["shard_floor_when_risky"])
        shard_model = model_name(st)[0]
        reduce = "inline if mechanical (concatenate, dedupe); one agent at task tier if it needs judgment"
        if est > CONFIG["parallel_cap"]:
            notes.append(f"{est} shards estimated; run in batches of {CONFIG['parallel_cap']}")

    agent_type = {"explore": "Explore", "plan": "Plan", "implement": "general-purpose",
                  "review": "general-purpose (read-only brief)", "answer": None}[kind]

    model, fallback = model_name(tier)
    return {
        # 12 hex chars (was 6, ~52% collision odds by 5,000 decisions): matched
        # exactly everywhere an id is looked up, never by prefix, so a 6-char id
        # already in someone's log still resolves.
        "id": "c-" + uuid.uuid4().hex[:12],
        "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "kind": kind,
        "scores_given": scores,
        "unsure": sorted(unsure),
        "scores_used": s,
        "difficulty": difficulty,
        "tier": tier,
        "model": model,
        "model_fallback": fallback,
        "effort": effort,
        "mode": mode,
        "mode_why": why,
        "probe": bool(probe_reasons),
        "probe_why": probe_reasons,
        "agent_type": agent_type,
        "shards": shard_count,
        "shard_concurrency": shard_concurrency,
        "shard_model": shard_model,
        "reduce": reduce,
        "verifier": verifier,
        "gate": gate,
        "override": override,
        "after_probe": after_probe,
        "notes": notes,
        "project": os.path.basename(os.getcwd()),
        # Absolute, resolved working directory at decision time. Lets a PreToolUse gate
        # bind its no-id fallback to decisions made in this same project instead of the
        # globally last one in a log shared across projects and sessions. A record from
        # before this fix simply has no `cwd`; readers must treat that as "unknown", never
        # as a match.
        "cwd": os.path.realpath(os.getcwd()),
    }


# ----------------------------------------------------------------------------
# Card
# ----------------------------------------------------------------------------
def card(d: dict, task: str | None, log_path: str | None) -> str:
    sc = " ".join(f"{k}{d['scores_given'][k]}{'?' if k in d['unsure'] else ''}" for k in DIMS)
    lines = []
    head = f"complexity  {sc}  kind={d['kind']}  difficulty {d['difficulty']}/9"
    if d["notes"]:
        head += "  (" + "; ".join(d["notes"]) + ")"
    lines.append(head)

    model = d["model"] + (f" (fallback {d['model_fallback']})" if d["model_fallback"] else "")
    if d["mode"] == "inline":
        route = f"inline · {model}"
        if d["tier"] >= 1:
            route = f"inline · at least {model} (delegate one agent at {d['model']} if this session is below it)"
        if d["probe"]:
            route = "probe first (Explore, haiku, read-only) -> then " + route
    elif d["mode"] == "probe":
        route = f"probe first (Explore, haiku, read-only) -> re-score S/P/R/V -> route again · task tier so far: {model}"
    elif d["mode"] == "single":
        route = f"single agent · {d['agent_type']} · {model} · effort {d['effort']}"
    else:
        route = (f"fan-out · {d['shards']} shards · {d['agent_type']} · shard model {d['shard_model']} · "
                 f"reduce: {d['reduce']} · task tier {model}")
        if d["shards"] > CONFIG["parallel_cap"]:
            concurrency = d.get("shard_concurrency", CONFIG["parallel_cap"])
            route += f" · {concurrency} at a time"
    lines.append(f"route       {route}")
    lines.append(f"why         {d['mode_why']}" + (f"; probe: {', '.join(d['probe_why'])}" if d["probe_why"] else ""))
    if d["verifier"]:
        lines.append(f"verifier    yes · {d['verifier']}, independent (give it the artifact and the task, not the reasoning)")
    else:
        lines.append("verifier    none")
    if d["gate"]:
        lines.append(f"gate        K{d['scores_used']['K']}: {d['gate']}")
    elif d["scores_used"]["K"] == 2:
        lines.append("gate        K2: changes ship through review, no direct deploy")
    if d["override"]:
        lines.append(f"override    {d['override']}  (user override wins; logged)")
    lines.append(f"id          {d['id']}" + (f"   log {log_path}" if log_path else "   (not logged)"))
    if task:
        lines.append(f"task        {task[:120]}")
    return "\n".join(lines)


def one_liner(d: dict) -> str:
    """The only thing that should reach the conversation by default."""
    sc = "".join(f"{k}{d['scores_given'][k]}{'?' if k in d['unsure'] else ''}" for k in DIMS)
    if d["mode"] == "probe":
        shape = "probe->reroute"
    elif d["mode"] == "fan-out":
        shape = f"fan-out {d['shards']}x{d['shard_model']}"
        if d["shards"] > CONFIG["parallel_cap"]:
            concurrency = d.get("shard_concurrency", CONFIG["parallel_cap"])
            shape += f" ({concurrency} at a time)"
    elif d["mode"] == "single":
        shape = f"agent {d['model']}"
    else:
        shape = f"inline>={d['model']}" if d["tier"] >= 1 else "inline"
        if d["probe"]:
            shape = "probe->" + shape
    parts = [d["id"], sc, shape]
    if d["verifier"]:
        parts.append(f"verify:{d['verifier']}")
    if d["gate"]:
        parts.append("gate:confirm" if "sign-off" not in d["gate"] else "gate:sign-off")
    if d["override"]:
        parts.append("override")
    return "[" + " · ".join(parts) + "]"


# ----------------------------------------------------------------------------
# Log
# ----------------------------------------------------------------------------
LOG_TAIL_BYTES = 1_048_576  # 1 MiB: how far back `show`, `outcome`'s id check, and
# `stats --last N` read without loading the whole log. Growth policy and rotation
# are documented in references/placements.md.


def log_path() -> str:
    return os.path.expanduser(os.environ.get(CONFIG["log_env"], CONFIG["log_default"]))


class LogUnreadable(Exception):
    """The log path exists but couldn't be opened or read -- a directory instead
    of a file, permission denied, or some other OS-level failure -- as opposed to
    simply not existing yet, which is the normal, silent state before the first
    decision is ever logged. str(e) is a clean, one-line message, already safe to
    print (see `_wrap_log_error`)."""


def _os_error_reason(e: OSError) -> str:
    """A short, printable reason for an OSError -- no full path (the caller
    already knows or prints it separately), no traceback."""
    return e.strerror or e.__class__.__name__


def _wrap_log_error(e: OSError, path: str) -> LogUnreadable:
    return LogUnreadable(f"can't read the decision log at {path} ({_os_error_reason(e)})")


def _ensure_trailing_newline(path: str) -> None:
    """If the log already exists and its last byte isn't a newline -- an interrupted
    write left a partial final line -- add one before the next append. Without this,
    the next record is glued onto the broken line and a reader loses both: the
    combined line fails json.loads and is skipped whole. A missing file is not an
    error here; append_log creates it fresh right after this returns."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    if size == 0:
        return
    with open(path, "rb+") as f:
        f.seek(-1, os.SEEK_END)
        if f.read(1) != b"\n":
            f.seek(0, os.SEEK_END)
            f.write(b"\n")


def append_log(record: dict) -> str:
    path = log_path()
    dirname = os.path.dirname(path)
    if dirname:  # COMPLEXITY_LOG may be a bare filename (no directory component)
        os.makedirs(dirname, exist_ok=True)
    _ensure_trailing_newline(path)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")  # one write, one whole line
    return path


def _parse_log_lines(lines) -> list[dict]:
    """Shared by read_log and read_log_tail: a line that isn't valid JSON, or that
    parses to something other than a JSON object (null, a list, a bare number or
    string -- all valid JSON, none of them a decision or outcome record), is
    skipped rather than raised or returned as-is for a caller to crash on."""
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


def read_log() -> list[dict]:
    """Raises LogUnreadable (never a bare OSError or UnicodeDecodeError) if the
    path exists but can't be read as a log -- a directory, permission denied, or
    a decode failure on a genuinely non-UTF-8 byte, none of which `errors=
    "replace"` alone can rule out (a directory or permission error happens at
    `open()`, before any decoding). A path that simply doesn't exist yet is not
    an error: that's the normal state before the first decision is ever logged."""
    path = log_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return _parse_log_lines(f)
    except OSError as e:
        raise _wrap_log_error(e, path) from e


def read_log_tail(max_bytes: int = LOG_TAIL_BYTES) -> list[dict]:
    """Like read_log, but reads at most the last `max_bytes` of the file instead of
    the whole thing: `show`, `outcome`'s existence check, and `stats --last N` only
    need recent history, and none of them should be linear in a log that has grown
    over months of use. The first line of the read window is always dropped: unless
    the window starts at byte 0 (a log smaller than max_bytes), it is very likely a
    line cut in half by the seek, and even on the rare exact boundary the cost is
    one whole record dropped from what is, by construction, a much longer log.

    Any other bounded reader of this log (a PreToolUse gate hook, say) should use
    the same approach: seek to max(0, size - max_bytes), read to EOF, drop the first
    line, parse the rest exactly like read_log. Raises LogUnreadable (never a bare
    OSError) if the path exists but can't be read; a path that doesn't exist yet
    returns an empty list, same as read_log."""
    path = log_path()
    if not os.path.exists(path):
        return []
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            offset = max(0, size - max_bytes)
            f.seek(offset)
            data = f.read()
    except OSError as e:
        raise _wrap_log_error(e, path) from e
    lines = data.split(b"\n")
    if offset > 0:
        lines = lines[1:]
    return _parse_log_lines(lines)


# ----------------------------------------------------------------------------
# Scorers
# ----------------------------------------------------------------------------
HEURISTIC_TASK_MAX_CHARS = 20_000  # score at most this many characters of the task text; every
# pattern below is linear in input length, but there is no reason to spend even linear work on
# more of a task description than this, and it bounds the argv route.py itself is invoked with.


def score_heuristic(task: str) -> tuple[dict[str, int], set[str], str, bool]:
    """Keyword heuristic. Deliberately conservative: anything without strong evidence is unsure.
    Exists for hooks and scripts with no model in the loop. A model reading the repo does better.

    Bounded to HEURISTIC_TASK_MAX_CHARS and linear-time in that bound: every pattern here either
    matches literal alternatives (no ambiguity for the engine to backtrack over) or bounds its
    repeat count explicitly, so no pattern can be walked into quadratic-or-worse backtracking by
    adversarial input. Returns whether the task text was truncated to reach the bound."""
    truncated = len(task) > HEURISTIC_TASK_MAX_CHARS
    task = task[:HEURISTIC_TASK_MAX_CHARS]
    t = task.lower()
    scores: dict[str, int] = {}
    unsure: set[str] = set()

    def hit(pat: str) -> bool:
        return re.search(pat, t) is not None

    # K: risk keywords are the most reliable signal in text
    if hit(r"\b(prod(uction)?|deploy|rotate|secret|credential|payment|billing|charge|refund|money|"
           r"delete|drop\b|truncate|force[- ]push|send (an? )?(email|message|sms)|publish|release)"):
        scores["K"] = 3
    elif hit(r"\b(auth|permission|migrat|schema|api contract|public api|ci\b|pipeline|infra|terraform|shared lib)"):
        scores["K"] = 2
    elif hit(r"\b(docs?|readme|comment|typo|scratch|notebook|test only)\b"):
        scores["K"] = 0
    else:
        scores["K"] = 1
        unsure.add("K")

    # S: scope words
    if hit(r"\b(all|every|across|entire|whole|codebase|repo(sitory)?|everywhere)\b"):
        scores["S"] = 3
    elif hit(r"\b(module|package|service|directory|folder|subsystem)\b"):
        scores["S"] = 2
    # Bounded to 200 chars so a long run of path-ish characters (letters, digits, '_', '/', '-'
    # are all in the class, so it does not stop at any of them) can't make the engine backtrack
    # over the whole remaining string at every candidate start position: with the bound, each
    # start position costs at most 200 steps of backtrack, so the whole scan stays O(n).
    elif hit(r"\b[\w/-]{1,200}\.(py|ts|tsx|js|go|rs|java|rb|sql|yaml|yml|json|md)\b") or hit(r"`[^`]+`"):
        scores["S"] = 1
        unsure.add("S")
    else:
        scores["S"] = 2
        unsure.add("S")

    # R: reasoning
    if hit(r"\b(rename|typo|format|lint|bump|version|boilerplate|scaffold|copy|move file)\b"):
        scores["R"] = 0
    elif hit(r"\b(design|architect|race|concurren|deadlock|intermittent|flaky|under load|memory leak|"
             r"security|perf(ormance)?|consisten|multi-?tenan)"):
        scores["R"] = 3
    elif hit(r"\b(debug|bug|why|investigate|root cause|refactor|optimi[sz]e|integrat|cach)"):
        scores["R"] = 2
        unsure.add("R")
    else:
        scores["R"] = 1
        unsure.add("R")

    # A: ambiguity
    if hit(r"\b(somehow|something like|maybe|not sure|figure out|whatever|improve|better|clean ?up|nicer|modern)"):
        scores["A"] = 2
        unsure.add("A")
    elif hit(r"`[^`]+`") and not hit(r"\?"):
        scores["A"] = 0
        unsure.add("A")
    else:
        scores["A"] = 1
        unsure.add("A")

    # I: independence
    if hit(r"\b(as (we )?discussed|like (before|last time)|the usual|you know|what do you think|should we)"):
        scores["I"] = 0
    else:
        scores["I"] = 2
        unsure.add("I")

    # P: parallelism from counts and list markers
    m = re.search(r"\b(\d+)\s+(?:[\w-]+\s+)?(files|handlers|routes|endpoints|services|modules|tests|records|pages|docs|tickets)\b", t)
    items = len(re.findall(r"(?m)^\s*(?:[-*]|\d+[.)])\s+", task))
    n = int(m.group(1)) if m else items
    if n >= 9 or hit(r"\b(each|every)\b.*\b(file|handler|route|endpoint|service|module|record|page|doc|ticket)s?\b"):
        scores["P"] = 3
    elif n >= 4:
        scores["P"] = 2
    elif n >= 2 or t.count(" and ") >= 3:
        scores["P"] = 1
        unsure.add("P")
    else:
        scores["P"] = 0
        unsure.add("P")

    # V: verifiability
    if hit(r"\b(security|correctness|proof|sign[- ]?off|compliance)\b"):
        scores["V"] = 3
    elif hit(r"\b(report|audit|review|assess|recommend|summar)"):
        scores["V"] = 2
    elif hit(r"\b(tests?|spec|ci\b|typecheck|lint)\b"):
        scores["V"] = 1
    elif scores["R"] == 0:
        scores["V"] = 0
        unsure.add("V")
    else:
        scores["V"] = 2
        unsure.add("V")

    # kind
    if hit(r"\b(audit|review|assess|check (all|every))\b"):
        kind = "review"
    elif hit(r"\b(fix|implement|add|change|build|write|update|migrate|refactor|rename|remove)\b"):
        kind = "implement"
    elif hit(r"\b(find|locate|where (is|are)|which (files|modules)|list all|how does|understand)\b"):
        kind = "explore"
    elif hit(r"\b(plan|propose|approach|architecture|options|compare)\b"):
        kind = "plan"
    elif hit(r"\b(what|why|explain)\b") and t.strip().endswith("?"):
        kind = "answer"
    else:
        kind = "implement"
    return scores, unsure, kind, truncated


JEV_API_URL = "https://api.typesafe.ai/v1/systemone"
JEV_TIMEOUT_SECONDS = 20  # default for direct use; the hook path passes a short --jev-timeout


def _read_env_file(path: str = ".env") -> dict[str, str]:
    """Minimal stdlib .env parser. Blank lines and '#' comments are skipped; a leading
    'export ' is tolerated; matching single or double quotes (and surrounding whitespace)
    are stripped from the value. A missing or unreadable file is not an error."""
    out: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        # Any run of spaces or tabs after "export" (a literal single space was the bug: a real
        # shell also accepts "export\tKEY=..." or "export   KEY=...", and route.py's parser
        # silently disagreeing with the hook's presence check meant the Jev path could be
        # configured and never run).
        line = re.sub(r"^export[ \t]+", "", line)
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        out[key] = val
    return out


def get_api_key() -> str | None:
    """TYPESAFE_API_KEY: the environment wins when set and non-empty; otherwise a `.env`
    file in the current directory (put one line `TYPESAFE_API_KEY=<your key>` in a file
    named `.env` in the directory you run from). Never written back to os.environ.
    Stripped on both paths: a value copy-pasted or read from a file often carries a
    trailing newline or stray whitespace that would otherwise break the HTTP header."""
    env_val = os.environ.get("TYPESAFE_API_KEY")
    if env_val:
        return env_val.strip()
    dotenv_val = _read_env_file(".env").get("TYPESAFE_API_KEY")
    return dotenv_val.strip() if dotenv_val else None


def redact_secrets(text: str, key: str | None) -> str:
    """The one redaction pass applied to every piece of free text before it is printed
    or persisted: the configured API key (a literal match, if any key is configured)
    and any 'Bearer <token>' a server or a user might type or echo back. A falsy
    `key` leaves the key-matching step a no-op; the Bearer pattern is always checked.

    Callers that hand this a JSON-decoded value (see `_redact_json_value` below) get
    the key matched against its actual characters no matter how the wire happened to
    escape them (a key containing a quote or backslash is escaped differently inside
    JSON text than in the decoded string) -- matching on raw, still-encoded wire text
    is what let a key leak in its escaped form. Never call this to build the string
    that will be JSON-decoded again: the caller must redact each decoded piece of a
    structured body, not the reassembled text."""
    if key:
        text = text.replace(key, "[redacted]")
    return re.sub(r"(?i)bearer\s+\S+", "Bearer [redacted]", text)


def _redact(text: str, key: str, limit: int) -> str:
    """redact_secrets, then collapse whitespace and truncate. Order matters: redacting
    after truncation would leave an unredacted prefix of the key in the output
    whenever the key straddles the cut point."""
    text = redact_secrets(text, key)
    text = " ".join(text.split())
    return text[:limit] + ("..." if len(text) > limit else "")


def _redact_json_value(value, key: str | None):
    """Recursively apply redact_secrets to every string in a JSON-decoded value
    (dict keys and values, list items), leaving numbers/bools/None untouched. Because
    this runs on values already decoded from JSON, a key needs no special-casing for
    whatever escaping the wire used -- `json.loads` already turned any `\\"` or `\\\\`
    back into the literal character before this ever sees it."""
    if isinstance(value, str):
        return redact_secrets(value, key)
    if isinstance(value, dict):
        return {(redact_secrets(k, key) if isinstance(k, str) else k): _redact_json_value(v, key)
                for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_json_value(v, key) for v in value]
    return value


def _walk_strings(value) -> list[str]:
    """Every string inside a JSON-decoded value -- dict keys and values, list
    items, at any nesting depth. Search-only: used to look for an encoded key,
    never to build redacted output (see `_redact_json_value` for that)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for k, v in value.items():
            if isinstance(k, str):
                out.append(k)
            out.extend(_walk_strings(v))
        return out
    if isinstance(value, list):
        out = []
        for v in value:
            out.extend(_walk_strings(v))
        return out
    return []


_DECODE_CANDIDATE_MAX_CHARS = 50_000  # an HTTP error body worth showing an excerpt of
# is never legitimately this large; skip decode attempts past it so a huge or
# adversarial response can't make the search below slow.

# A run of base64-alphabet characters long enough to plausibly encode a key, with
# optional padding -- used to find a base64 blob EMBEDDED in surrounding plain
# text (a whole-text-only decode attempt would miss "bad credential: <blob>",
# since the prefix makes the whole string invalid base64).
_BASE64_RUN_RE = re.compile(r"[A-Za-z0-9+/]{8,}={0,2}")


def _base64_decode_attempts(text: str) -> list[str]:
    """Every distinct base64 decode this layer is willing to try on `text`: the
    whole string, and any base64-alphabet run found embedded within it. Each is
    decoded with strict validation, so a run that merely looks base64-ish but
    isn't (wrong padding, stray characters once dashes/underscores are excluded)
    is skipped rather than mis-decoded."""
    attempts = {text.strip()}
    attempts.update(m.group(0) for m in _BASE64_RUN_RE.finditer(text))
    out: list[str] = []
    for candidate in attempts:
        if len(candidate) < 8:
            continue
        try:
            padded = candidate + "=" * (-len(candidate) % 4)
            decoded = base64.b64decode(padded.encode("ascii"), validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError, UnicodeEncodeError, binascii.Error):
            continue
        if decoded and decoded != text:
            out.append(decoded)
    return out


def _decode_candidates(text: str) -> list[str]:
    """One layer of decode attempts on `text`: JSON (walking every string it finds,
    at any nesting depth), unicode-escape, percent-encoding (RFC 3986), base64
    (the whole text, and any base64-looking run embedded within it), and HTML
    entities (numeric decimal `&#NNN;`, numeric hex `&#xHH;`, and named entities
    like `&amp;` -- realistically a proxy's or upstream server's HTML error page).
    Only results that actually differ from `text` are returned. Every attempt is
    best-effort: a decoder that doesn't apply (invalid JSON, invalid escapes, not
    valid base64, no entities present) is skipped, not an error -- this is a
    search for a key that might be hiding in one of these forms, not a strict
    decoder."""
    if len(text) > _DECODE_CANDIDATE_MAX_CHARS:
        return []
    out: list[str] = []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if parsed is not None:
        out.extend(s for s in _walk_strings(parsed) if s != text)
    try:
        unescaped = text.encode("utf-8", "backslashreplace").decode("unicode_escape")
        if unescaped != text:
            out.append(unescaped)
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    try:
        unquoted = urllib.parse.unquote(text, errors="strict")
        if unquoted != text:
            out.append(unquoted)
    except (UnicodeDecodeError, ValueError):
        pass
    unescaped_html = html.unescape(text)
    if unescaped_html != text:
        out.append(unescaped_html)
    out.extend(_base64_decode_attempts(text))
    return out


_DECODE_SEARCH_MAX_CANDIDATES = 500  # bounds the fixpoint search below against a
# pathological or adversarial body; an ordinary error body explores far fewer than
# this before either finding the key or running out of new candidates to try.


def _key_reachable_by_decoding(raw_text: str, key: str, max_rounds: int = 5) -> bool:
    """True if `key` can be reached from `raw_text` by decoding it -- JSON-parsing
    and walking every string, unicode-escape decoding, percent-decoding, or
    base64-decoding, applied repeatedly up to `max_rounds` times in any combination
    -- in a way a single literal replace over the flat text would not already
    remove. A plain, literal occurrence of `key` in `raw_text` is not itself
    flagged: `_error_excerpt` redacts that the ordinary way. What this catches is
    a form (escaped, nested, or both at once, and a body that holds the key in
    both a plain and an encoded form simultaneously) that a literal replacement is
    blind to -- exactly what let a key leak in its escaped form before this fix.
    Once found, the caller withholds the whole body rather than emit a fragment
    that still decodes to the key; this function never tries to say which part is
    safe to keep.

    Bounded (`max_rounds`, `_DECODE_SEARCH_MAX_CANDIDATES`) against a large or
    adversarial body; within those bounds this is a real fixpoint search -- it
    stops as soon as decoding stops producing anything new, and keeps going past
    a single round for a body that needs more than one to reveal the key."""
    if not key:
        return False
    # What a single literal replace over the flat text already removes; anything
    # the decoders below still turn up afterwards is what that replace would miss.
    baseline = raw_text.replace(key, "\0")
    seen = {baseline}
    frontier = [baseline]
    explored = 0
    for _ in range(max_rounds):
        next_frontier: list[str] = []
        for text in frontier:
            for candidate in _decode_candidates(text):
                if candidate in seen:
                    continue
                seen.add(candidate)
                if key in candidate:
                    return True
                explored += 1
                if explored >= _DECODE_SEARCH_MAX_CANDIDATES:
                    return False
                next_frontier.append(candidate)
        if not next_frontier:
            break
        frontier = next_frontier
    return False


def _error_excerpt(body: bytes, key: str, limit: int = 200) -> str:
    """A short, safe excerpt of an HTTP error body: never the request or its headers,
    and with the API key (or an echoed Authorization header) redacted before
    truncation.

    Before anything else, `_key_reachable_by_decoding` checks the body for the key
    in an ENCODED form -- nested any number of JSON levels deep, unicode-escaped,
    percent-encoded, base64-encoded, or several of those stacked, and including a
    body that holds the key in both a plain and an encoded form at once. If it
    finds one, no excerpt is produced at all: a fixed message is returned instead
    of a partially-redacted string that still decodes to the key. This is
    deliberately all-or-nothing -- a literal replace can miss an encoded form
    completely (that was the bug), so once one is confirmed present the whole body
    is withheld rather than risk shipping a fragment nobody actually redacted.

    Otherwise, a JSON body (whatever its top-level shape -- object, array, or a
    bare string or number) is parsed and redacted at the level of its own decoded
    values, then re-rendered; a body that isn't valid JSON is redacted as plain
    text. Either is safe now: the check above already ruled out anything a literal
    replace could miss."""
    raw_text = body.decode("utf-8", "replace")
    if key and _key_reachable_by_decoding(raw_text, key):
        return "(error body omitted: contained the key in an encoded form)"

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        text = redact_secrets(raw_text, key)
        text = " ".join(text.split())
        return text[:limit] + ("..." if len(text) > limit else "")

    redacted = _redact_json_value(parsed, key)
    if isinstance(redacted, dict):
        candidate = redacted.get("error") or redacted.get("message") or redacted
    else:
        candidate = redacted
    text = candidate if isinstance(candidate, str) else json.dumps(candidate, ensure_ascii=False)
    return _redact(text, key, limit)  # safety net: catches a Bearer pattern reassembled above


def score_jev(task: str, context: str | None,
              timeout: int = JEV_TIMEOUT_SECONDS) -> tuple[dict[str, int], set[str], str, dict]:
    """Calibrated scoring via TypeSafe's Jev, called directly over HTTPS with stdlib urllib
    (no SDK). Contract: TypeSafe's HTTP API, https://docs.typesafe.ai/api.
    Score level numbering is not documented as 0- or 1-based, so it is derived per response
    from the `legend` keys (the lowest key maps to rubric level 0) rather than assumed.

    `timeout` bounds only this HTTP call; the hook path passes a short value (--jev-timeout) so
    an unreachable or slow Jev API can't stall a prompt, then falls back to the heuristic."""
    key = get_api_key()
    if not key:
        sys.exit("jev: no TYPESAFE_API_KEY found (checked the environment and .env in the "
                 "current directory; put one line TYPESAFE_API_KEY=<your key> in a file "
                 "named .env in the directory you run from).")
    if not all(33 <= ord(c) <= 126 for c in key):
        sys.exit("jev: TYPESAFE_API_KEY contains whitespace, control, or non-ASCII characters; "
                 "re-copy it.")

    state: dict = {"task": task}
    if context:
        state["context"] = context
    questions: dict = {d: {"type": "score", "instructions": q, "criteria": levels}
                       for d, (q, levels) in RUBRIC.items()}
    questions["kind"] = {"type": "choice",
                         "instructions": "What kind of work is this task, primarily?",
                         "criteria": KINDS}
    body = json.dumps({"state": state, "model": "jev-latest", "questions": questions}).encode("utf-8")

    try:
        req = urllib.request.Request(
            JEV_API_URL, data=body, method="POST",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_body = resp.read()
    except urllib.error.HTTPError as e:
        detail = ""
        if e.code == 422:
            try:
                detail = ": " + _error_excerpt(e.read(), key)
            except OSError:
                pass
        messages = {
            401: "jev: TypeSafe rejected the API key (401 unauthorized).",
            422: "jev: TypeSafe rejected the request" + detail,
            429: "jev: TypeSafe rate limit hit (429). Not retrying.",
            529: "jev: TypeSafe is overloaded (529). Not retrying.",
        }
        sys.exit(messages.get(e.code, f"jev: TypeSafe returned HTTP {e.code}."))
    except (ValueError, UnicodeError):
        # Defence in depth: the ASCII check above should already reject a key that would
        # produce an invalid header, but never let a header-encoding exception (which can
        # quote the bad value) reach the top level with the key inside it.
        sys.exit("jev: could not send the request to TypeSafe (invalid header value).")
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as e:
        sys.exit(f"jev: could not reach or read from TypeSafe ({type(e).__name__}).")

    try:
        resp_json = json.loads(resp_body)
    except json.JSONDecodeError:
        sys.exit("jev: TypeSafe response was not valid JSON.")

    try:
        return validate_jev_response(resp_json, key)
    except JevResponseError as e:
        sys.exit(str(e))


class JevResponseError(ValueError):
    """A Jev API response failed validation. str(e) is a clean, one-line message,
    already safe to print (see `validate_jev_response`)."""


def _finite_number(value, label: str) -> float:
    """float(value), but NaN and +/-Infinity are rejected same as anything that
    doesn't convert at all. `round()` on a NaN or infinite float raises ValueError or
    OverflowError respectively deep inside whatever called this if it isn't caught
    here first, and a NaN comparison (`nan < threshold`) is always False, which
    would otherwise make a NaN confidence look certain instead of invalid."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        raise JevResponseError(f"jev: {label} is not a number.")
    if not math.isfinite(f):
        raise JevResponseError(f"jev: {label} is not a finite number.")
    return f


def validate_jev_response(resp_json, key: str | None = None) -> tuple[dict[str, int], set[str], str, dict]:
    """Pure validation of a decoded Jev response body -- the dict `json.loads` gives
    you for the HTTP response, nothing else. No network, no I/O, so this is directly
    unit-testable with a hand-built dict.

    Every number in the response is checked finite before use. A score must lie
    within its own question's legend range (a probability-weighted mean is
    documented to be fractional, so this is a continuous range, not membership in
    the legend's own levels); a confidence, including the one on `kind`, must lie in
    0..1. Any of that failing raises JevResponseError with a clean, printable
    message instead of a bare ValueError/OverflowError from `round()`, a NaN
    silently treated as a certain confidence, or an out-of-range score silently
    clamped into range. `usage` is best-effort: a field that isn't a finite,
    non-negative number is dropped rather than failing the whole response.

    `key` is only used to redact an unexpected `kind` value before it is quoted in
    an error message (see redact_secrets); it does not affect validation."""
    answers = resp_json.get("answers") if isinstance(resp_json, dict) else None
    if not isinstance(answers, dict):
        raise JevResponseError("jev: TypeSafe response has no 'answers'.")

    scores: dict[str, int] = {}
    unsure: set[str] = set()
    raw: dict = {}
    for d, (_, levels) in RUBRIC.items():
        a = answers.get(d)
        if not isinstance(a, dict):
            raise JevResponseError(f"jev: response is missing an answer for {d}.")
        legend = a.get("legend")
        if not isinstance(legend, dict) or not legend:
            raise JevResponseError(f"jev: response for {d} has no legend to interpret the score scale.")
        if len(legend) != len(levels):
            raise JevResponseError(f"jev: response for {d} has {len(legend)} legend levels, expected {len(levels)}.")
        try:
            legend_keys = [float(k) for k in legend]
        except (TypeError, ValueError):
            raise JevResponseError(f"jev: response for {d} has non-numeric legend keys.")
        if not all(math.isfinite(lk) for lk in legend_keys):
            raise JevResponseError(f"jev: response for {d} has a non-finite legend key.")
        if "score" not in a:
            raise JevResponseError(f"jev: response for {d} is missing a numeric score.")
        score_val = _finite_number(a["score"], f"response for {d}'s score")
        lo, hi = min(legend_keys), max(legend_keys)
        if not (lo <= score_val <= hi):
            raise JevResponseError(
                f"jev: response for {d} has a score {score_val:g} outside its legend range [{lo:g}, {hi:g}].")
        level = int(round(score_val - lo))
        scores[d] = max(0, min(len(levels) - 1, level))  # redundant once score_val is range-checked; kept as a floor/ceiling against rounding at the very edge
        conf = _finite_number(a.get("confidence", 1.0), f"response for {d}'s confidence")
        if not (0.0 <= conf <= 1.0):
            raise JevResponseError(f"jev: response for {d} has a confidence {conf:g} outside 0..1.")
        raw[d] = {"score": score_val, "confidence": conf}
        if conf < CONFIG["jev_unsure_below"]:
            unsure.add(d)

    k = answers.get("kind")
    if not isinstance(k, dict) or "choice" not in k:
        raise JevResponseError("jev: response is missing a 'kind' answer.")
    kind = str(k["choice"])
    if kind not in KINDS:
        raise JevResponseError(f"jev: response chose an unknown kind {redact_secrets(kind, key)!r}.")
    kind_conf = _finite_number(k.get("confidence", 1.0), "response for kind's confidence")
    if not (0.0 <= kind_conf <= 1.0):
        raise JevResponseError(f"jev: response for kind has a confidence {kind_conf:g} outside 0..1.")
    raw["kind"] = {"choice": kind, "confidence": kind_conf}

    usage_obj = resp_json.get("usage") if isinstance(resp_json, dict) else None
    if isinstance(usage_obj, dict):
        usage: dict[str, int] = {}
        for field in ("input_tokens", "output_tokens"):
            try:
                f = float(usage_obj[field])
            except (KeyError, TypeError, ValueError):
                continue  # malformed field; drop it, not fatal
            if math.isfinite(f) and f >= 0:
                usage[field] = int(f)
        if usage:
            raw["usage"] = usage

    return scores, unsure, kind, raw


def format_scores(scores: dict[str, int], unsure: set[str]) -> str:
    return ",".join(f"{d}={scores[d]}{'?' if d in unsure else ''}" for d in DIMS)


# ----------------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------------
def _try_append_log(record: dict) -> "str | None":
    """append_log, but a write failure (COMPLEXITY_LOG is a directory, an empty
    path, an unwritable directory, an unreadable-but-existing file, ...) is
    reported once on stderr rather than raised: the decision (or outcome) this
    call exists to report is not lost just because it couldn't also be logged.
    Returns the log path on success, None if logging was skipped or failed."""
    try:
        return append_log(record)
    except OSError as e:
        print(f"complexity: decision not logged ({_os_error_reason(e)})", file=sys.stderr)
        return None


def cmd_route(args: argparse.Namespace) -> None:
    scores, unsure = parse_scores(args.scores)
    kind = args.kind
    if kind not in KINDS:
        sys.exit(f"--kind must be one of {', '.join(KINDS)}")
    key = get_api_key()
    override = redact_secrets(args.override, key) if args.override else args.override
    d = decide(scores, unsure, kind, args.shards, override, args.after_probe)
    d["task"] = redact_secrets(args.task or "", key)[:200]
    d["event"] = "decision"
    path = None if args.no_log else _try_append_log(d)
    if args.json:
        print(json.dumps(d, indent=2))
    elif args.explain:
        print(card(d, d.get("task"), path))
    else:
        print(one_liner(d))


def cmd_show(args: argparse.Namespace) -> None:
    def _decisions(rows):
        return [r for r in rows if r.get("event") == "decision"]

    try:
        if args.id in (None, "last"):
            rows = _decisions(read_log_tail()) or _decisions(read_log())
            if not rows:
                sys.exit(f"no decisions logged yet ({log_path()})")
            d = rows[-1]
        else:
            matches = [r for r in _decisions(read_log_tail()) if r.get("id") == args.id]
            if not matches:
                matches = [r for r in _decisions(read_log()) if r.get("id") == args.id]
            if not matches:
                sys.exit(f"no decision {args.id} in {log_path()}")
            d = matches[-1]
    except LogUnreadable as e:
        sys.exit(f"complexity: {e}")
    try:
        print(card(d, d.get("task"), log_path()))
    except KeyError as e:
        # A record missing a required field entirely (e.g. an older or hand-edited
        # log entry without `scores_given`) must not die with a raw KeyError
        # traceback. Say what's missing and what is there instead, and exit
        # non-zero rather than claim success.
        sys.exit(f"decision {d.get('id', '?')} is an incomplete record (missing {e}); "
                 f"present fields: {', '.join(sorted(d))}")
    except TypeError:
        # F12 (PLAYBOOK.md "skill publish-hardening", Task F): a field that IS
        # present but the wrong type (e.g. `shards` holding a string instead of a
        # number, which then fails a `>` comparison against CONFIG["parallel_cap"])
        # is a different problem from a field that's missing entirely, and
        # printing the raw TypeError text ("'>' not supported between instances
        # of 'str' and 'int'") would be a confusing way to say so. State it
        # plainly instead, with no raw exception text and no "missing" wording.
        sys.exit(f"decision {d.get('id', '?')} has a malformed field of the wrong type; "
                 f"present fields: {', '.join(sorted(d))}")


def cmd_score(args: argparse.Namespace) -> None:
    raw = None
    truncated = False
    if args.jev:
        scores, unsure, kind, raw = score_jev(args.task, args.context, args.jev_timeout)
        src = "jev"
    else:
        scores, unsure, kind, truncated = score_heuristic(args.task)
        src = "heuristic"
    if args.kind:
        kind = args.kind

    scores_line = f"scores ({src})  {format_scores(scores, unsure)}  kind={kind}"
    note = None
    if src == "heuristic":
        note = "note: keyword heuristic; your own read of the task and repo should override these."
        if truncated:
            note += f" (task truncated to {HEURISTIC_TASK_MAX_CHARS} chars for scoring)"

    d = None
    path = None
    if args.route:
        key = get_api_key()
        d = decide(scores, unsure, kind, None, None, args.after_probe)
        d["task"] = redact_secrets(args.task, key)[:200]
        d["event"] = "decision"
        d["scorer"] = src
        path = None if args.no_log else _try_append_log(d)

    if args.json:
        # One JSON document, always -- whether or not --route or --jev were given,
        # and never mixed with any of the human lines below. Before this, --json only
        # ever added a bare JSON block after the human "scores (...)" line in --jev mode,
        # and printed nothing extra at all (silently ignoring --json) in heuristic mode.
        doc = {
            "scorer": src,
            "scores_line": scores_line,
            "scores": scores,
            "unsure": sorted(unsure),
            "kind": kind,
            "raw": raw,
            "note": note,
        }
        if args.route:
            doc["decision"] = d
            doc["one_liner"] = one_liner(d)
        print(json.dumps(doc, indent=2))
        return

    print(scores_line)
    if note:
        print(note)
    if args.route:
        print(card(d, d.get("task"), path) if args.explain else one_liner(d))


def cmd_outcome(args: argparse.Namespace) -> None:
    try:
        known = any(r.get("event") == "decision" and r.get("id") == args.id for r in read_log_tail())
        if not known:
            known = any(r.get("event") == "decision" and r.get("id") == args.id for r in read_log())
    except LogUnreadable as e:
        sys.exit(f"complexity: {e}")
    if not known:
        sys.exit(f"no decision {args.id} in {log_path()}; check the id with `route.py show {args.id}`")
    key = get_api_key()
    rec = {
        "event": "outcome",
        "id": args.id,
        "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "result": args.result,
        "note": redact_secrets(args.note or "", key),
    }
    try:
        path = append_log(rec)
    except OSError as e:
        sys.exit(f"complexity: outcome not recorded ({_os_error_reason(e)})")
    print(f"recorded {args.result} for {args.id} in {path}")


def cmd_stats(args: argparse.Namespace) -> None:
    # --last N reads a bounded tail of the log (see read_log_tail); without it, the
    # summary is over everything, which does need the whole file.
    try:
        rows = read_log_tail() if args.last else read_log()
    except LogUnreadable as e:
        sys.exit(f"complexity: {e}")
    if not rows:
        print(f"no decisions logged yet ({log_path()})")
        return
    decisions = [r for r in rows if r.get("event") == "decision"]
    if args.last:
        decisions = decisions[-args.last:]

    # Every outcome ever recorded for each id, in log order -- not just the last one.
    # Before this fix, only the last outcome was kept, so a decision that was retried
    # and then came back ok showed zero retries: the "ok" silently overwrote the "retry".
    # The per-tier "final" columns below still come from the last entry in this history,
    # so their meaning is unchanged; "ever_*" is the new information.
    outcome_history: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        if r.get("event") == "outcome" and r.get("id"):
            outcome_history[r["id"]].append(r.get("result", ""))

    by_tier: dict[str, Counter] = defaultdict(Counter)
    modes = Counter()
    overrides = 0
    rated = 0
    for d in decisions:
        modes[d.get("mode")] += 1
        if d.get("override"):
            overrides += 1
        t = d.get("model", "?")
        by_tier[t]["n"] += 1
        hist = outcome_history.get(d.get("id"), [])
        if hist:
            rated += 1
            by_tier[t]["rated"] += 1
            by_tier[t][hist[-1]] += 1  # final result -- same meaning as before the fix
            if "retry" in hist:
                by_tier[t]["ever_retry"] += 1
            if "escalated" in hist:
                by_tier[t]["ever_escalated"] += 1
            if "failed" in hist:
                by_tier[t]["ever_failed"] += 1
    print(f"{len(decisions)} decisions, {rated} with outcomes, "
          f"{overrides} overrides   ({log_path()})")
    print("\nmodes: " + ", ".join(f"{m} {n}" for m, n in modes.most_common()))
    print("\ntier      n   rated   ok   retry   escalated   failed   (final result)")
    for t in CONFIG["tiers"]:
        c = by_tier.get(t)
        if not c:
            continue
        print(f"{t:8} {c['n']:3}   {c['rated']:5}   {c['ok']:2}   {c['retry']:5}   {c['escalated']:9}   {c['failed']:6}")
    print("\ntier      ever_retry   ever_escalated   ever_failed   (at any point, not just the final result)")
    for t in CONFIG["tiers"]:
        c = by_tier.get(t)
        if not c:
            continue
        print(f"{t:8} {c['ever_retry']:10}   {c['ever_escalated']:14}   {c['ever_failed']:9}")
    hints = []
    for t, c in by_tier.items():
        if c["rated"] >= 5:
            bad = (c["retry"] + c["escalated"] + c["failed"]) / c["rated"]
            if bad >= 0.3:
                hints.append(f"{t}: {bad:.0%} of rated decisions needed a retry or escalation; "
                             f"consider narrowing its difficulty band at the top")
            elif bad == 0 and c["rated"] >= 10 and t != CONFIG["tiers"][0]:
                hints.append(f"{t}: no retries in {c['rated']} rated decisions; the band below it may be able to take more")
    if overrides and decisions:
        hints.append(f"overrides: {overrides}/{len(decisions)}; read them with "
                     f"`grep override {log_path()}` to see which rule the user keeps disagreeing with")
    if hints:
        print("\nhints:\n  " + "\n  ".join(hints))


def cmd_rubric(_: argparse.Namespace) -> None:
    for d, (q, levels) in RUBRIC.items():
        print(f"{d}  {q}")
        for i, lv in enumerate(levels):
            print(f"   {i}  {lv}")
    print("kind  " + " | ".join(f"{k}: {v}" for k, v in KINDS.items()))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("route", help="scores -> decision card (logged)")
    r.add_argument("--scores", required=True, help='e.g. "S=2,R=3?,A=2,K=2,I=1,P=0,V=2"')
    r.add_argument("--kind", required=True, choices=list(KINDS))
    r.add_argument("--task", help="short task description for the log")
    r.add_argument("--shards", type=positive_int(), help="estimated independent shards (fan-out only)")
    r.add_argument("--override", help="what the user asked for instead, if anything")
    r.add_argument("--after-probe", action="store_true", help="this is the re-route after a probe: never probe again")
    r.add_argument("--explain", action="store_true", help="print the full card instead of the one-liner")
    r.add_argument("--json", action="store_true")
    r.add_argument("--no-log", action="store_true")
    r.set_defaults(fn=cmd_route)

    sh = sub.add_parser("show", help="print the full card for a logged decision (default: last)")
    sh.add_argument("id", nargs="?", help="decision id, or 'last'")
    sh.set_defaults(fn=cmd_show)

    s = sub.add_parser("score", help="estimate scores from the task text (heuristic, or --jev)")
    s.add_argument("--task", required=True)
    s.add_argument("--context", help="extra state for the scorer: repo facts, probe output")
    s.add_argument("--jev", action="store_true",
                   help="use TypeSafe Jev via direct HTTPS call (needs TYPESAFE_API_KEY, "
                        "from the environment or .env)")
    s.add_argument("--jev-timeout", type=positive_int(120), default=JEV_TIMEOUT_SECONDS,
                   help="seconds to wait for the Jev API call before giving up (default: "
                        "%(default)s); the hook path passes a short value and falls back to "
                        "the heuristic scorer if it's exceeded")
    s.add_argument("--kind", choices=list(KINDS), help="override the detected kind")
    s.add_argument("--route", action="store_true", help="also route and log")
    s.add_argument("--after-probe", action="store_true", help="re-route after a probe: never probe again")
    s.add_argument("--explain", action="store_true", help="with --route: print the full card")
    s.add_argument("--json", action="store_true")
    s.add_argument("--no-log", action="store_true")
    s.set_defaults(fn=cmd_score)

    o = sub.add_parser("outcome", help="record how a routed task went")
    o.add_argument("--id", required=True)
    o.add_argument("--result", required=True, choices=["ok", "retry", "escalated", "failed"])
    o.add_argument("--note")
    o.set_defaults(fn=cmd_outcome)

    st = sub.add_parser("stats", help="summarize the decision log")
    st.add_argument("--last", type=positive_int(), help="summarize only the last N decisions")
    st.set_defaults(fn=cmd_stats)

    rb = sub.add_parser("rubric", help="print the rubric levels")
    rb.set_defaults(fn=cmd_rubric)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)  # quiet exit when piped into head
    except (ImportError, AttributeError, ValueError):
        pass
    main()
