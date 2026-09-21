"""A1: pins SKILL.md's three worked examples exactly, end to end (parse_scores() then
decide()), plus a handful of CLI-level integration checks against the real script --
the gate command from PLAYBOOK.md's sprint gates, and the A5 shard-concurrency and
A3 after-probe reproductions, run through the actual subprocess rather than just the
in-process functions covered in test_decide.py.
"""
from __future__ import annotations

import re
from pathlib import Path


# ----------------------------------------------------------------------------
# Worked example 1: "Rename getUser to fetchUser everywhere."
# ----------------------------------------------------------------------------
def test_example_1_rename_in_a_large_repo(route_mod):
    scores, unsure = route_mod.parse_scores("S2 R0 A0 K1 I2 P0 V1")
    d = route_mod.decide(scores, unsure, "implement")
    assert d["difficulty"] == 1
    assert d["model"] == "haiku"
    assert d["mode"] == "single"
    assert d["verifier"] is None


def test_example_1_rename_in_a_small_repo_is_inline(route_mod):
    # "In a 150-line repo the same task is S1 and the router says inline."
    scores, unsure = route_mod.parse_scores("S1 R0 A0 K1 I2 P0 V1")
    d = route_mod.decide(scores, unsure, "implement")
    assert d["mode"] == "inline"


# ----------------------------------------------------------------------------
# Worked example 2: "Intermittent 500s on checkout under load."
# ----------------------------------------------------------------------------
def test_example_2_checkout_500s_probes_first(route_mod):
    scores, unsure = route_mod.parse_scores("S2? R3 A2 K2 I1 P0 V2")
    d = route_mod.decide(scores, unsure, "implement")
    assert d["difficulty"] == 7
    assert d["model"] == "opus"
    assert d["mode"] == "probe"
    assert d["verifier"] == "opus"
    assert d["gate"] is None  # K2, not K3: a review note, not a formal gate
    card = route_mod.card(d, None, None)
    assert "K2: changes ship through review, no direct deploy" in card


def test_example_2_checkout_500s_after_probe_s_still_2_is_single_agent(route_mod):
    # Corrected per A7: S>=2 keeps it a single agent regardless of I, even after
    # the probe confirms the subsystem-sized scope. The old SKILL.md text claimed
    # this stayed "inline" because of I1, which the code has never actually done.
    scores, unsure = route_mod.parse_scores("S2 R3 A2 K2 I1 P0 V2")
    d = route_mod.decide(scores, unsure, "implement", after_probe=True)
    assert d["mode"] == "single"
    assert d["probe"] is False


def test_example_2_checkout_500s_after_probe_s_resolved_to_1_is_inline(route_mod):
    scores, unsure = route_mod.parse_scores("S1 R3 A2 K2 I1 P0 V2")
    d = route_mod.decide(scores, unsure, "implement", after_probe=True)
    assert d["mode"] == "inline"
    assert d["probe"] is False


# ----------------------------------------------------------------------------
# Worked example 3: "Audit all route handlers for missing auth checks."
# ----------------------------------------------------------------------------
def test_example_3_audit_all_handlers(route_mod):
    scores, unsure = route_mod.parse_scores("S3 R1 A1 K2 I3 P3 V2")
    d = route_mod.decide(scores, unsure, "review", shards=6)
    assert d["difficulty"] == 5  # 1+1+2, +1 for S==3
    assert d["model"] == "sonnet"
    assert d["mode"] == "fan-out"
    assert d["shards"] == 6
    assert d["shard_model"] == "sonnet"  # one tier down would be haiku, but K2 floors it
    assert d["verifier"] == "sonnet"


def test_example_3_audit_default_shard_count_is_8(route_mod):
    scores, unsure = route_mod.parse_scores("S3 R1 A1 K2 I3 P3 V2")
    d = route_mod.decide(scores, unsure, "review")  # no --shards: default for P3
    assert d["shards"] == 8


# ----------------------------------------------------------------------------
# CLI-level integration: the smoke gate from CLAUDE.md / PLAYBOOK.md, verbatim
# ----------------------------------------------------------------------------
def test_smoke_gate_command_unchanged_shape(run_cli):
    proc = run_cli("route", "--scores", "S3 R1 A1 K2 I3 P2 V2", "--kind", "review",
                    "--shards", "7", "--task", "t")
    assert proc.returncode == 0
    assert "fan-out 7xsonnet" in proc.stdout
    assert "verify:sonnet" in proc.stdout
    assert re.match(r"\[c-[0-9a-f]+ · S3R1A1K2I3P2V2 · fan-out 7xsonnet · verify:sonnet\]", proc.stdout.strip())


