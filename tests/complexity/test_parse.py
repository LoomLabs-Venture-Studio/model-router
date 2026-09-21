"""A2: parse_scores accepts exactly the two documented syntaxes, consumes the whole
string, and rejects everything else with a clean one-line message and a non-zero
exit -- never a traceback.

Every "bad input" case here reproduces a real bug found in `route.py` before this
sprint (see PLAYBOOK.md, sprint "skill publish-hardening", criterion A2 / Codex
finding #8):
  - "K30" silently became K3 (the trailing digit was dropped, not rejected)
  - "K0.9" silently became K0 (the fractional part was dropped)
  - a duplicate dimension silently overwrote the first value
  - trailing junk after a full, valid score set was silently ignored
"""
from __future__ import annotations

import pytest

ALL_DIMS = ["S", "R", "A", "K", "I", "P", "V"]


def test_comma_equals_syntax(route_mod):
    scores, unsure = route_mod.parse_scores("S=2,R=3?,A=2,K=2,I=1,P=0,V=2")
    assert scores == {"S": 2, "R": 3, "A": 2, "K": 2, "I": 1, "P": 0, "V": 2}
    assert unsure == {"R"}


def test_comma_equals_syntax_tolerates_spacing(route_mod):
    scores, unsure = route_mod.parse_scores("S = 2, R=1, A=0, K=1, I=2, P=0, V=1")
    assert scores == {"S": 2, "R": 1, "A": 0, "K": 1, "I": 2, "P": 0, "V": 1}
    assert unsure == set()


def test_space_syntax(route_mod):
    scores, unsure = route_mod.parse_scores("S3 R1 A1 K2 I3 P2 V2")
    assert scores == {"S": 3, "R": 1, "A": 1, "K": 2, "I": 3, "P": 2, "V": 2}
    assert unsure == set()


def test_space_syntax_with_unsure_marks(route_mod):
    scores, unsure = route_mod.parse_scores("S2? R1 A1? K2 I3 P2 V2")
    assert scores["S"] == 2 and scores["A"] == 1
    assert unsure == {"S", "A"}


def test_lowercase_dimensions_accepted(route_mod):
    scores, unsure = route_mod.parse_scores("s3 r1 a1 k2 i3 p2 v2")
    assert scores == {"S": 3, "R": 1, "A": 1, "K": 2, "I": 3, "P": 2, "V": 2}


@pytest.mark.parametrize("text", [
    "S1 R1 A1 K30 I2 P0 V1",   # out-of-range level, multi-digit
    "S1 R1 A1 K9 I2 P0 V1",    # out-of-range level, single extra digit
])
def test_out_of_range_level_rejected(route_mod, text):
    with pytest.raises(SystemExit) as exc:
        route_mod.parse_scores(text)
    assert exc.value.code and "0..3" in str(exc.value.code)


def test_non_integer_level_rejected(route_mod):
    # "K0.9" used to silently become K0 (the ".9" was dropped by a partial match).
    with pytest.raises(SystemExit) as exc:
        route_mod.parse_scores("S1 R1 A1 K0.9 I2 P0 V1")
    assert exc.value.code
    assert "traceback" not in str(exc.value.code).lower()


def test_duplicate_dimension_rejected(route_mod):
    # Used to silently keep the last value (K3 then K0 -> K0).
    with pytest.raises(SystemExit) as exc:
        route_mod.parse_scores("S1 R1 A1 K3 K0 I2 P0 V1")
    assert "duplicate" in str(exc.value.code).lower()
    assert "K" in str(exc.value.code)


def test_duplicate_dimension_with_unsure_mark_rejected(route_mod):
    # "K0? K1": the '?' used to stick to the replacement value instead of erroring.
    with pytest.raises(SystemExit) as exc:
        route_mod.parse_scores("S1 R1 A1 K0? K1 I2 P0 V1")
    assert "duplicate" in str(exc.value.code).lower()


def test_unknown_dimension_rejected(route_mod):
    with pytest.raises(SystemExit):
        route_mod.parse_scores("S1 R1 A1 K1 X2 P0 V1")


def test_missing_dimension_rejected(route_mod):
    with pytest.raises(SystemExit) as exc:
        route_mod.parse_scores("S1 R1 A1 K1 I2 P0")  # no V
    assert "missing" in str(exc.value.code).lower()
    assert "V" in str(exc.value.code)


def test_trailing_junk_rejected_space_syntax(route_mod):
    # A full, valid score set followed by garbage used to be silently accepted.
    with pytest.raises(SystemExit):
        route_mod.parse_scores("S1 R1 A1 K1 I2 P0 V1 extra")


def test_trailing_junk_rejected_comma_syntax(route_mod):
    with pytest.raises(SystemExit):
        route_mod.parse_scores("S=1,R=1,A=1,K=1,I=1,P=1,V=1,extra")


def test_empty_input_rejected(route_mod):
    with pytest.raises(SystemExit):
        route_mod.parse_scores("")


def test_mixed_syntax_rejected(route_mod):
    # Equals-signs but space-separated (no comma): not one of the two documented forms.
    with pytest.raises(SystemExit):
        route_mod.parse_scores("S=1 R=1 A=1 K=1 I=1 P=1 V=1")


def test_result_types(route_mod):
    scores, unsure = route_mod.parse_scores("S3 R1 A1 K2 I3 P2 V2")
    assert isinstance(scores, dict)
    assert isinstance(unsure, set)
    assert all(isinstance(v, int) for v in scores.values())
    assert set(scores) == set(ALL_DIMS)


# --- CLI-level: the same rejections, through the real argparse/sys.exit path -------
def test_cli_rejects_bad_scores_cleanly(run_cli):
    proc = run_cli("route", "--scores", "S1 R1 A1 K30 I2 P0 V1", "--kind", "implement", "--no-log")
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr
    assert proc.stderr.strip()
    assert proc.stdout == ""


def test_cli_accepts_documented_syntax(run_cli):
    proc = run_cli("route", "--scores", "S=2,R=1,A=0,K=1,I=2,P=0,V=1", "--kind", "implement", "--no-log")
    assert proc.returncode == 0
    assert proc.stdout.startswith("[c-")
