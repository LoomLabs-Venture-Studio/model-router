#!/usr/bin/env python3
"""account.py: turn a run's manifest of subagent transcripts, plus Arm A's Jev
usage, into a cost report priced from pricing.json.

manifest.json maps each (arm, task) slot to a list of transcript entries:
  {"A/task1": [{"arm": "A", "task": 1, "role": "engineer", "transcript": "/path/x.jsonl"}, ...], ...}
role is one of "engineer", "probe", "reviewer".

Transcripts are Claude Code subagent JSONL logs. Assistant records look like:
  {"type": "assistant", "message": {"id": "msg_...", "model": "claude-sonnet-5",
   "usage": {"input_tokens": .., "output_tokens": .., "cache_creation_input_tokens": ..,
             "cache_read_input_tokens": ..,
             "cache_creation": {"ephemeral_5m_input_tokens": .., "ephemeral_1h_input_tokens": ..},
             "service_tier": "standard", "inference_geo": "global", "speed": "standard"}}}
The same message id can repeat across streaming chunks; each is counted once, at its
last occurrence. Non-assistant records, and assistant records without a usage block,
are ignored.

Cache-write tokens are priced by TTL (5-minute vs 1-hour cache writes are priced
differently). When a message's usage carries the "cache_creation" breakdown, it is
used directly (and cross-checked against the flat cache_creation_input_tokens field
-- a mismatch is a hard error naming the message). When the breakdown is absent
(older transcripts), the flat figure is priced at the 5-minute rate as a fallback,
and the fallback is recorded in the report with the count of messages affected.

Non-default billing modifiers (service_tier, inference_geo, speed) change the price
and are not modeled: "us" inference_geo, "fast" speed, or a batch service_tier are
hard errors naming the value and the transcript; any other unrecognised value is
recorded and a one-line warning is printed, but accounting continues (priced as
standard). Distinct values seen per (arm, task) are recorded in the report either way.

Prices come from pricing.json, looked up by the LONGEST configured prefix of the
model id (ids can carry date/variant suffixes). A model matching no prefix, or a
token type present in usage with no configured price (missing or null), is a hard
error -- this never silently prices anything at zero.

Router transcripts (role "router") get a router_marginal_usd alongside their full
usd, both per line item and summed per arm (per_arm[arm].router_full_usd /
router_marginal_usd): marginal is just the uncached input plus output tokens,
excluding cache writes and reads, since a real session deciding for itself
already has its context loaded -- caching isn't a genuine extra cost of routing
the way it is for a fresh subagent. Phase 1's Jev decisions (role "router",
model jev-latest, priced from decisions.json) keep working unchanged; a decision
with "router": "claude" carries no Jev usage and is skipped there -- its cost
comes from a manifest transcript entry (role "router") instead, priced like any
other line item.

Usage: account.py --run <id> [--runs-root <path>] [--pricing <path>]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import NoReturn

TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
DEFAULT_RUNS_ROOT = Path("~/.cache/ab-routing/runs").expanduser()
JEV_MODEL = "jev-latest"

GUARD_FIELDS = ("service_tier", "inference_geo", "speed")
GUARD_SAFE = {"service_tier": "standard", "inference_geo": "global", "speed": "standard"}
GUARD_BLOCKED = {"inference_geo": {"us"}, "speed": {"fast"}}


def die(msg: str) -> NoReturn:
    sys.exit(f"account: {msg}")


def empty_tokens() -> dict:
    return {f: 0 for f in TOKEN_FIELDS}


def add_tokens(a: dict, b: dict) -> dict:
    return {f: a.get(f, 0) + (b.get(f, 0) or 0) for f in TOKEN_FIELDS}


def load_json(path: Path, default):
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ----------------------------------------------------------------------------
# Transcript parsing
# ----------------------------------------------------------------------------
def parse_transcript(path: Path) -> dict:
    """Returns {message_id: (model, usage)}. The same message id overwrites its
    earlier entry as the file is scanned top to bottom, so the LAST occurrence
    (final streamed usage totals) is what survives."""
    out: dict = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict) or rec.get("type") != "assistant":
                continue
            msg = rec.get("message")
            if not isinstance(msg, dict):
                continue
            usage = msg.get("usage")
            msg_id = msg.get("id")
            if not isinstance(usage, dict) or not msg_id:
                continue
            out[msg_id] = (msg.get("model"), usage)
    return out


# ----------------------------------------------------------------------------
# Cache-write TTL split
# ----------------------------------------------------------------------------
def split_cache_write(model_id: str, msg_id: str, usage: dict, transcript_path,
                       fallback_stats: dict) -> tuple[int, int]:
    """Returns (ephemeral_5m_tokens, ephemeral_1h_tokens) for one message's cache
    writes. Uses the "cache_creation" breakdown when present, cross-checked
    against the flat cache_creation_input_tokens field (a hard error on mismatch,
    naming the message). Falls back to pricing the flat figure entirely as a
    5-minute write when the breakdown is absent, recording the fallback."""
    flat = usage.get("cache_creation_input_tokens", 0) or 0
    breakdown = usage.get("cache_creation")
    if isinstance(breakdown, dict) and breakdown:
        m5 = breakdown.get("ephemeral_5m_input_tokens", 0) or 0
        m1h = breakdown.get("ephemeral_1h_input_tokens", 0) or 0
        if m5 + m1h != flat:
            die(f"message {msg_id!r} ({model_id}) in {transcript_path}: cache_creation breakdown "
                f"({m5} 5m + {m1h} 1h = {m5 + m1h}) does not match cache_creation_input_tokens ({flat})")
        return m5, m1h
    if flat:
        fallback_stats["count"] += 1
        fallback_stats["tokens"] += flat
    return flat, 0


# ----------------------------------------------------------------------------
# Guard rails: billing modifiers this accounting does not model
# ----------------------------------------------------------------------------
def check_guard_value(field: str, value, model_id: str, msg_id: str, transcript_path,
                       warned: set) -> None:
    if value in (None, ""):
        return
    if field == "service_tier" and "batch" in str(value).lower():
        die(f"message {msg_id!r} ({model_id}) in {transcript_path} uses service_tier={value!r} "
            f"(batch pricing is not modeled by this accounting)")
    if value in GUARD_BLOCKED.get(field, set()):
        die(f"message {msg_id!r} ({model_id}) in {transcript_path} uses {field}={value!r} "
            f"(changes the price and is not modeled by this accounting)")
    if value != GUARD_SAFE.get(field):
        key = (field, value)
        if key not in warned:
            warned.add(key)
            print(f"account: warning: unrecognised {field}={value!r} for {model_id} in "
                  f"{transcript_path} (message {msg_id!r}); priced as {GUARD_SAFE.get(field)!r}",
                  file=sys.stderr)


# ----------------------------------------------------------------------------
# Pricing
# ----------------------------------------------------------------------------
def find_price_row(model_id: str, pricing: dict) -> tuple[str, str, dict]:
    candidates = []
    for provider, pdata in pricing.items():
        for prefix, row in (pdata.get("prices") or {}).items():
            if model_id.startswith(prefix):
                candidates.append((len(prefix), provider, prefix, row))
    if not candidates:
        die(f"no pricing entry matches model {model_id!r} (add a prefix for it to pricing.json)")
    candidates.sort(key=lambda c: -c[0])
    longest = candidates[0][0]
    tied = [c for c in candidates if c[0] == longest]
    if len(tied) > 1:
        die(f"model {model_id!r} matches more than one equally-specific pricing prefix: "
            f"{[c[2] for c in tied]}")
    _, provider, prefix, row = tied[0]
    return provider, prefix, row


def price_message(model_id: str, msg_id: str, usage: dict, transcript_path, pricing: dict,
                   fallback_stats: dict | None = None) -> tuple[float, dict, str, str]:
    """Prices one message's usage, splitting cache-write tokens by TTL. Returns
    (usd, breakdown, provider, matched_prefix)."""
    if fallback_stats is None:
        fallback_stats = {"count": 0, "tokens": 0}
    m5, m1h = split_cache_write(model_id, msg_id, usage, transcript_path, fallback_stats)
    provider, prefix, row = find_price_row(model_id, pricing)

    usd = 0.0
    breakdown = {}
    parts = [
        ("input_tokens", usage.get("input_tokens", 0) or 0, "input"),
        ("output_tokens", usage.get("output_tokens", 0) or 0, "output"),
        ("cache_read_input_tokens", usage.get("cache_read_input_tokens", 0) or 0, "cache_read"),
        ("cache_write_5m_tokens", m5, "cache_write_5m"),
        ("cache_write_1h_tokens", m1h, "cache_write_1h"),
    ]
    for label, n, price_field in parts:
        if n <= 0:
            continue
        price = row.get(price_field)
        if price is None:
            die(f"pricing for {model_id!r} (matched prefix {prefix!r}) has no {price_field!r} price, "
                f"but usage has {n} {label} (message {msg_id!r} in {transcript_path})")
        cost = n * price / 1_000_000
        usd += cost
        breakdown[label] = {"tokens": n, "price_per_mtok": price, "usd": cost}
    return usd, breakdown, provider, prefix


def marginal_router_usd(breakdown: dict) -> float:
    """A router's marginal cost: its uncached input plus output tokens only.
    Cache writes/reads are excluded -- a real session deciding for itself
    already has its context loaded, so caching isn't a genuine extra cost of
    routing the way it is for a fresh subagent starting cold."""
    return (breakdown.get("input_tokens", {}).get("usd", 0.0)
            + breakdown.get("output_tokens", {}).get("usd", 0.0))


def aggregate_line_items(line_items: list[dict]) -> dict:
    """Rolls a flat line_items list up into per_task / per_arm / per_arm_model /
    grand_total_usd, plus per_arm router_full_usd / router_marginal_usd (summed
    over role=="router" items only -- see marginal_router_usd). Pulled out of
    build_report so it's directly testable against a real costs.json's own
    line_items, independent of how they were produced (manifest transcripts or
    Jev decisions)."""
    per_task: dict = defaultdict(lambda: defaultdict(lambda: {"tokens": empty_tokens(), "usd": 0.0}))
    per_arm: dict = defaultdict(lambda: {"tokens": empty_tokens(), "usd": 0.0,
                                          "router_full_usd": 0.0, "router_marginal_usd": 0.0})
    per_arm_model: dict = defaultdict(lambda: defaultdict(lambda: {"tokens": empty_tokens(), "usd": 0.0}))

    for li in line_items:
        arm, task, model = li["arm"], li["task"], li["model"]
        b = per_task[task][arm]
        b["tokens"] = add_tokens(b["tokens"], li["tokens"])
        b["usd"] += li["usd"]
        per_arm[arm]["tokens"] = add_tokens(per_arm[arm]["tokens"], li["tokens"])
        per_arm[arm]["usd"] += li["usd"]
        if li.get("role") == "router":
            per_arm[arm]["router_full_usd"] += li["usd"]
            per_arm[arm]["router_marginal_usd"] += li.get("router_marginal_usd", 0.0)
        mb = per_arm_model[arm][model]
        mb["tokens"] = add_tokens(mb["tokens"], li["tokens"])
        mb["usd"] += li["usd"]

    return {
        "per_task": {str(t): dict(arms) for t, arms in per_task.items()},
        "per_arm": dict(per_arm),
        "per_arm_model": {arm: dict(models) for arm, models in per_arm_model.items()},
        "grand_total_usd": {arm: v["usd"] for arm, v in per_arm.items()},
    }


# ----------------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------------
def resolve_transcript_path(raw: str, run_dir: Path) -> Path:
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p
    candidate = run_dir / p
    return candidate if candidate.exists() else p


def build_report(run_dir: Path, pricing: dict) -> dict:
    manifest = load_json(run_dir / "manifest.json", {})
    if not isinstance(manifest, dict):
        die(f"manifest.json at {run_dir} is not a JSON object")
    decisions = load_json(run_dir / "decisions.json", [])
    if not isinstance(decisions, list):
        die(f"decisions.json at {run_dir} is not a JSON array")

    line_items = []
    fallback_totals: dict = defaultdict(lambda: {"count": 0, "tokens": 0})
    guardrail_values: dict = defaultdict(lambda: defaultdict(set))
    warned: set = set()

    for slot, entries in manifest.items():
        if not entries:
            continue
        for entry in entries:
            for key in ("arm", "task", "role", "transcript"):
                if key not in entry:
                    die(f"manifest entry in slot {slot!r} is missing {key!r}: {entry}")
            path = resolve_transcript_path(entry["transcript"], run_dir)
            if not path.exists():
                die(f"transcript not found for slot {slot!r}: {path}")

            slot_key = f"{entry['arm']}/task{entry['task']}"
            per_model_tokens: dict = defaultdict(empty_tokens)
            per_model_usd: dict = defaultdict(float)
            per_model_breakdown: dict = defaultdict(dict)

            for msg_id, (model, usage) in parse_transcript(path).items():
                if not model:
                    continue
                for field in GUARD_FIELDS:
                    check_guard_value(field, usage.get(field), model, msg_id, path, warned)
                    if usage.get(field) not in (None, ""):
                        guardrail_values[slot_key][field].add(usage[field])

                per_model_tokens[model] = add_tokens(per_model_tokens[model], usage)
                usd, breakdown, provider, prefix = price_message(
                    model, msg_id, usage, path, pricing, fallback_totals[model])
                per_model_usd[model] += usd
                for label, d in breakdown.items():
                    agg = per_model_breakdown[model].setdefault(
                        label, {"tokens": 0, "price_per_mtok": d["price_per_mtok"], "usd": 0.0})
                    agg["tokens"] += d["tokens"]
                    agg["usd"] += d["usd"]
                per_model_breakdown[model]["_price_row"] = {"provider": provider, "prefix": prefix}

            for model, tokens in per_model_tokens.items():
                pr = per_model_breakdown[model].pop("_price_row")
                li = {
                    "arm": entry["arm"], "task": entry["task"], "role": entry["role"], "model": model,
                    "price_row": pr, "tokens": tokens, "usd": per_model_usd[model],
                    "breakdown": per_model_breakdown[model], "transcript": str(path),
                }
                if entry["role"] == "router":
                    li["router_marginal_usd"] = marginal_router_usd(li["breakdown"])
                line_items.append(li)

    for rec in decisions:
        if rec.get("router") == "claude":
            continue  # Claude-router decisions carry no Jev usage; their cost
                      # comes from a manifest transcript entry (role "router") above
        usage = rec.get("usage")
        if not usage:
            continue  # a failed or unparsed decision call carries no usage
        tokens = {
            "input_tokens": usage.get("input_tokens", 0) or 0,
            "output_tokens": usage.get("output_tokens", 0) or 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
        usd, breakdown, provider, prefix = price_message(
            JEV_MODEL, rec.get("decision_id") or f"task{rec.get('task')}", tokens,
            f"{run_dir}/decisions.json", pricing)
        line_items.append({
            "arm": "A", "task": rec.get("task"), "role": "router", "model": JEV_MODEL,
            "price_row": {"provider": provider, "prefix": prefix},
            "tokens": tokens, "usd": usd, "breakdown": breakdown,
            "call_type": rec.get("call_type"), "decision_id": rec.get("decision_id"),
            "router_marginal_usd": marginal_router_usd(breakdown),
        })

    aggregated = aggregate_line_items(line_items)

    return {
        "run": run_dir.name,
        "pricing_source": {p: {"source_url": d.get("source_url"), "retrieved": d.get("retrieved")}
                            for p, d in pricing.items()},
        "line_items": line_items,
        "per_task": aggregated["per_task"],
        "per_arm": aggregated["per_arm"],
        "per_arm_model": aggregated["per_arm_model"],
        "grand_total_usd": aggregated["grand_total_usd"],
        "cache_write_fallback": {
            model: {"message_count": s["count"], "tokens": s["tokens"]}
            for model, s in fallback_totals.items() if s["count"] > 0
        },
        "guardrails": {
            slot: {field: sorted(values, key=str) for field, values in fields.items()}
            for slot, fields in guardrail_values.items()
        },
    }


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run", required=True)
    p.add_argument("--runs-root", help=f"default: {DEFAULT_RUNS_ROOT}")
    p.add_argument("--pricing", help="default: pricing.json next to this script")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    runs_root = Path(args.runs_root).expanduser() if args.runs_root else DEFAULT_RUNS_ROOT
    run_dir = runs_root / args.run
    if not run_dir.is_dir():
        print(f"account: run {args.run!r} not found at {run_dir}", file=sys.stderr)
        return 1

    pricing_path = Path(args.pricing) if args.pricing else Path(__file__).resolve().parent / "pricing.json"
    if not pricing_path.exists():
        print(f"account: pricing file not found: {pricing_path}", file=sys.stderr)
        return 1
    with open(pricing_path, encoding="utf-8") as f:
        pricing = json.load(f)

    report = build_report(run_dir, pricing)
    out_path = run_dir / "costs.json"
    out_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    totals = ", ".join(f"{arm} ${usd:.4f}" for arm, usd in report["grand_total_usd"].items())
    print(f"account: wrote {out_path} (grand total: {totals})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
