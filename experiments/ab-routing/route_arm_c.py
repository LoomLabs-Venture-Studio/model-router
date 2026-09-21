#!/usr/bin/env python3
"""route_arm_c.py: Phase 2's router -- build the prompt a fresh Claude instance
sees to decide how to delegate one ticket, and strictly record its reply.

Reuses route_arm_a.py's context builder (build_context/context_to_text): the
same throwaway-clone pytest run and pristine assertion, so the router sees
exactly what Jev saw in Phase 1. Unlike Jev (a direct HTTP call this script
makes itself), the router here is a real Claude Code subagent with no tools,
so it cannot read a file -- the operator pastes the prompt this script writes
into it verbatim, then saves its reply to a file for --record.

Two separate invocations:
  route_arm_c.py --run <id> --task <n> --brief <ticket> [--runs-root <path>]
                 [--prompt-dir <dir>] [--after-probe --probe-output <path>]
      Writes <prompt-dir>/task<n>_router_prompt.md (and a same-directory
      "_pending.json" companion recording the context/brief/prompt hash for
      --record to pick up) and prints the prompt's path.

  route_arm_c.py --run <id> --task <n> --record <reply file> [--runs-root <path>]
                 [--prompt-dir <dir>]
      Parses the reply STRICTLY (see extract_json_object/validate_decision)
      and, only on success, appends the full decision to <run>/decisions.json.
      There is no flag that lets the operator supply, edit, or fall back to a
      decision -- only what the router actually replied is ever recorded.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import route_arm_a  # noqa: E402 -- reuse build_context/context_to_text/TaskCopyMutated/append_decision

DEFAULT_RUNS_ROOT = route_arm_a.DEFAULT_RUNS_ROOT
MODELS = ("haiku", "sonnet", "opus", "fable")
MODES = ("agent", "fan-out")
REQUIRED_FIELDS = {"model", "mode", "shards", "probe_first", "verifier", "confidence", "reason"}

# Case-insensitive substrings the rendered prompt must never contain -- a router
# with tools has none here, but the prompt itself must give it nothing to infer
# an experiment, a scorer, or Phase 1 exists.
FORBIDDEN_WORDS = ("experiment", "arm ", "jev", "typesafe", "rubric", "hidden",
                   "baseline", "ab-routing", "claude-jev-skill", "phase")

ROLE_SENTENCE = "You are deciding how to delegate one engineering ticket to an AI coding agent."
OBJECTIVE_SENTENCE = "Choose the cheapest option you are confident will complete the ticket correctly."
REPLY_INSTRUCTIONS = ("Do not call any tool or skill. Do not ask questions. Reply with only this "
                       "JSON object and nothing else:")
SCHEMA_TEXT = """{
  "model": "haiku" | "sonnet" | "opus" | "fable",  // ascending in both cost and capability
  "mode": "agent" | "fan-out",  // "agent": one agent does the whole ticket. "fan-out": several
                                // agents split it into independent pieces
  "shards": <int, 1 for "agent">,  // number of independent pieces for "fan-out"
  "probe_first": <bool>,  // true: a cheap, read-only agent sizes the task first, then you
                          // decide again with its findings
  "verifier": null | "haiku" | "sonnet" | "opus" | "fable",  // null, or a model that
                                                              // independently reviews the result
  "confidence": <number 0 to 1, your confidence the chosen option completes the ticket correctly>,
  "reason": "<60 words or fewer>"
}"""


def default_prompt_dir(runs_root: Path, run_id: str) -> Path:
    return runs_root.parent / "router" / run_id


# ----------------------------------------------------------------------------
# Prompt rendering
# ----------------------------------------------------------------------------
def build_menu() -> str:
    return (
        "## Menu\n\n"
        "- `model`: one of `haiku`, `sonnet`, `opus`, `fable`, in ascending order of both "
        "cost and capability.\n"
        "- `mode`: `agent` (one agent does the whole ticket) or `fan-out` (several agents "
        "split it into independent pieces; give `shards`, the number of pieces).\n"
        "- `probe_first`: true means a cheap, read-only agent looks at the ticket first and "
        "sizes it, and you will be asked to decide again with its findings.\n"
        "- `verifier`: null, or a model that independently reviews the result after it is done.\n"
    )


def render_prompt(ticket_text: str, context_text: str, probe_text: str | None = None) -> str:
    parts = [
        ROLE_SENTENCE, "",
        "## Ticket", "",
        "<<<TICKET>>>", ticket_text.strip(), "<<<END TICKET>>>", "",
        "## Repository facts", "",
        "<<<CONTEXT>>>", context_text.strip(), "<<<END CONTEXT>>>", "",
    ]
    if probe_text is not None:
        parts += [
            "## Findings from a read-only sizing pass", "",
            "<<<PROBE>>>", probe_text.strip(), "<<<END PROBE>>>", "",
            "A sizing pass has already been done; set probe_first to false.", "",
        ]
    parts += [build_menu(), OBJECTIVE_SENTENCE, "", REPLY_INSTRUCTIONS, "", SCHEMA_TEXT, ""]
    return "\n".join(parts)


def assert_no_forbidden_words(prompt_text: str) -> None:
    lowered = prompt_text.lower()
    hits = [w for w in FORBIDDEN_WORDS if w in lowered]
    if hits:
        raise ValueError(f"router prompt contains forbidden word(s): {', '.join(hits)!r}")


# ----------------------------------------------------------------------------
# Reply parsing / validation
# ----------------------------------------------------------------------------
def extract_json_object(reply_text: str) -> dict:
    """The reply must be EXACTLY a JSON object, optionally wrapped in a
    ```json fence, with only surrounding whitespace -- any other prose makes
    it invalid (fullmatch/whole-string parse, not a search)."""
    text = reply_text.strip()
    fence = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n?```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"reply is not exactly one JSON object (optionally ```json-fenced): {e}")
    if not isinstance(obj, dict):
        raise ValueError("reply JSON is not an object")
    return obj