def test_cli_shards_above_cap_reports_concurrency_in_one_liner(run_cli):
    proc = run_cli("route", "--scores", "S3 R1 A1 K2 I3 P2 V2", "--kind", "review",
                    "--shards", "12", "--no-log")
    assert proc.returncode == 0
    assert "fan-out 12xsonnet (8 at a time)" in proc.stdout


def test_cli_shards_above_cap_reports_concurrency_in_card(run_cli):
    proc = run_cli("route", "--scores", "S3 R1 A1 K2 I3 P2 V2", "--kind", "review",
                    "--shards", "12", "--no-log", "--explain")
    assert proc.returncode == 0
    assert "12 shards" in proc.stdout
    assert "8 at a time" in proc.stdout


def test_cli_after_probe_never_prints_probe(run_cli):
    proc = run_cli("route", "--after-probe", "--scores", "S2 R2 A1 K2 I0 P0 V2",
                    "--kind", "implement", "--no-log")
    assert proc.returncode == 0
    assert "probe" not in proc.stdout.lower()


def test_cli_bad_shards_exits_nonzero_no_traceback(run_cli):
    for bad in ("0", "-5", "abc"):
        proc = run_cli("route", "--scores", "S1 R1 A1 K1 I2 P2 V1", "--kind", "implement",
                        "--shards", bad, "--no-log")
        assert proc.returncode != 0
        assert "Traceback" not in proc.stderr


# ----------------------------------------------------------------------------
# F12 addition (team-lead, QA slice 1): positive_int accepts plain ASCII digits
# only (never Python's underscore-grouping form) and caps at a plausible max.
# ----------------------------------------------------------------------------
def test_cli_shards_rejects_underscore_grouping_and_huge_values(run_cli):
    for bad in ("1_0", "999999999999", "1_000"):
        proc = run_cli("route", "--scores", "S1 R1 A1 K1 I2 P2 V1", "--kind", "implement",
                        "--shards", bad, "--no-log")
        assert proc.returncode != 0, f"{bad!r} should have been rejected"
        assert "Traceback" not in proc.stderr


def test_cli_shards_accepts_the_cap_but_not_one_above_it(run_cli):
    ok = run_cli("route", "--scores", "S1 R1 A1 K1 I2 P2 V1", "--kind", "implement",
                 "--shards", "1000", "--no-log")
    assert ok.returncode == 0, ok.stderr
    over = run_cli("route", "--scores", "S1 R1 A1 K1 I2 P2 V1", "--kind", "implement",
                    "--shards", "1001", "--no-log")
    assert over.returncode != 0
    assert "Traceback" not in over.stderr


# ----------------------------------------------------------------------------
# F12 (PLAYBOOK.md "skill publish-hardening", Task F): SKILL.md's sample card
# ("What the user sees") must match what `show` really prints for the audit
# example -- before this fix it omitted the "why" line entirely and truncated
# the "reduce:" text, so a reader copying the sample would see a card shaped
# differently from the real one.
# ----------------------------------------------------------------------------
def test_skill_md_sample_card_matches_real_output(run_cli):
    proc = run_cli("route", "--scores", "S3 R1 A1 K2 I3 P2 V2", "--kind", "review",
                    "--shards", "7", "--no-log", "--explain")
    assert proc.returncode == 0, proc.stderr
    real_lines = {ln.split(None, 1)[0]: ln for ln in proc.stdout.splitlines() if ln.strip()}

    skill_md = (Path(__file__).resolve().parents[2] / "skills" / "complexity" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    fence_start = skill_md.index("which prints the full card:")
    block = skill_md[fence_start:].split("```")[1]
    sample_lines = {ln.split(None, 1)[0]: ln for ln in block.strip("\n").splitlines() if ln.strip()}

    # Every field the sample shows must exist in the real card, with identical
    # text -- except "id", whose id and log path are necessarily specific to a
    # single logged decision and not reproducible byte for byte here.
    assert set(sample_lines) - {"id"} == set(real_lines) - {"id"}, (
        f"SKILL.md's sample card has different fields than the real one: "
        f"sample={sorted(sample_lines)} real={sorted(real_lines)}"
    )
    for field, sample_line in sample_lines.items():
        if field == "id":
            continue
        assert sample_line == real_lines[field], (
            f"SKILL.md's sample '{field}' line doesn't match real output:\n"
            f"  sample: {sample_line!r}\n"
            f"  real:   {real_lines[field]!r}"
        )
