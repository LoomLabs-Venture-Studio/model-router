"""A1: pins the documented routing policy (SKILL.md, references/routing-policy.md,
references/rubric.md) against the real decide()/parse_scores() functions, table-driven.
Also covers the A3/A4/A5 bug fixes at the decide()-level (the CLI-level reproductions
of the same bugs are in test_parse.py and test_examples.py).

Every test builds scores with parse_scores() (or a plain dict, when a whole-number
combination is being swept) and calls decide() -- never a re-implementation of the
policy.
"""
from __future__ import annotations

import os

import pytest


def base_scores(**overrides):
    """S0 R0 A0 K0 I0 P0 V0, with overrides. I0 alone would force mode=inline for
    every case, so tests that care about mode default I to 2 (cleanly briefable)
    unless they are specifically testing the I dimension."""
    s = {"S": 0, "R": 0, "A": 0, "K": 0, "I": 2, "P": 0, "V": 0}
    s.update(overrides)
    return s


# ----------------------------------------------------------------------------
# Difficulty and tier bands
# ----------------------------------------------------------------------------
@pytest.mark.parametrize("r,a,k,s_score,expected_difficulty", [
    (0, 0, 0, 0, 0),
    (1, 1, 0, 0, 2),
    (2, 2, 2, 0, 6),
    (0, 0, 0, 3, 1),      # S==3 adds 1 to difficulty
    (3, 3, 3, 3, 9),      # capped at 9 even though 3+3+3+1=10
])
def test_difficulty_formula(route_mod, r, a, k, s_score, expected_difficulty):
    scores = base_scores(R=r, A=a, K=k, S=s_score, V=2)  # V=2 avoids the test-loop discount
    d = route_mod.decide(scores, set(), "implement")
    assert d["difficulty"] == expected_difficulty


@pytest.mark.parametrize("difficulty,expected_tier", [
    (0, 0), (2, 0),   # haiku
    (3, 1), (5, 1),   # sonnet
    (6, 2), (7, 2),   # opus
    (8, 3), (9, 3),   # fable
])
def test_tier_bands(route_mod, difficulty, expected_tier):
    assert route_mod.tier_for(difficulty) == expected_tier


def test_tier_bands_cover_config(route_mod):
    # Pin the bands themselves, not just tier_for's behavior, so a CONFIG edit that
    # narrows a band is caught here rather than downstream.
    assert route_mod.CONFIG["bands"] == [(0, 2, 0), (3, 5, 1), (6, 7, 2), (8, 9, 3)]
    assert route_mod.CONFIG["tiers"] == ["haiku", "sonnet", "opus", "fable"]


def test_config_pinned_exactly(route_mod):
    """Blanket safety net for the mutation-testing pass (PLAYBOOK.md, "skill
    publish-hardening" sprint, CTO review of A1): every other test in this file
    exercises SOME constant behaviorally, but two are structurally out of reach
    of any behavioral test through decide()/parse_scores() alone:

      - jev_unsure_below is only read by score_jev(), which makes a network call
        (never exercised by this suite -- see conftest.py and PLAYBOOK.md's ban
        on tests touching the network or .env).
      - verifier_min_tier["K3"] (2) currently equals risk_floor[3] (2): K==3
        already forces tier >= 2 before the verifier rule runs, so LOWERING
        verifier_min_tier["K3"] has no observable effect via decide() at all
        (see test_verifier_k3_minimum_pinned_exactly for the direction that IS
        reachable: raising it).

    A direct equality check on the whole CONFIG dict catches a change to either
    of those -- and to every other constant, as a second, independent check
    alongside the behavioral tests above -- even when no routing decision would
    visibly differ. This test asserts VALUES, not that the policy behaves
    correctly; the behavioral tests above are what should fail first and explain
    why, when either kind of test does fail.
    """
    assert route_mod.CONFIG == {
        "tiers": ["haiku", "sonnet", "opus", "fable"],
        "fallback": {"fable": "opus"},
        "bands": [(0, 2, 0), (3, 5, 1), (6, 7, 2), (8, 9, 3)],
        "test_loop": {"V": 1, "K": 1, "R": 1},
        "risk_floor": {2: 1, 3: 2},
        "verifier_min_tier": {"default": 1, "K3": 2},
        "parallel_cap": 8,
        "shard_floor_when_risky": 1,
        "shards_by_P": {1: 3, 2: 6, 3: 8},
        "round_up": ["R", "A", "K", "V"],
        "jev_unsure_below": 0.6,
        "log_env": "COMPLEXITY_LOG",
        "log_default": os.path.join("~", ".claude", "complexity-router", "decisions.jsonl"),
    }