def validate_decision(obj: dict, after_probe: bool) -> dict:
    """Validates every field and type exactly per the schema. Raises ValueError
    naming the first problem found. Returns the decision fields to record,
    with probe_first forced false (and probe_first_ignored recorded) if an
    after-probe reply set it true anyway -- per the design, that is recorded
    as ignored, not rejected."""
    extra = set(obj) - REQUIRED_FIELDS
    if extra:
        raise ValueError(f"unexpected field(s): {', '.join(sorted(extra))}")
    missing = REQUIRED_FIELDS - set(obj)
    if missing:
        raise ValueError(f"missing field(s): {', '.join(sorted(missing))}")

    model = obj["model"]
    if model not in MODELS:
        raise ValueError(f"model must be one of {MODELS}, got {model!r}")

    mode = obj["mode"]
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")

    shards = obj["shards"]
    if not isinstance(shards, int) or isinstance(shards, bool):
        raise ValueError(f"shards must be an integer, got {shards!r}")
    if mode == "agent" and shards != 1:
        raise ValueError(f"mode 'agent' requires shards == 1, got {shards}")
    if mode == "fan-out" and shards < 2:
        raise ValueError(f"mode 'fan-out' requires shards >= 2, got {shards}")

    probe_first = obj["probe_first"]
    if not isinstance(probe_first, bool):
        raise ValueError(f"probe_first must be a boolean, got {probe_first!r}")

    verifier = obj["verifier"]
    if verifier is not None and verifier not in MODELS:
        raise ValueError(f"verifier must be null or one of {MODELS}, got {verifier!r}")

    confidence = obj["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError(f"confidence must be a number, got {confidence!r}")
    if not (0 <= confidence <= 1):
        raise ValueError(f"confidence must be between 0 and 1, got {confidence!r}")

    reason = obj["reason"]
    if not isinstance(reason, str):
        raise ValueError(f"reason must be a string, got {reason!r}")
    word_count = len(reason.split())
    if word_count > 60:
        raise ValueError(f"reason must be 60 words or fewer, got {word_count}")

    probe_first_ignored = after_probe and probe_first
    return {
        "model": model, "mode": mode, "shards": shards,
        "probe_first": False if probe_first_ignored else probe_first,
        "probe_first_ignored": probe_first_ignored,
        "verifier": verifier, "confidence": confidence, "reason": reason,
    }


# ----------------------------------------------------------------------------
# Build mode
# ----------------------------------------------------------------------------
def do_build(args: argparse.Namespace) -> int:
    runs_root = Path(args.runs_root).expanduser() if args.runs_root else DEFAULT_RUNS_ROOT
    run_dir = runs_root / args.run
    task_dir = run_dir / "A" / f"task{args.task}"
    if not task_dir.is_dir():
        print(f"route_arm_c: {task_dir} does not exist (run make_run.py first)", file=sys.stderr)
        return 1

    brief_path = Path(args.brief)
    try:
        ticket_text = brief_path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"route_arm_c: could not read --brief {brief_path}: {e}", file=sys.stderr)
        return 1

    try:
        context_str = route_arm_a.context_to_text(route_arm_a.build_context(task_dir))
    except route_arm_a.TaskCopyMutated as e:
        print(str(e), file=sys.stderr)
        return 1

    probe_text = None
    if args.after_probe:
        try:
            probe_text = Path(args.probe_output).read_text(encoding="utf-8")
        except OSError as e:
            print(f"route_arm_c: could not read --probe-output {args.probe_output}: {e}", file=sys.stderr)
            return 1

    prompt_text = render_prompt(ticket_text, context_str, probe_text)
    try:
        assert_no_forbidden_words(prompt_text)
    except ValueError as e:
        print(f"route_arm_c: refusing to write prompt: {e}", file=sys.stderr)
        return 1

    prompt_dir = Path(args.prompt_dir).expanduser() if args.prompt_dir else default_prompt_dir(runs_root, args.run)
    prompt_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = prompt_dir / f"task{args.task}_router_prompt.md"
    prompt_path.write_text(prompt_text, encoding="utf-8")

    pending = {
        "task": args.task,
        "call_type": "after_probe" if args.after_probe else "initial",
        "brief_path": str(brief_path),
        "context": context_str,
        "prompt_path": str(prompt_path),
        "prompt_sha256": hashlib.sha256(prompt_text.encode("utf-8")).hexdigest(),
    }
    (prompt_dir / f"task{args.task}_pending.json").write_text(
        json.dumps(pending, indent=2) + "\n", encoding="utf-8")

    print(str(prompt_path))
    return 0


