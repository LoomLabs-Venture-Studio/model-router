"""C4 (#19) and C5 (#20, #21): decision ids, outcome existence checks, and stats
outcome history. Also D3 (PLAYBOOK.md "skill publish-hardening"): `show` on an
incomplete record.

Before this sprint's fix:
  C4: ids were "c-" + 6 hex chars (~52% collision odds by 5,000 decisions).
  C5 (#20): `outcome --id c-doesnotexist --result ok` printed "recorded" and
      exited 0 -- reproduced in test_outcome_for_an_unknown_id_is_rejected.
  C5 (#21): stats kept only the LAST outcome per decision, so a decision that was
      retried and then came back ok showed zero retries -- reproduced in
      test_stats_reports_a_retry_then_ok_decision_correctly.
  D3: `show` on a record missing an optional field (e.g. `scores_given`) died with a
      raw KeyError traceback and exit code 1 from the interpreter -- reproduced in
      test_show_on_a_record_missing_scores_given_reports_cleanly.
"""
from __future__ import annotations

import json
import re


# ----------------------------------------------------------------------------
# C4: 12-hex ids, and old 6-hex ids in an existing log still resolve
# ----------------------------------------------------------------------------
def test_new_decision_ids_are_twelve_hex_characters(run_cli):
    result = run_cli("route", "--scores", "S1 R1 A1 K1 I1 P1 V1", "--kind", "implement", "--task", "t")
    assert result.returncode == 0, result.stderr
    m = re.match(r"^\[c-([0-9a-f]+) ", result.stdout.strip())
    assert m, result.stdout
    assert len(m.group(1)) == 12


def test_smoke_command_shape_from_claude_md(run_cli):
    """The exact gate command CLAUDE.md and this sprint's gates specify."""
    result = run_cli("route", "--scores", "S3 R1 A1 K2 I3 P2 V2", "--kind", "review",
                      "--shards", "7", "--task", "t")
    assert result.returncode == 0, result.stderr
    out = result.stdout.strip()
    assert re.match(r"^\[c-[0-9a-f]{12} · S3R1A1K2I3P2V2 · fan-out 7xsonnet · verify:sonnet\]$", out), out


def test_show_resolves_an_old_style_six_hex_id_from_an_existing_log(run_cli, isolated_log):
    isolated_log.write_text(json.dumps({
        "event": "decision", "id": "c-abc123", "ts": "2026-01-01T00:00:00+00:00",
        "kind": "implement",
        "scores_given": {"S": 1, "R": 1, "A": 1, "K": 1, "I": 1, "P": 1, "V": 1}, "unsure": [],
        "scores_used": {"S": 1, "R": 1, "A": 1, "K": 1, "I": 1, "P": 1, "V": 1},
        "difficulty": 3, "tier": 0, "model": "haiku", "model_fallback": None,
        "mode": "inline", "mode_why": "x", "probe": False, "probe_why": [],
        "agent_type": "general-purpose", "shards": None, "shard_concurrency": None,
        "shard_model": None, "reduce": None, "verifier": None, "gate": None,
        "override": None, "after_probe": False, "notes": [], "project": "p", "task": "old task",
    }) + "\n", encoding="utf-8")
    result = run_cli("show", "c-abc123")
    assert result.returncode == 0, result.stderr
    assert "id          c-abc123" in result.stdout


def test_outcome_accepts_an_old_style_six_hex_id(run_cli, isolated_log):
    isolated_log.write_text(json.dumps({
        "event": "decision", "id": "c-abc123", "ts": "2026-01-01T00:00:00+00:00",
        "kind": "implement", "scores_used": {"S": 0}, "model": "haiku",
    }) + "\n", encoding="utf-8")
    result = run_cli("outcome", "--id", "c-abc123", "--result", "ok")
    assert result.returncode == 0, result.stderr
    assert "recorded ok for c-abc123" in result.stdout


def test_ids_are_matched_exactly_never_by_prefix(run_cli, isolated_log):
    """A short id must not accidentally match a longer one that starts with it --
    ids are looked up by exact equality only, on purpose (C4: no prefix matching,
    to keep a short id from becoming ambiguous)."""
    isolated_log.write_text(
        json.dumps({"event": "decision", "id": "c-abc123def456", "ts": "t",
                     "scores_used": {"S": 0}, "model": "haiku"}) + "\n",
        encoding="utf-8",
    )
    result = run_cli("show", "c-abc123")
    assert result.returncode != 0
    assert "no decision c-abc123" in (result.stdout + result.stderr)