# ----------------------------------------------------------------------------
# Test-loop discount
# ----------------------------------------------------------------------------
def test_test_loop_discount_applies(route_mod):
    # R1 A1 K1 -> difficulty 3 -> sonnet; V<=1, K<=1, R<=1 -> discount to haiku.
    scores = base_scores(R=1, A=1, K=1, V=1)
    d = route_mod.decide(scores, set(), "implement")
    assert d["tier"] == 0
    assert any("test-loop discount" in n for n in d["notes"])


def test_test_loop_discount_never_below_tier_0(route_mod):
    scores = base_scores(R=0, A=0, K=0, V=0)  # already haiku
    d = route_mod.decide(scores, set(), "implement")
    assert d["tier"] == 0
    assert not any("test-loop discount" in n for n in d["notes"])


@pytest.mark.parametrize("r,k,v", [(2, 1, 1), (1, 2, 1), (1, 1, 2)])
def test_test_loop_discount_requires_all_three_low(route_mod, r, k, v):
    scores = base_scores(R=r, A=1, K=k, V=v)
    d = route_mod.decide(scores, set(), "implement")
    assert not any("test-loop discount" in n for n in d["notes"])


# ----------------------------------------------------------------------------
# Risk floors and the K3 gate
# ----------------------------------------------------------------------------
def test_risk_floor_k2_at_least_sonnet(route_mod):
    # Exact equality, not >=: a mutation that over-floors K2 to opus must fail this
    # too, not just one that removes the floor entirely.
    scores = base_scores(R=0, A=0, K=2, V=0)  # difficulty 2 -> haiku band, floored up
    d = route_mod.decide(scores, set(), "implement")
    assert d["tier"] == 1
    assert d["model"] == "sonnet"


def test_risk_floor_k2_does_not_bind_when_band_already_at_or_above_sonnet(route_mod):
    # Contrast case: without the floor, difficulty 2 alone would stay haiku (see
    # test_tier_bands). The floor is what moves it -- pin the *other* side too, so a
    # mutation that always forces sonnet (independent of the band) is also visible:
    # difficulty 4 is already sonnet from the band, K1 shouldn't need any floor.
    scores = base_scores(R=2, A=1, K=1, V=2)  # difficulty 4, K<2: no floor involved
    d = route_mod.decide(scores, set(), "implement")
    assert d["tier"] == 1
    assert d["model"] == "sonnet"


@pytest.mark.parametrize("r,a,expected_difficulty", [
    (0, 0, 3),   # bottom of the sonnet band before the floor
    (2, 0, 5),   # top of the sonnet band before the floor
])
def test_risk_floor_k3_at_least_opus_from_sonnet_band(route_mod, r, a, expected_difficulty):
    # K=3 forces difficulty >= 3 by construction (difficulty = R+A+K), so a K=3 task
    # can never land in the haiku band (0-2) in the first place -- the floor's only
    # reachable job is lifting a sonnet-band difficulty (3-5) up to opus.
    scores = base_scores(R=r, A=a, K=3, V=0)
    d = route_mod.decide(scores, set(), "implement")
    assert d["difficulty"] == expected_difficulty
    assert d["tier"] == 2
    assert d["model"] == "opus"
    assert d["gate"] == "confirm with the user before any irreversible step"


def test_k3_v3_gate_is_human_sign_off(route_mod):
    scores = base_scores(K=3, V=3)
    d = route_mod.decide(scores, set(), "implement")
    assert "human sign-off" in d["gate"]


def test_k2_no_formal_gate_but_review_note_in_card(route_mod):
    scores = base_scores(K=2, V=0)
    d = route_mod.decide(scores, set(), "implement")
    assert d["gate"] is None
    card = route_mod.card(d, None, None)
    assert "K2: changes ship through review, no direct deploy" in card


# ----------------------------------------------------------------------------
# Verifier rule
# ----------------------------------------------------------------------------
@pytest.mark.parametrize("k,v,expect_verifier", [
    (0, 0, False), (1, 1, False),
    (2, 2, True),      # K>=2 and V>=2
    (2, 1, False),     # K>=2 but V<2
    (0, 3, True),      # V==3 alone is enough
])
def test_verifier_added_when_documented(route_mod, k, v, expect_verifier):
    scores = base_scores(K=k, V=v)
    d = route_mod.decide(scores, set(), "implement")
    assert bool(d["verifier"]) is expect_verifier


def test_verifier_minimum_tier_is_sonnet(route_mod):
    scores = base_scores(K=2, V=2)  # difficulty low -> haiku band before the floor
    d = route_mod.decide(scores, set(), "implement")
    assert d["verifier"] == "sonnet"


