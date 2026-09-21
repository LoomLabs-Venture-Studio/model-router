"""C2 (#14): validate_jev_response, the pure function factored out of score_jev's
response handling. No network call anywhere in this file: every test hands a
hand-built dict (exactly the shape json.loads(resp_body) would produce) straight
to validate_jev_response and inspects the result or the raised JevResponseError.

Before this sprint's fix, this logic lived inline in score_jev and:
  - `"score": "NaN"` made `round(score_val - min(legend_keys))` raise a bare
    ValueError ("cannot convert float NaN to integer"), not a clean `jev: ...`
    message -- reproduced in test_nan_score_is_a_clean_error_not_a_traceback.
  - `"score": "Infinity"` made the same `round()` call raise OverflowError --
    reproduced in test_infinite_score_is_a_clean_error_not_a_traceback.
  - a NaN confidence compared `nan < jev_unsure_below` as False, so it was
    silently treated as certain (never added to `unsure`) -- reproduced in
    test_nan_confidence_is_rejected_not_treated_as_certain.
  - a score outside the legend's own range was silently clamped with
    `max(0, min(3, level))`, so e.g. a wildly negative risk score became K0
    without any signal -- reproduced in test_out_of_range_score_is_rejected_not_clamped.
  - a non-finite usage number (e.g. `int(float("inf"))`) raised OverflowError,
    uncaught by the `except (KeyError, TypeError, ValueError)` clause -- reproduced
    in test_non_finite_usage_value_is_dropped_not_fatal.

Every test here confirms the NEW behavior: a clean JevResponseError instead of a
bare arithmetic exception, and usage dropped rather than fatal.
"""
from __future__ import annotations

import math

import pytest


def make_answer(score, confidence=0.9, legend=None):
    return {"score": score, "confidence": confidence,
            "legend": legend if legend is not None else {"0": "a", "1": "b", "2": "c", "3": "d"}}


def make_response(overrides: dict | None = None) -> dict:
    """A minimal, fully valid Jev response covering all seven rubric dimensions
    plus 'kind', so a single field can be overridden per test without repeating
    the whole shape everywhere."""
    answers = {d: make_answer(1.0) for d in ["S", "R", "A", "K", "I", "P", "V"]}
    answers["kind"] = {"choice": "implement", "confidence": 0.95}
    resp = {"answers": answers, "usage": {"input_tokens": 100, "output_tokens": 20}}
    if overrides:
        for path, value in overrides.items():
            d, field = path
            if d == "kind":
                resp["answers"]["kind"][field] = value
            else:
                resp["answers"][d][field] = value
    return resp


# ----------------------------------------------------------------------------
# A fully valid response validates cleanly and unchanged from the old behavior
# ----------------------------------------------------------------------------
def test_a_valid_response_validates(route_mod):
    resp = make_response()
    scores, unsure, kind, raw = route_mod.validate_jev_response(resp)
    assert scores == {"S": 1, "R": 1, "A": 1, "K": 1, "I": 1, "P": 1, "V": 1}
    assert unsure == set()  # confidence 0.9/0.95 is above jev_unsure_below (0.6)
    assert kind == "implement"
    assert raw["S"] == {"score": 1.0, "confidence": 0.9}
    assert raw["usage"] == {"input_tokens": 100, "output_tokens": 20}


def test_fractional_probability_weighted_score_within_range_is_accepted(route_mod):
    """Jev's score is documented as a probability-weighted mean, so a fractional
    value that never lands on an exact legend level must still validate -- it is
    not required to be one of the legend's own integer keys, only within their
    range."""
    resp = make_response({("S", "score"): 1.35})
    scores, _, _, raw = route_mod.validate_jev_response(resp)
    assert scores["S"] == 1  # round(1.35 - 0) == 1
    assert raw["S"]["score"] == 1.35


def test_low_confidence_marks_a_dimension_unsure(route_mod):
    resp = make_response({("V", "confidence"): 0.3})
    _, unsure, _, _ = route_mod.validate_jev_response(resp)
    assert "V" in unsure


# ----------------------------------------------------------------------------
# NaN / Infinity: clean JevResponseError, never a bare ValueError/OverflowError
# ----------------------------------------------------------------------------
def test_nan_score_is_a_clean_error_not_a_traceback(route_mod):
    resp = make_response({("K", "score"): "NaN"})
    with pytest.raises(route_mod.JevResponseError, match=r"(?i)k.*(finite|not a number)"):
        route_mod.validate_jev_response(resp)


def test_infinite_score_is_a_clean_error_not_a_traceback(route_mod):
    resp = make_response({("K", "score"): "Infinity"})
    with pytest.raises(route_mod.JevResponseError, match=r"(?i)k.*(finite|range)"):
        route_mod.validate_jev_response(resp)


def test_negative_infinite_score_is_a_clean_error(route_mod):
    resp = make_response({("R", "score"): float("-inf")})
    with pytest.raises(route_mod.JevResponseError):
        route_mod.validate_jev_response(resp)


def test_nan_confidence_is_rejected_not_treated_as_certain(route_mod):
    """Before the fix: `nan < jev_unsure_below` is False, so a NaN confidence was
    silently accepted as if it meant full certainty. It must now be a validation
    error instead."""
    resp = make_response({("A", "confidence"): float("nan")})
    with pytest.raises(route_mod.JevResponseError, match=r"(?i)a.*confidence"):
        route_mod.validate_jev_response(resp)


def test_infinite_confidence_is_rejected(route_mod):
    resp = make_response({("P", "confidence"): float("inf")})
    with pytest.raises(route_mod.JevResponseError):
        route_mod.validate_jev_response(resp)