def test_show_on_a_record_missing_scores_given_reports_cleanly(run_cli, isolated_log):
    """A record lacking an optional field (here `scores_given`, but `card()` reads many
    others by bracket access) must not die with a raw KeyError traceback: print a clean,
    one-line message and exit non-zero instead (D3, PLAYBOOK.md "skill
    publish-hardening")."""
    isolated_log.write_text(json.dumps({
        "event": "decision", "id": "c-incomplete01", "ts": "2026-01-01T00:00:00+00:00",
        "kind": "implement",
        # scores_given is missing entirely; scores_used is present so other fields
        # in card() that read it don't also blow up before we get to the missing one.
        "scores_used": {"S": 1, "R": 1, "A": 1, "K": 1, "I": 1, "P": 1, "V": 1},
        "unsure": [],
    }) + "\n", encoding="utf-8")
    result = run_cli("show", "c-incomplete01")
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    combined = result.stdout + result.stderr
    assert "c-incomplete01" in combined
    assert "incomplete record" in combined or "scores_given" in combined


def test_show_on_a_record_with_a_field_of_the_wrong_type_reports_cleanly(run_cli, isolated_log):
    """F12 (PLAYBOOK.md "skill publish-hardening", Task F): a field that IS
    present but the wrong type (here `shards` holding a string, which fails a
    `>` comparison against CONFIG["parallel_cap"] in card()) is a different
    problem from a MISSING field, and the message must say so plainly -- never
    the raw TypeError text ("'>' not supported between instances of 'str' and
    'int'"), and never claim the field is "missing" when it's very much
    present."""
    isolated_log.write_text(json.dumps({
        "event": "decision", "id": "c-badshards01", "ts": "2026-01-01T00:00:00+00:00",
        "kind": "review",
        "scores_given": {"S": 3, "R": 1, "A": 1, "K": 2, "I": 3, "P": 2, "V": 2}, "unsure": [],
        "scores_used": {"S": 3, "R": 1, "A": 1, "K": 2, "I": 3, "P": 2, "V": 2},
        "difficulty": 5, "tier": 1, "model": "sonnet", "model_fallback": None,
        "mode": "fan-out", "mode_why": "x", "probe": False, "probe_why": [],
        "agent_type": "general-purpose", "shards": "lots", "shard_concurrency": 2,
        "shard_model": "sonnet", "reduce": "x", "verifier": None, "gate": None,
        "override": None, "notes": [], "project": "p", "task": "t",
    }) + "\n", encoding="utf-8")
    result = run_cli("show", "c-badshards01")
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    combined = result.stdout + result.stderr
    assert "c-badshards01" in combined
    assert "'>' not supported" not in combined
    assert "missing" not in combined.lower()
    assert "wrong type" in combined.lower() or "malformed" in combined.lower()


# ----------------------------------------------------------------------------
# F12: `stats --last` takes a positive integer, same as `route --shards`
# ----------------------------------------------------------------------------
def test_stats_last_rejects_zero_negative_and_non_integer(run_cli):
    for bad in ("0", "-3", "abc"):
        result = run_cli("stats", "--last", bad)
        assert result.returncode != 0
        assert "Traceback" not in result.stderr


def test_stats_last_rejects_underscore_grouping_and_huge_values(run_cli):
    for bad in ("1_0", "999999999999"):
        result = run_cli("stats", "--last", bad)
        assert result.returncode != 0, f"{bad!r} should have been rejected"
        assert "Traceback" not in result.stderr


def test_jev_timeout_rejects_huge_values_but_accepts_a_sane_one(run_cli):
    over = run_cli("score", "--task", "t", "--jev-timeout", "999", "--no-log")
    assert over.returncode != 0
    assert "Traceback" not in over.stderr
    ok = run_cli("score", "--task", "t", "--jev-timeout", "120", "--no-log")
    assert ok.returncode == 0, ok.stderr


# ----------------------------------------------------------------------------
# C5 (#20): outcome rejects an unknown id
# ----------------------------------------------------------------------------
def test_outcome_for_an_unknown_id_is_rejected(run_cli):
    result = run_cli("outcome", "--id", "c-doesnotexist", "--result", "ok")
    assert result.returncode != 0
    assert "recorded" not in result.stdout
    assert "c-doesnotexist" in (result.stdout + result.stderr)


def test_outcome_for_a_real_id_still_works(run_cli):
    route_result = run_cli("route", "--scores", "S1 R1 A1 K1 I1 P1 V1", "--kind", "implement", "--task", "t")
    decision_id = re.match(r"^\[(c-[0-9a-f]+)", route_result.stdout.strip()).group(1)
    result = run_cli("outcome", "--id", decision_id, "--result", "ok")
    assert result.returncode == 0, result.stderr
    assert f"recorded ok for {decision_id}" in result.stdout