def test_verifier_default_minimum_isolated_from_the_risk_floor(route_mod):
    # verifier_min_tier["default"] applies via (K>=2 and V>=2) OR V==3. The case above
    # (K=2, V=2) also satisfies K's own risk floor, which independently forces tier to
    # sonnet -- so that test can't tell the verifier minimum apart from the risk floor.
    # V==3 alone triggers the verifier without K>=2, so with K=0 (no risk floor at all)
    # and a haiku-band difficulty, only verifier_min_tier["default"] can be raising the
    # verifier to sonnet here.
    scores = base_scores(R=0, A=0, K=0, V=3)
    d = route_mod.decide(scores, set(), "implement")
    assert d["tier"] == 0          # haiku band, no risk floor (K=0)
    assert d["verifier"] == "sonnet"


def test_verifier_minimum_tier_opus_when_k3(route_mod):
    scores = base_scores(K=3, V=2)
    d = route_mod.decide(scores, set(), "implement")
    assert d["verifier"] == "opus"


def test_verifier_k3_minimum_pinned_exactly(route_mod):
    # K==3's own risk floor already guarantees tier >= 2 (opus) before the verifier
    # rule runs, and verifier_min_tier["K3"] (2) currently equals that floor, so it
    # can only ever RAISE the verifier further, never lower it: a mutation raising
    # verifier_min_tier["K3"] to 3 would turn this "opus" into "fable" and this
    # assertion would catch it. A mutation LOWERING it (to 0 or 1) has no observable
    # effect via decide() at all -- the risk floor already dominates -- and is only
    # caught by the CONFIG snapshot test below, at the value level, not behaviorally.
    scores = base_scores(R=0, A=0, K=3, V=2)  # difficulty 3 -> risk-floored to opus (tier 2)
    d = route_mod.decide(scores, set(), "implement")
    assert d["tier"] == 2
    assert d["verifier"] == "opus"


# ----------------------------------------------------------------------------
# Unsure handling
# ----------------------------------------------------------------------------
@pytest.mark.parametrize("dim", ["R", "A", "K", "V"])
def test_unsure_rounds_up(route_mod, dim):
    scores = base_scores(**{dim: 1})
    d = route_mod.decide(scores, {dim}, "implement")
    assert d["scores_used"][dim] == 2
    assert any("rounded up" in n for n in d["notes"])


def test_unsure_round_up_caps_at_3(route_mod):
    scores = base_scores(K=3)
    d = route_mod.decide(scores, {"K"}, "implement")
    assert d["scores_used"]["K"] == 3


@pytest.mark.parametrize("given_i", [0, 1, 2, 3])
def test_unsure_i_always_treated_as_1(route_mod, given_i):
    """A4: an unsure I is treated as 1, regardless of the given value -- not
    min(given, 1), which left I0? at 0 and never raised a low guess."""
    scores = base_scores(I=given_i, P=2)  # P2 so mode differs visibly between I0 and I1
    d = route_mod.decide(scores, {"I"}, "implement")
    assert d["scores_used"]["I"] == 1


def test_unsure_i0_no_longer_stays_inline(route_mod):
    # Before the fix, "S1 R1 A1 K1 I0? P2 V1" stayed inline because min(0, 1) == 0.
    scores = base_scores(S=1, R=1, A=1, K=1, I=0, P=2, V=1)
    d = route_mod.decide(scores, {"I"}, "implement")
    assert d["scores_used"]["I"] == 1
    assert d["mode"] == "fan-out"


def test_unsure_s_triggers_probe(route_mod):
    scores = base_scores(S=2)
    d = route_mod.decide(scores, {"S"}, "implement")
    assert d["mode"] == "probe"
    assert d["probe"] is True


@pytest.mark.parametrize("p", [2, 3])
def test_unsure_p_at_2_or_more_triggers_probe(route_mod, p):
    scores = base_scores(P=p)
    d = route_mod.decide(scores, {"P"}, "implement")
    assert d["mode"] == "probe"


@pytest.mark.parametrize("p", [0, 1])
def test_unsure_p_below_2_is_taken_as_given(route_mod, p):
    scores = base_scores(P=p)
    d = route_mod.decide(scores, {"P"}, "implement")
    assert d["mode"] != "probe"


