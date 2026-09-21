"""selftest for account.py: message-id de-duplication (last occurrence wins),
per-model summing, exact USD pricing against a synthetic pricing file, cache-write
TTL splitting (with its mismatch hard error and no-breakdown fallback), the
inference_geo/speed/service_tier guard rails, the two original hard-error paths
(unknown model, null price for a used token type), Phase 2's router marginal
cost (per line item and per arm), the Claude-router decision skip in the Jev
decisions loop, and a regression against the real run r1's costs.json: its
grand totals must come out byte-identical after these changes."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import account

FIXTURES = Path(__file__).resolve().parent / "fixtures"

PRICING = {
    "testprov": {
        "source_url": "https://example.test/pricing",
        "retrieved": "2026-01-01",
        "prices": {
            "model-x": {"input": 10, "output": 20, "cache_write_5m": 5, "cache_write_1h": 10, "cache_read": 1},
            "model-y": {"input": 1, "output": 2, "cache_write_5m": 0.5, "cache_write_1h": 1, "cache_read": 0.1},
            "jev-latest": {"input": 0.1, "output": 0, "cache_write_5m": None, "cache_write_1h": None, "cache_read": None},
        },
    }
}


def _write_transcript(path) -> None:
    records = [
        # m1, first (streamed) chunk -- superseded by the next line
        {"type": "assistant", "message": {"id": "m1", "model": "model-x",
         "usage": {"input_tokens": 100, "output_tokens": 20,
                   "cache_creation_input_tokens": 10, "cache_read_input_tokens": 5}}},
        # m1, final chunk (no TTL breakdown -> priced via the 5m fallback) -- this is the one that must be counted
        {"type": "assistant", "message": {"id": "m1", "model": "model-x",
         "usage": {"input_tokens": 100, "output_tokens": 50,
                   "cache_creation_input_tokens": 10, "cache_read_input_tokens": 5}}},
        # non-assistant record: ignored
        {"type": "user", "message": {"id": "u1", "content": "hi"}},
        # assistant record with no usage block: ignored
        {"type": "assistant", "message": {"id": "m2", "model": "model-x"}},
        # a second model
        {"type": "assistant", "message": {"id": "m3", "model": "model-y",
         "usage": {"input_tokens": 200, "output_tokens": 80,
                   "cache_creation_input_tokens": 0, "cache_read_input_tokens": 100}}},
    ]
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_parse_transcript_dedupes_by_last_occurrence(tmp_path):
    transcript = tmp_path / "t.jsonl"
    _write_transcript(transcript)

    messages = account.parse_transcript(transcript)

    assert set(messages) == {"m1", "m3"}  # m2 has no usage, the user record is ignored
    model, usage = messages["m1"]
    assert model == "model-x"
    assert usage == {"input_tokens": 100, "output_tokens": 50,
                      "cache_creation_input_tokens": 10, "cache_read_input_tokens": 5}


def test_account_sums_tokens_and_prices_exactly(tmp_path):
    transcript = tmp_path / "t.jsonl"
    _write_transcript(transcript)

    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    manifest = {"A/task1": [{"arm": "A", "task": 1, "role": "engineer", "transcript": str(transcript)}]}
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    decisions = [{"task": 1, "arm": "A", "call_type": "initial", "decision_id": "c-abcdef",
                  "usage": {"input_tokens": 1000, "output_tokens": 0}}]
    (run_dir / "decisions.json").write_text(json.dumps(decisions), encoding="utf-8")

    report = account.build_report(run_dir, PRICING)
    by_model = {li["model"]: li for li in report["line_items"]}

    assert by_model["model-x"]["tokens"] == {
        "input_tokens": 100, "output_tokens": 50,
        "cache_creation_input_tokens": 10, "cache_read_input_tokens": 5,
    }
    # 100*10 + 50*20 + 10*5 (cache_write_5m, via the no-breakdown fallback) + 5*1, all /1e6
    assert by_model["model-x"]["usd"] == pytest.approx(0.002055)

    assert by_model["model-y"]["tokens"] == {
        "input_tokens": 200, "output_tokens": 80,
        "cache_creation_input_tokens": 0, "cache_read_input_tokens": 100,
    }
    # 200*1 + 80*2 + 100*0.1, all /1e6 (cache_write skipped: 0 tokens)
    assert by_model["model-y"]["usd"] == pytest.approx(0.00037)

    assert by_model["jev-latest"]["role"] == "router"
    assert by_model["jev-latest"]["usd"] == pytest.approx(0.0001)  # 1000*0.1/1e6

    total = 0.002055 + 0.00037 + 0.0001
    assert report["per_task"]["1"]["A"]["usd"] == pytest.approx(total)
    assert report["per_arm"]["A"]["usd"] == pytest.approx(total)
    assert report["grand_total_usd"]["A"] == pytest.approx(total)
    assert "B" not in report["grand_total_usd"]

    # m1's cache write had no TTL breakdown, so it must show up as a recorded fallback
    assert report["cache_write_fallback"]["model-x"] == {"message_count": 1, "tokens": 10}
    assert "model-y" not in report["cache_write_fallback"]  # 0 cache_creation tokens, never triggered


def test_unknown_model_is_a_hard_error():
    with pytest.raises(SystemExit, match="no pricing entry matches"):
        account.price_message("nonexistent-model", "m1", {"input_tokens": 1}, "t.jsonl", PRICING)


def test_null_price_for_a_used_token_type_is_a_hard_error():
    usage = {"input_tokens": 0, "output_tokens": 0,
              "cache_creation_input_tokens": 5, "cache_read_input_tokens": 0}
    with pytest.raises(SystemExit, match="no 'cache_write_5m' price"):
        account.price_message("jev-latest", "m1", usage, "t.jsonl", PRICING)


def test_cache_write_split_by_ttl_prices_at_two_different_rates():
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
              "cache_creation_input_tokens": 30,
              "cache_creation": {"ephemeral_5m_input_tokens": 10, "ephemeral_1h_input_tokens": 20}}
    fallback = {"count": 0, "tokens": 0}

    usd, breakdown, _, _ = account.price_message("model-x", "m9", usage, "t.jsonl", PRICING, fallback)

    assert usd == pytest.approx(10 * 5 / 1e6 + 20 * 10 / 1e6)
    assert breakdown["cache_write_5m_tokens"]["tokens"] == 10
    assert breakdown["cache_write_1h_tokens"]["tokens"] == 20
    assert fallback == {"count": 0, "tokens": 0}  # breakdown was present: no fallback used


def test_cache_creation_breakdown_mismatch_is_a_hard_error():
    usage = {"cache_creation_input_tokens": 30,
              "cache_creation": {"ephemeral_5m_input_tokens": 10, "ephemeral_1h_input_tokens": 5}}
    with pytest.raises(SystemExit, match="does not match cache_creation_input_tokens"):
        account.price_message("model-x", "m9", usage, "t.jsonl", PRICING)


def test_missing_breakdown_falls_back_to_5m_rate_and_is_recorded():
    fallback = {"count": 0, "tokens": 0}
    usage = {"cache_creation_input_tokens": 40}  # no "cache_creation" breakdown key at all

    usd, breakdown, _, _ = account.price_message("model-x", "m2", usage, "t.jsonl", PRICING, fallback)

    assert usd == pytest.approx(40 * 5 / 1e6)
    assert breakdown["cache_write_5m_tokens"]["tokens"] == 40
    assert fallback == {"count": 1, "tokens": 40}


def test_inference_geo_us_is_a_hard_error():
    with pytest.raises(SystemExit, match="inference_geo='us'"):
        account.check_guard_value("inference_geo", "us", "model-x", "m1", "t.jsonl", set())


def test_speed_fast_is_a_hard_error():
    with pytest.raises(SystemExit, match="speed='fast'"):
        account.check_guard_value("speed", "fast", "model-x", "m1", "t.jsonl", set())


def test_batch_service_tier_is_a_hard_error():
    with pytest.raises(SystemExit, match="service_tier='batch'"):
        account.check_guard_value("service_tier", "batch", "model-x", "m1", "t.jsonl", set())


def test_unrecognised_guard_value_warns_but_does_not_fail(capsys):
    warned = set()
    account.check_guard_value("service_tier", "priority", "model-x", "m1", "t.jsonl", warned)
    assert ("service_tier", "priority") in warned
    assert "unrecognised service_tier='priority'" in capsys.readouterr().err


def test_guardrails_are_collected_per_slot_through_build_report(tmp_path, capsys):
    records = [
        {"type": "assistant", "message": {"id": "g1", "model": "model-x",
         "usage": {"input_tokens": 5, "output_tokens": 5,
                   "service_tier": "standard", "inference_geo": "global", "speed": "standard"}}},
        {"type": "assistant", "message": {"id": "g2", "model": "model-x",
         "usage": {"input_tokens": 5, "output_tokens": 5, "service_tier": "priority"}}},
    ]
    transcript = tmp_path / "g.jsonl"
    transcript.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    run_dir = tmp_path / "run_g"
    run_dir.mkdir()
    manifest = {"A/task1": [{"arm": "A", "task": 1, "role": "engineer", "transcript": str(transcript)}]}
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run_dir / "decisions.json").write_text("[]", encoding="utf-8")

    report = account.build_report(run_dir, PRICING)

    assert report["guardrails"]["A/task1"]["service_tier"] == ["priority", "standard"]
    assert report["guardrails"]["A/task1"]["inference_geo"] == ["global"]
    assert report["guardrails"]["A/task1"]["speed"] == ["standard"]
    assert "unrecognised service_tier='priority'" in capsys.readouterr().err


def test_inference_geo_us_hard_errors_through_build_report(tmp_path):
    records = [{"type": "assistant", "message": {"id": "bad1", "model": "model-x",
                 "usage": {"input_tokens": 1, "output_tokens": 1, "inference_geo": "us"}}}]
    transcript = tmp_path / "bad.jsonl"
    transcript.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    run_dir = tmp_path / "run_bad"
    run_dir.mkdir()
    manifest = {"A/task1": [{"arm": "A", "task": 1, "role": "engineer", "transcript": str(transcript)}]}
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run_dir / "decisions.json").write_text("[]", encoding="utf-8")

    with pytest.raises(SystemExit, match="inference_geo='us'"):
        account.build_report(run_dir, PRICING)


# ----------------------------------------------------------------------------
# Phase 2: router marginal cost, and the r1 regression
# ----------------------------------------------------------------------------
def test_marginal_router_usd_excludes_cache():
    breakdown = {
        "input_tokens": {"tokens": 500, "price_per_mtok": 10, "usd": 0.005},
        "output_tokens": {"tokens": 200, "price_per_mtok": 50, "usd": 0.01},
        "cache_read_input_tokens": {"tokens": 2000, "price_per_mtok": 0.25, "usd": 0.0005},
        "cache_write_5m_tokens": {"tokens": 1000, "price_per_mtok": 12.5, "usd": 0.0125},
    }
    assert account.marginal_router_usd(breakdown) == pytest.approx(0.005 + 0.01)


def test_marginal_router_usd_equals_full_when_no_cache_present():
    # Jev never has cache tokens, so its marginal cost is always its full cost.
    breakdown = {
        "input_tokens": {"tokens": 1759, "price_per_mtok": 0.042, "usd": 0.000073878},
        "output_tokens": {"tokens": 144, "price_per_mtok": 0, "usd": 0.0},
    }
    assert account.marginal_router_usd(breakdown) == pytest.approx(0.000073878)


def _write_router_transcript(path, model="claude-fable-5-1") -> None:
    records = [{"type": "assistant", "message": {"id": "r1", "model": model,
                "usage": {"input_tokens": 500, "output_tokens": 200,
                          "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 2000}}}]
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


ROUTER_PRICING = {
    "anthropic": {"source_url": "x", "retrieved": "y", "prices": {
        "claude-fable-5-1": {"input": 10, "output": 50, "cache_write_5m": 12.5,
                              "cache_write_1h": 20, "cache_read": 0.25},
    }},
}


def test_manifest_router_transcript_gets_marginal_and_full_cost_per_line_item(tmp_path):
    transcript = tmp_path / "router.jsonl"
    _write_router_transcript(transcript)
    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    manifest = {"A/task3": [{"arm": "A", "task": 3, "role": "router", "transcript": str(transcript)}]}
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run_dir / "decisions.json").write_text("[]", encoding="utf-8")

    report = account.build_report(run_dir, ROUTER_PRICING)
    li = report["line_items"][0]

    assert li["role"] == "router"
    assert li["usd"] == pytest.approx(500 * 10 / 1e6 + 200 * 50 / 1e6 + 2000 * 0.25 / 1e6 + 1000 * 12.5 / 1e6)
    assert li["router_marginal_usd"] == pytest.approx(500 * 10 / 1e6 + 200 * 50 / 1e6)
    assert li["router_marginal_usd"] < li["usd"]  # cache excluded, so strictly less

    assert report["per_arm"]["A"]["router_full_usd"] == pytest.approx(li["usd"])
    assert report["per_arm"]["A"]["router_marginal_usd"] == pytest.approx(li["router_marginal_usd"])


def test_claude_router_decision_carries_no_jev_usage_and_is_skipped(tmp_path):
    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
    decisions = [
        {"task": 1, "arm": "A", "call_type": "initial", "decision_id": "c-jev1",
         "usage": {"input_tokens": 1000, "output_tokens": 100}},  # Jev: has usage, is priced
        {"task": 2, "arm": "A", "call_type": "initial", "router": "claude",
         "model": "sonnet", "mode": "agent", "shards": 1, "probe_first": False,
         "verifier": None, "confidence": 0.9, "reason": "ok"},  # Claude: no usage, skipped
    ]
    (run_dir / "decisions.json").write_text(json.dumps(decisions), encoding="utf-8")

    report = account.build_report(run_dir, PRICING)
    jev_items = [li for li in report["line_items"] if li["model"] == "jev-latest"]
    assert len(jev_items) == 1
    assert jev_items[0]["task"] == 1


def test_r1_grand_totals_are_unchanged_by_the_router_marginal_cost_addition():
    """Regression: feeds r1's REAL line_items (from the trimmed real-shape
    fixture) through the current aggregation and checks the grand totals come
    out byte-identical to what shipped in Phase 1's report -- adding
    router_full_usd/router_marginal_usd must not perturb the existing usd
    totals anywhere in the roll-up."""
    costs = json.loads((FIXTURES / "costs.json").read_text(encoding="utf-8"))
    aggregated = account.aggregate_line_items(costs["line_items"])

    assert aggregated["grand_total_usd"]["A"] == 3.4739382619999994
    assert aggregated["grand_total_usd"]["B"] == 9.09328025

    # router_full_usd must equal the sum of the real router-role line items' usd
    expected_router_full = sum(li["usd"] for li in costs["line_items"]
                                if li["arm"] == "A" and li["role"] == "router")
    assert aggregated["per_arm"]["A"]["router_full_usd"] == pytest.approx(expected_router_full)
    # the fixture predates this change, so its Jev line items have no
    # router_marginal_usd field; aggregate_line_items must default it to 0.0
    # rather than raising, so this pre-existing costs.json can still be re-run
    assert aggregated["per_arm"]["A"]["router_marginal_usd"] == 0.0