def test_nan_kind_confidence_is_rejected(route_mod):
    resp = make_response({("kind", "confidence"): float("nan")})
    with pytest.raises(route_mod.JevResponseError, match=r"(?i)kind.*confidence"):
        route_mod.validate_jev_response(resp)


# ----------------------------------------------------------------------------
# Out-of-range: rejected, not silently clamped
# ----------------------------------------------------------------------------
def test_out_of_range_score_is_rejected_not_clamped(route_mod):
    """Before the fix: `max(0, min(3, level))` clamped ANY level into 0..3, so a
    wildly negative risk score silently became K0 -- the opposite of what a risk
    dimension should ever do with untrustworthy input."""
    resp = make_response({("K", "score"): -5.0})
    with pytest.raises(route_mod.JevResponseError, match=r"(?i)k.*range"):
        route_mod.validate_jev_response(resp)


def test_above_scale_score_is_rejected(route_mod):
    resp = make_response({("V", "score"): 10.0})
    with pytest.raises(route_mod.JevResponseError, match=r"(?i)v.*range"):
        route_mod.validate_jev_response(resp)


def test_confidence_above_one_is_rejected(route_mod):
    resp = make_response({("I", "confidence"): 1.5})
    with pytest.raises(route_mod.JevResponseError, match=r"(?i)i.*confidence"):
        route_mod.validate_jev_response(resp)


def test_confidence_below_zero_is_rejected(route_mod):
    resp = make_response({("I", "confidence"): -0.1})
    with pytest.raises(route_mod.JevResponseError):
        route_mod.validate_jev_response(resp)


# ----------------------------------------------------------------------------
# Usage: dropped field by field, never fatal
# ----------------------------------------------------------------------------
def test_non_finite_usage_value_is_dropped_not_fatal(route_mod):
    resp = make_response()
    resp["usage"] = {"input_tokens": float("inf"), "output_tokens": 20}
    _, _, _, raw = route_mod.validate_jev_response(resp)
    assert raw["usage"] == {"output_tokens": 20}


def test_negative_usage_value_is_dropped(route_mod):
    resp = make_response()
    resp["usage"] = {"input_tokens": -5, "output_tokens": 20}
    _, _, _, raw = route_mod.validate_jev_response(resp)
    assert raw["usage"] == {"output_tokens": 20}


def test_nan_usage_value_is_dropped(route_mod):
    resp = make_response()
    resp["usage"] = {"input_tokens": "NaN", "output_tokens": 20}
    _, _, _, raw = route_mod.validate_jev_response(resp)
    assert raw["usage"] == {"output_tokens": 20}


def test_usage_entirely_dropped_when_no_field_is_valid(route_mod):
    resp = make_response()
    resp["usage"] = {"input_tokens": "NaN", "output_tokens": float("inf")}
    _, _, _, raw = route_mod.validate_jev_response(resp)
    assert "usage" not in raw


def test_malformed_usage_block_is_omitted_not_fatal(route_mod):
    resp = make_response()
    resp["usage"] = "not even a dict"
    _, _, _, raw = route_mod.validate_jev_response(resp)
    assert "usage" not in raw


# ----------------------------------------------------------------------------
# Structural errors carried over from before (still clean, still non-fatal to the
# process as a whole -- score_jev turns these into sys.exit, never a traceback)
# ----------------------------------------------------------------------------
def test_missing_answers_key_is_a_clean_error(route_mod):
    with pytest.raises(route_mod.JevResponseError, match="answers"):
        route_mod.validate_jev_response({"nope": True})


def test_missing_dimension_answer_is_a_clean_error(route_mod):
    resp = make_response()
    del resp["answers"]["K"]
    with pytest.raises(route_mod.JevResponseError, match="K"):
        route_mod.validate_jev_response(resp)


def test_legend_key_count_mismatch_is_a_clean_error(route_mod):
    resp = make_response({("S", "score"): 1.0})
    resp["answers"]["S"]["legend"] = {"0": "a", "1": "b"}
    with pytest.raises(route_mod.JevResponseError, match="legend"):
        route_mod.validate_jev_response(resp)


def test_non_finite_legend_key_is_a_clean_error(route_mod):
    resp = make_response()
    resp["answers"]["S"]["legend"] = {"0": "a", "1": "b", "nan": "c", "3": "d"}
    with pytest.raises(route_mod.JevResponseError, match="legend"):
        route_mod.validate_jev_response(resp)


def test_missing_kind_is_a_clean_error(route_mod):
    resp = make_response()
    del resp["answers"]["kind"]
    with pytest.raises(route_mod.JevResponseError, match="kind"):
        route_mod.validate_jev_response(resp)


def test_unknown_kind_choice_is_a_clean_error_and_redacts_the_key(route_mod):
    resp = make_response({("kind", "choice"): "DUMMY_KEY_123: not a real kind"})
    with pytest.raises(route_mod.JevResponseError) as excinfo:
        route_mod.validate_jev_response(resp, key="DUMMY_KEY_123")
    assert "DUMMY_KEY_123" not in str(excinfo.value)


def test_score_jev_response_shape_matches_math_isfinite_expectations(route_mod):
    """Sanity check on the helper this all rests on: math.isfinite rejects exactly
    nan and +/-inf, nothing else."""
    assert route_mod._finite_number(2.5, "x") == 2.5
    with pytest.raises(route_mod.JevResponseError):
        route_mod._finite_number(math.nan, "x")
    with pytest.raises(route_mod.JevResponseError):
        route_mod._finite_number(math.inf, "x")
    with pytest.raises(route_mod.JevResponseError):
        route_mod._finite_number("not a number", "x")
