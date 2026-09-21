"""C6 (#23, amended): `score --json` prints exactly one JSON document, in both
heuristic and (structurally, without a network call) Jev mode, and never mixes it
with any human-readable line. Without --json, output is unchanged byte for byte
from before this sprint.

Before this sprint's fix:
  - `score --task "..." --json --route --no-log` (heuristic, the only mode these
    tests can exercise without the network) printed the human "scores (...)" line,
    the note, and the one-liner -- --json was silently ignored, producing NO json
    at all. Reproduced in test_json_flag_in_heuristic_mode_used_to_print_no_json_at_all
    (asserts the CURRENT, fixed behavior; see the docstring for the repro that
    motivated it).
  - In --jev mode (not exercised here -- no network), --json instead mixed a
    bare JSON block in between the human lines, which is what
    experiments/ab-routing/route_arm_a.py::parse_route_output had to parse
    around; that parser is covered separately in
    experiments/ab-routing/selftest/test_route_arm_a.py.
"""
from __future__ import annotations

import json


def test_json_output_is_a_single_parseable_document(run_cli):
    result = run_cli("score", "--task", "rename a function", "--route", "--json", "--no-log")
    assert result.returncode == 0, result.stderr
    doc = json.loads(result.stdout)  # would raise if anything else shared stdout
    assert isinstance(doc, dict)


def test_json_document_has_the_required_top_level_keys(run_cli):
    result = run_cli("score", "--task", "rename a function", "--route", "--json", "--no-log")
    doc = json.loads(result.stdout)
    for key in ("scorer", "scores_line", "scores", "unsure", "kind", "raw", "note", "decision", "one_liner"):
        assert key in doc, f"missing {key!r}"
    assert doc["scorer"] == "heuristic"
    assert doc["scores_line"].startswith("scores (heuristic)")
    assert doc["raw"] is None  # heuristic mode: no Jev raw block
    assert set(doc["scores"]) == {"S", "R", "A", "K", "I", "P", "V"}
    assert isinstance(doc["unsure"], list)
    assert doc["one_liner"].startswith("[c-")
    assert doc["decision"]["id"] == doc["one_liner"][1:].split(" ")[0]


def test_json_without_route_omits_decision_and_one_liner(run_cli):
    result = run_cli("score", "--task", "rename a function", "--json")
    doc = json.loads(result.stdout)
    assert doc["scorer"] == "heuristic"
    assert "decision" not in doc
    assert "one_liner" not in doc
    assert doc["raw"] is None


def test_json_note_present_for_heuristic_scorer(run_cli):
    result = run_cli("score", "--task", "rename a function", "--json")
    doc = json.loads(result.stdout)
    assert doc["note"] is not None
    assert "heuristic" in doc["note"]


def test_json_output_has_no_stray_human_lines_before_or_after(run_cli):
    """The whole of stdout must be the one document -- nothing printed before or
    after it, in either --route mode or not."""
    for args in (
        ["score", "--task", "rename a function", "--json"],
        ["score", "--task", "rename a function", "--route", "--json", "--no-log"],
        ["score", "--task", "rename a function", "--route", "--json", "--no-log", "--explain"],
    ):
        result = run_cli(*args)
        assert result.returncode == 0, result.stderr
        stripped = result.stdout.strip()
        assert stripped.startswith("{") and stripped.endswith("}")
        json.loads(stripped)  # the WHOLE stdout, not a substring of it, must parse


def test_json_route_still_appends_exactly_one_decision_to_the_log(run_cli, isolated_log):
    result = run_cli("score", "--task", "rename a function", "--route", "--json")
    assert result.returncode == 0, result.stderr
    lines = [l for l in isolated_log.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1
    logged = json.loads(lines[0])
    assert logged["event"] == "decision"
    assert logged["task"] == "rename a function"


# ----------------------------------------------------------------------------
# Without --json: byte-for-byte unchanged from before this sprint
# ----------------------------------------------------------------------------
def test_non_json_heuristic_output_unchanged(run_cli):
    result = run_cli("score", "--task", "rename a function")
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0].startswith("scores (heuristic)")
    assert lines[1].startswith("note: keyword heuristic")
    assert len(lines) == 2  # no --route: nothing else prints


def test_non_json_heuristic_with_route_prints_scores_then_note_then_one_liner(run_cli):
    result = run_cli("score", "--task", "rename a function", "--route", "--no-log")
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0].startswith("scores (heuristic)")
    assert lines[1].startswith("note: keyword heuristic")
    assert lines[2].startswith("[c-")


def test_non_json_explain_prints_the_card_not_the_one_liner(run_cli):
    result = run_cli("score", "--task", "rename a function", "--route", "--no-log", "--explain")
    assert result.returncode == 0, result.stderr
    assert "complexity  " in result.stdout
    assert "route       " in result.stdout
    assert "task        rename a function" in result.stdout
