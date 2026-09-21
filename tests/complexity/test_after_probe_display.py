"""F10 (PLAYBOOK.md "skill publish-hardening", Task F): --after-probe must keep the
user's `?` flags in the one-liner, the card, and the logged `unsure` list, while
still never probing and never changing any decision field.

Before this fix, decide() discarded "S" and "P" from `unsure` once it had used
them (rounding S up, taking P as given) whenever after_probe was True. That
discard had no effect on the decision itself: every later read of `unsure` for S
or P (the probe-reason checks, and the probe-mode branch) is gated on
`can_probe`, which is already False whenever after_probe is True -- but the
discard did make the returned "unsure" list, the card, and the one-liner
silently drop a still-unsure S or P. Reproduced exactly:

    route --scores "S=2?,R=1,A=1,K=1,I=2,P=0,V=1" --kind implement --after-probe --explain

used to print "S2 ... difficulty 4/9" (the "?" stripped) and log `"unsure": []`,
even though the difficulty was computed from the rounded-up S=3.

test_after_probe_keeps_the_question_mark_reproduction below is that exact
reproduction, now fixed. The equivalence property test loads the pre-fix
decide() from git history (the last commit before this fix touched decide())
and confirms every decision field OTHER than "unsure" itself is identical to
the fixed decide() across a large random sample of after_probe=True calls --
proving this change is display-and-logging-only, never a policy change.
"""
from __future__ import annotations

import importlib.util
import random
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# The commit immediately before F10's fix touched decide() -- the last point at
# which `unsure.discard("S")` / `unsure.discard("P")` were still present in the
# after_probe branch. Pinned to an exact SHA (not "HEAD"): HEAD moves forward
# with every later commit, including this one, so "HEAD" would stop meaning
# "the pre-fix version" the moment this test itself is committed.
PRE_F10_REV = "d08dea4c60470ada5fa2a3f51053a33dfc54a8ac"

DIMS = ["S", "R", "A", "K", "I", "P", "V"]
KINDS = ["explore", "plan", "implement", "review", "answer"]
SAMPLE_SIZE = 2500  # >= the 2,000 PLAYBOOK.md's F10 criterion asks for

# Every field decide() returns EXCEPT "unsure" itself (the one field F10
# intentionally changes) and "id" / "ts" / "project" / "cwd" (freshly generated
# or environment-dependent every call, never expected to match between two
# separate invocations of two different module instances).
DECISION_FIELDS_TO_COMPARE = [
    "kind", "scores_given", "scores_used", "difficulty", "tier", "model",
    "model_fallback", "effort", "mode", "mode_why", "probe", "probe_why",
    "agent_type", "shards", "shard_concurrency", "shard_model", "reduce",
    "verifier", "gate", "override", "after_probe", "notes",
]


@pytest.fixture(scope="session")
def pre_f10_route_mod(tmp_path_factory):
    """route.py as it stood at PRE_F10_REV, loaded into its own temp module --
    never the live file on disk. Skips cleanly (rather than failing the whole
    suite) if that revision isn't reachable, e.g. a shallow checkout."""
    try:
        proc = subprocess.run(
            ["git", "show", f"{PRE_F10_REV}:skills/complexity/scripts/route.py"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        pytest.skip(f"could not run git show for {PRE_F10_REV}: {e}")
    if proc.returncode != 0:
        pytest.skip(f"git show {PRE_F10_REV}:route.py failed (shallow checkout?): {proc.stderr.strip()}")

    out_dir = tmp_path_factory.mktemp("pre_f10")
    path = out_dir / "route_pre_f10.py"
    path.write_text(proc.stdout, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("complexity_route_pre_f10", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ----------------------------------------------------------------------------
# The exact reproduction from PLAYBOOK.md's F10 criterion
# ----------------------------------------------------------------------------
def test_after_probe_keeps_the_question_mark_reproduction(route_mod):
    scores, unsure = route_mod.parse_scores("S=2?,R=1,A=1,K=1,I=2,P=0,V=1")
    d = route_mod.decide(scores, unsure, "implement", after_probe=True)

    assert d["difficulty"] == 4  # unchanged: S is still rounded up to 3 internally
    assert d["unsure"] == ["S"]  # was [] before the fix
    assert d["mode"] != "probe"  # still never probes
    assert d["probe"] is False

    card = route_mod.card(d, None, None)
    assert "S2?" in card.splitlines()[0], card  # was "S2" before the fix
    one_liner = route_mod.one_liner(d)
    assert "S2?" in one_liner, one_liner  # was "S2" before the fix


def test_after_probe_p_taken_as_given_also_keeps_its_question_mark(route_mod):
    scores, unsure = route_mod.parse_scores("S1 R1 A1 K1 I2 P2? V1")
    d = route_mod.decide(scores, unsure, "implement", after_probe=True)
    assert "P" in d["unsure"]
    assert d["mode"] != "probe"
    card = route_mod.card(d, None, None)
    assert "P2?" in card.splitlines()[0], card


def test_after_probe_still_never_probes_with_both_s_and_p_unsure(route_mod):
    scores, unsure = route_mod.parse_scores("S2? R1 A1 K1 I2 P2? V1")
    d = route_mod.decide(scores, unsure, "implement", after_probe=True)
    assert d["mode"] != "probe"
    assert d["probe"] is False
    assert d["probe_why"] == []
    assert set(d["unsure"]) == {"S", "P"}


# ----------------------------------------------------------------------------
# Property test: the change is display/logging-only, never a policy change
# ----------------------------------------------------------------------------
def test_after_probe_decision_fields_unchanged_across_random_inputs(route_mod, pre_f10_route_mod):
    rng = random.Random(20260921)
    mismatches = []
    for _ in range(SAMPLE_SIZE):
        scores = {d: rng.randint(0, 3) for d in DIMS}
        unsure = {d for d in DIMS if rng.random() < 0.35}
        kind = rng.choice(KINDS)
        shards = rng.choice([None, 1, 2, 3, 5, 8, 12])
        override = rng.choice([None, "use opus", "inline"])

        old = pre_f10_route_mod.decide(dict(scores), set(unsure), kind, shards, override, True)
        new = route_mod.decide(dict(scores), set(unsure), kind, shards, override, True)

        for field in DECISION_FIELDS_TO_COMPARE:
            if old.get(field) != new.get(field):
                mismatches.append((scores, sorted(unsure), kind, shards, override,
                                    field, old.get(field), new.get(field)))

    assert not mismatches, (
        f"{len(mismatches)} decision-field mismatches between pre- and post-F10 decide() "
        f"(showing up to 5): {mismatches[:5]}"
    )