# ----------------------------------------------------------------------------
# A3: --after-probe never returns or prints a probe
# ----------------------------------------------------------------------------
def test_after_probe_suppresses_i0_s2_probe_reason(route_mod):
    # Reproduction from the sprint spec: this used to print probe->inline anyway.
    scores, unsure = route_mod.parse_scores("S2 R2 A1 K2 I0 P0 V2")
    d = route_mod.decide(scores, unsure, "implement", after_probe=True)
    assert d["mode"] == "inline"
    assert d["probe"] is False
    assert d["probe_why"] == []


def test_after_probe_never_probes_even_with_unsure_left(route_mod):
    # An after-probe call is documented as never probing again; anything still
    # unsure is decided conservatively instead (S rounds up, P is taken as given).
    scores, unsure = route_mod.parse_scores("S2? R1 A1 K1 I2 P2? V1")
    d = route_mod.decide(scores, unsure, "implement", after_probe=True)
    assert d["mode"] != "probe"
    assert d["probe"] is False


def test_kind_answer_never_probes_first_match_inline(route_mod):
    # kind=answer is the first rule checked; it must win before any unsure-driven
    # probe logic runs, even with S and P both unsure.
    scores, unsure = route_mod.parse_scores("S1? R1 A1 K1 I2 P2? V1")
    d = route_mod.decide(scores, unsure, "answer")
    assert d["mode"] == "inline"
    assert d["probe"] is False
    assert d["mode_why"] == "kind=answer: nothing to delegate"


# ----------------------------------------------------------------------------
# Modes
# ----------------------------------------------------------------------------
def test_mode_i0_is_inline(route_mod):
    scores = base_scores(I=0, S=0)
    d = route_mod.decide(scores, set(), "implement")
    assert d["mode"] == "inline"


def test_mode_i0_with_s2_probes_first_when_not_after_probe(route_mod):
    scores = base_scores(I=0, S=2)
    d = route_mod.decide(scores, set(), "implement")
    assert d["mode"] == "inline"
    assert d["probe"] is True


@pytest.mark.parametrize("p,expected_tier_shift", [(2, -1), (3, -1)])
def test_mode_p_ge_2_fans_out_one_tier_down(route_mod, p, expected_tier_shift):
    scores = base_scores(R=2, A=2, K=1, P=p, V=2)  # difficulty 5 -> sonnet (tier 1)
    d = route_mod.decide(scores, set(), "implement")
    assert d["mode"] == "fan-out"
    assert d["tier"] == 1
    shard_tier = route_mod.CONFIG["tiers"].index(d["shard_model"])
    assert shard_tier == d["tier"] + expected_tier_shift


def test_mode_p_ge_2_shard_floored_at_sonnet_when_k2(route_mod):
    scores = base_scores(R=0, A=0, K=2, P=3, V=0)  # task tier would floor shards at haiku otherwise
    d = route_mod.decide(scores, set(), "implement")
    assert d["mode"] == "fan-out"
    assert d["shard_model"] == "sonnet"


def test_mode_p_ge_2_shard_not_floored_when_k_below_2(route_mod):
    # Controlled contrast for the test above: identical scores except K, so a
    # mutation to shard_floor_when_risky (or to the K>=2 condition guarding it)
    # is caught by a clean pair, not just the K=2 side.
    scores = base_scores(R=0, A=0, K=1, P=3, V=0)
    d = route_mod.decide(scores, set(), "implement")
    assert d["mode"] == "fan-out"
    assert d["shard_model"] == "haiku"


def test_mode_p1_fans_out_at_task_tier_no_discount(route_mod):
    scores = base_scores(R=2, A=2, K=1, P=1, V=2)  # difficulty 5 -> sonnet
    d = route_mod.decide(scores, set(), "implement")
    assert d["mode"] == "fan-out"
    assert d["shard_model"] == d["model"]  # P1 keeps the task tier, no one-tier discount


def test_mode_s2_forces_single_agent(route_mod):
    scores = base_scores(S=2, I=2, P=0)
    d = route_mod.decide(scores, set(), "implement")
    assert d["mode"] == "single"


def test_mode_i3_forces_single_agent(route_mod):
    scores = base_scores(S=0, I=3, P=0)
    d = route_mod.decide(scores, set(), "implement")
    assert d["mode"] == "single"


def test_mode_kind_explore_forces_single_agent(route_mod):
    scores = base_scores(S=0, I=2, P=0)
    d = route_mod.decide(scores, set(), "explore")
    assert d["mode"] == "single"


def test_mode_otherwise_inline(route_mod):
    scores = base_scores(S=0, I=2, P=0)
    d = route_mod.decide(scores, set(), "implement")
    assert d["mode"] == "inline"