def test_outcome_lookup_falls_back_to_a_full_scan_beyond_the_tail_window(run_cli, isolated_log, route_mod):
    """An id logged long before the bounded tail window (see read_log_tail) must
    still be accepted: outcome checks the tail first, then falls back to a full
    scan before rejecting."""
    old_decision = json.dumps({"event": "decision", "id": "c-oldoldoldold", "ts": "t",
                                "scores_used": {"S": 0}, "model": "haiku", "pad": "x" * 2000})
    filler = [json.dumps({"event": "decision", "id": f"c-{i:012d}", "ts": "t",
                           "scores_used": {"S": 0}, "model": "haiku", "pad": "y" * 2000})
              for i in range(1000)]
    isolated_log.write_text(old_decision + "\n" + "\n".join(filler) + "\n", encoding="utf-8")

    # confirm the old id really has fallen out of the default bounded tail window
    tail_ids = {r["id"] for r in route_mod.read_log_tail()}
    assert "c-oldoldoldold" not in tail_ids, "test setup didn't actually push the id out of the tail"

    result = run_cli("outcome", "--id", "c-oldoldoldold", "--result", "ok")
    assert result.returncode == 0, result.stderr


# ----------------------------------------------------------------------------
# C5 (#21): outcome history, not just the last outcome
# ----------------------------------------------------------------------------
def test_stats_reports_a_retry_then_ok_decision_correctly(run_cli):
    route_result = run_cli("route", "--scores", "S1 R1 A1 K1 I1 P1 V1", "--kind", "implement", "--task", "t")
    decision_id = re.match(r"^\[(c-[0-9a-f]+)", route_result.stdout.strip()).group(1)
    run_cli("outcome", "--id", decision_id, "--result", "retry")
    run_cli("outcome", "--id", decision_id, "--result", "ok")

    result = run_cli("stats")
    assert result.returncode == 0, result.stderr
    tier_line = next(l for l in result.stdout.splitlines() if l.strip().startswith("haiku"))
    assert " 1 " in tier_line or tier_line.split()[1] == "1"  # n
    # the FINAL result table must show 1 ok, not 1 retry (the last event wins there)
    final_table = result.stdout.split("tier      n   rated   ok")[1].split("tier      ever_retry")[0]
    haiku_final = next(l for l in final_table.splitlines() if l.strip().startswith("haiku"))
    fields = haiku_final.split()
    # tier n rated ok retry escalated failed
    assert fields[3] == "1"  # ok
    assert fields[4] == "0"  # retry (final result only)
    # the ever_* table must still show the retry that happened along the way
    ever_table = result.stdout.split("tier      ever_retry")[1]
    haiku_ever = next(l for l in ever_table.splitlines() if l.strip().startswith("haiku"))
    ever_fields = haiku_ever.split()
    assert ever_fields[1] == "1"  # ever_retry


def test_stats_ever_escalated_and_ever_failed_columns(run_cli):
    route_result = run_cli("route", "--scores", "S1 R1 A1 K1 I1 P1 V1", "--kind", "implement", "--task", "t")
    decision_id = re.match(r"^\[(c-[0-9a-f]+)", route_result.stdout.strip()).group(1)
    run_cli("outcome", "--id", decision_id, "--result", "escalated")
    run_cli("outcome", "--id", decision_id, "--result", "failed")

    result = run_cli("stats")
    assert result.returncode == 0, result.stderr
    ever_table = result.stdout.split("tier      ever_retry")[1]
    haiku_ever = next(l for l in ever_table.splitlines() if l.strip().startswith("haiku"))
    fields = haiku_ever.split()
    assert fields[2] == "1"  # ever_escalated
    assert fields[3] == "1"  # ever_failed
    # and the final result (failed, the last event) is reflected in the first table
    final_table = result.stdout.split("tier      n   rated   ok")[1].split("tier      ever_retry")[0]
    haiku_final = next(l for l in final_table.splitlines() if l.strip().startswith("haiku"))
    assert haiku_final.split()[6] == "1"  # failed column


def test_stats_with_no_outcomes_reports_zero_across_both_tables(run_cli):
    run_cli("route", "--scores", "S1 R1 A1 K1 I1 P1 V1", "--kind", "implement", "--task", "t")
    result = run_cli("stats")
    assert result.returncode == 0, result.stderr
    assert "1 decisions, 0 with outcomes" in result.stdout