# ----------------------------------------------------------------------------
# Record mode
# ----------------------------------------------------------------------------
def do_record(args: argparse.Namespace) -> int:
    runs_root = Path(args.runs_root).expanduser() if args.runs_root else DEFAULT_RUNS_ROOT
    run_dir = runs_root / args.run
    if not run_dir.is_dir():
        print(f"route_arm_c: {run_dir} does not exist (run make_run.py first)", file=sys.stderr)
        return 1

    prompt_dir = Path(args.prompt_dir).expanduser() if args.prompt_dir else default_prompt_dir(runs_root, args.run)
    pending_path = prompt_dir / f"task{args.task}_pending.json"
    if not pending_path.exists():
        print(f"route_arm_c: no pending prompt at {pending_path} (build the prompt first)", file=sys.stderr)
        return 1
    try:
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"route_arm_c: could not read {pending_path}: {e}", file=sys.stderr)
        return 1

    reply_path = Path(args.record)
    try:
        reply_text = reply_path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"route_arm_c: could not read --record {reply_path}: {e}", file=sys.stderr)
        return 1

    after_probe = pending.get("call_type") == "after_probe"
    try:
        obj = extract_json_object(reply_text)
        decision_fields = validate_decision(obj, after_probe)
    except ValueError as e:
        print(f"route_arm_c: invalid reply: {e}", file=sys.stderr)
        return 1

    record = {
        "task": pending["task"],
        "arm": "A",
        "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "router": "claude",
        "call_type": pending["call_type"],
        "brief_path": pending["brief_path"],
        "context": pending["context"],
        "prompt_path": pending["prompt_path"],
        "prompt_sha256": pending["prompt_sha256"],
        **decision_fields,
        "reply_sha256": hashlib.sha256(reply_text.encode("utf-8")).hexdigest(),
    }
    route_arm_a.append_decision(run_dir / "decisions.json", record)

    shard_note = f" x{decision_fields['shards']}" if decision_fields["mode"] == "fan-out" else ""
    print(f"route_arm_c: recorded task {pending['task']} ({pending['call_type']}): "
          f"model={decision_fields['model']} mode={decision_fields['mode']}{shard_note}")
    return 0


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run", required=True)
    p.add_argument("--task", required=True, type=int, choices=(1, 2, 3, 4))
    p.add_argument("--runs-root", help=f"default: {DEFAULT_RUNS_ROOT}")
    p.add_argument("--prompt-dir", help="default: <runs-root>/../router/<run-id>")
    p.add_argument("--brief", help="build mode: path to the ticket file; its text is sent verbatim")
    p.add_argument("--after-probe", action="store_true", help="build mode: the re-route after a probe")
    p.add_argument("--probe-output", help="build mode with --after-probe: path to the probe's output text")
    p.add_argument("--record", help="record mode: path to the router's raw reply text")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.record:
        return do_record(args)
    if not args.brief:
        parser.error("--brief is required unless --record is given")
    if args.after_probe and not args.probe_output:
        parser.error("--after-probe requires --probe-output")
    return do_build(args)


if __name__ == "__main__":
    sys.exit(main())