def test_mode_kind_answer_always_inline(route_mod):
    scores = base_scores(S=3, R=3, A=3, K=3, I=3, P=3, V=3)  # maximal everything
    d = route_mod.decide(scores, set(), "answer")
    assert d["mode"] == "inline"
    assert d["mode_why"] == "kind=answer: nothing to delegate"


# ----------------------------------------------------------------------------
# A5: shard counting, the P1 cap, and the parallel cap
# ----------------------------------------------------------------------------
def test_parallel_cap_is_8(route_mod):
    assert route_mod.CONFIG["parallel_cap"] == 8


@pytest.mark.parametrize("p,expected_default", [(1, 3), (2, 6), (3, 8)])
def test_shards_default_by_p_when_not_given(route_mod, p, expected_default):
    # Hardcoded expected values, not CONFIG["shards_by_P"][p]: reading the value back
    # out of the (possibly mutated) CONFIG to build the expectation would make this
    # test agree with any mutation instead of catching it.
    scores = base_scores(P=p)
    d = route_mod.decide(scores, set(), "implement", shards=None)
    assert d["shards"] == expected_default


def test_shards_above_parallel_cap_keeps_total_reports_concurrency(route_mod):
    scores = base_scores(P=2)
    d = route_mod.decide(scores, set(), "implement", shards=12)
    assert d["shards"] == 12  # total kept, not silently clamped to 8
    assert d["shard_concurrency"] == 8
    assert any("run in batches of 8" in n for n in d["notes"])


def test_shards_at_or_under_cap_unchanged(route_mod):
    scores = base_scores(P=2)
    d = route_mod.decide(scores, set(), "implement", shards=7)
    assert d["shards"] == 7
    assert d["shard_concurrency"] == 7


def test_p1_caps_shards_at_3(route_mod):
    scores = base_scores(P=1)
    d = route_mod.decide(scores, set(), "implement", shards=8)
    assert d["shards"] == 3
    assert any("P1 caps shards at 3" in n for n in d["notes"])


def test_p1_shards_under_cap_unaffected(route_mod):
    scores = base_scores(P=1)
    d = route_mod.decide(scores, set(), "implement", shards=2)
    assert d["shards"] == 2


# ----------------------------------------------------------------------------
# Tier names and the fable->opus fallback
# ----------------------------------------------------------------------------
def test_top_tier_is_fable_with_opus_fallback(route_mod):
    scores = base_scores(R=3, A=3, K=3, V=0)  # difficulty 9 -> fable band
    d = route_mod.decide(scores, set(), "implement")
    assert d["difficulty"] == 9
    assert d["tier"] == 3
    assert d["model"] == "fable"
    assert d["model_fallback"] == "opus"
    assert d["effort"] == "max"


def test_non_fable_tiers_have_no_fallback(route_mod):
    scores = base_scores(R=0, A=0, K=0, V=2)  # difficulty 0 -> haiku, no fallback listed
    d = route_mod.decide(scores, set(), "implement")
    assert d["model"] == "haiku"
    assert d["model_fallback"] is None


# ----------------------------------------------------------------------------
# Override: annotation only, policy line unchanged
# ----------------------------------------------------------------------------
def test_override_does_not_change_the_policy_line(route_mod):
    scores = base_scores(R=1, A=1, K=1, V=1)
    plain = route_mod.decide(dict(scores), set(), "implement")
    overridden = route_mod.decide(dict(scores), set(), "implement", override="use opus")
    for key in ("mode", "tier", "model", "shards", "verifier", "gate"):
        assert plain[key] == overridden[key]
    assert overridden["override"] == "use opus"
    assert plain["override"] is None


def test_override_annotates_one_liner(route_mod):
    scores = base_scores()
    d = route_mod.decide(scores, set(), "implement", override="inline")
    assert "override" in route_mod.one_liner(d)


# ----------------------------------------------------------------------------
# A6: the embedded rubric text matches references/rubric.md
# ----------------------------------------------------------------------------
def test_rubric_s_is_volume_not_file_count(route_mod):
    question, levels = route_mod.RUBRIC["S"]
    assert "remain" in question.lower()
    assert "volume" in question.lower() or "file count" in question.lower()
    assert len(levels) == 4
    # A 7-file, 150-line repo must read as S1 (under ~300 lines), not S2 by file count.
    assert "300" in levels[1]


def test_rubric_keys_and_level_counts_unchanged_for_jev(route_mod):
    # The Jev request shape depends on this: same dimension keys, 4 levels each.
    assert set(route_mod.RUBRIC) == {"S", "R", "A", "K", "I", "P", "V"}
    for dim, (_, levels) in route_mod.RUBRIC.items():
        assert len(levels) == 4, f"{dim} must keep exactly 4 levels"
