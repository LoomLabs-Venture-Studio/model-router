"""selftest for report.py: fed trimmed copies of a REAL run's artefacts
(selftest/fixtures/, scrubbed only of this machine's absolute home-directory
paths -- everything else, including every number, is exactly what the real
run produced). Asserts specific real numbers rather than hand-invented ones,
since an earlier version of this file used an invented costs.json shape
(per_arm_model.breakdown, which does not exist) and never caught that the
cost-breakdown table was rendering every cell as $0.0000.
"""
from __future__ import annotations

import json
from pathlib import Path

import report

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _write_run(run_dir: Path, *names: str) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        (run_dir / name).write_text((FIXTURES / name).read_text(encoding="utf-8"), encoding="utf-8")


# ----------------------------------------------------------------------------
# Headline / cost numbers, against the real run
# ----------------------------------------------------------------------------
def test_headline_grand_totals_and_ratio(tmp_path):
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "r1"
    _write_run(run_dir, "results.json", "costs.json")

    rc = report.main(["--run", "r1", "--runs-root", str(runs_root), "--out", str(run_dir / "REPORT.md")])
    assert rc == 0

    text = (run_dir / "REPORT.md").read_text(encoding="utf-8")
    assert "Arm A $3.47" in text
    assert "Arm B $9.09" in text
    assert "Ratio A/B: 0.382" in text

    costs = _load("costs.json")
    assert costs["grand_total_usd"]["A"] == 3.4739382619999994
    assert costs["grand_total_usd"]["B"] == 9.09328025
    assert round(costs["grand_total_usd"]["A"] / costs["grand_total_usd"]["B"], 3) == 0.382


def test_per_task_table_task3_costs(tmp_path):
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "r1"
    _write_run(run_dir, "results.json", "costs.json")

    rc = report.main(["--run", "r1", "--runs-root", str(runs_root), "--out", str(run_dir / "REPORT.md")])
    assert rc == 0
    text = (run_dir / "REPORT.md").read_text(encoding="utf-8")

    # task 3: A cost 1.35, B cost 2.37 (per_task in the real costs.json)
    lines = [l for l in text.splitlines() if l.startswith("| 3 |")]
    assert len(lines) == 1, lines
    assert "$1.35" in lines[0]
    assert "$2.37" in lines[0]

    costs = _load("costs.json")
    assert round(costs["per_task"]["3"]["A"]["usd"], 2) == 1.35
    assert round(costs["per_task"]["3"]["B"]["usd"], 2) == 2.37

    # A totals row: real pass counts are 51/51 for both arms (see results.json)
    total_line = next(l for l in text.splitlines() if l.startswith("| **Total**"))
    assert "51/51" in total_line


def test_per_task_table_is_not_the_old_target_only_bug(tmp_path):
    """Regression: the old table printed the TARGET count (e.g. 8) in the
    "Tests (A/B)" column for task 1, whose suite is 10 tests (8 target + 2
    guard), and never distinguished A from B. This checks the real total
    (passed/total, both marks) appears for task 1 (10/10 in the real run),
    plus separate target and guard columns."""
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "r1"
    _write_run(run_dir, "results.json", "costs.json")

    rc = report.main(["--run", "r1", "--runs-root", str(runs_root), "--out", str(run_dir / "REPORT.md")])
    assert rc == 0
    text = (run_dir / "REPORT.md").read_text(encoding="utf-8")

    task1_line = next(l for l in text.splitlines() if l.startswith("| 1 |"))
    cells = [c.strip() for c in task1_line.strip("|").split("|")]
    # Task | A passed/total | B passed/total | A target | B target | A guard | B guard | Cost A | Cost B | Ratio
    assert cells[1] == "10/10"
    assert cells[2] == "10/10"
    assert cells[3] == "8/8"   # A target
    assert cells[5] == "2/2"   # A guard


# ----------------------------------------------------------------------------
# Cost breakdown, against the real per-token-type shape
# ----------------------------------------------------------------------------
def test_cost_breakdown_has_nonzero_fable_output_cell(tmp_path):
    """Regression: the old code read model_data["breakdown"], which does not
    exist on costs.json's per_arm_model entries (only on individual
    line_items) -- every per-token-type cell rendered as $0.0000 regardless of
    the real numbers. claude-fable-5-1's output tokens cost $4.32 for real."""
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "r1"
    _write_run(run_dir, "results.json", "costs.json")

    rc = report.main(["--run", "r1", "--runs-root", str(runs_root), "--out", str(run_dir / "REPORT.md")])
    assert rc == 0
    text = (run_dir / "REPORT.md").read_text(encoding="utf-8")

    fable_line = next(l for l in text.splitlines() if l.startswith("| claude-fable-5-1"))
    assert "$0.0000" not in fable_line.split("|")[3], fable_line  # the Output column, not $0
    assert "4.3206" in fable_line

    # sanity against the aggregation function directly
    costs = _load("costs.json")
    groups = report.aggregate_breakdown_by(costs["line_items"], ("arm", "model"))
    fable = groups[("B", "claude-fable-5-1")]
    assert fable["by_type"]["output_tokens"]["usd"] > 0


def test_cost_breakdown_per_role_table(tmp_path):
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "r1"
    _write_run(run_dir, "results.json", "costs.json")

    rc = report.main(["--run", "r1", "--runs-root", str(runs_root), "--out", str(run_dir / "REPORT.md")])
    assert rc == 0
    text = (run_dir / "REPORT.md").read_text(encoding="utf-8")
    assert "| A | probe |" in text
    assert "| A | router |" in text
    assert "| A | engineer |" in text


# ----------------------------------------------------------------------------
# --as-scored: two headline rows with the real 49-vs-51 discrepancy
# ----------------------------------------------------------------------------
def test_as_scored_vs_corrected_headline(tmp_path):
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "r1"
    _write_run(run_dir, "results.json", "costs.json")
    as_scored_path = tmp_path / "results.as_scored.json"
    as_scored_path.write_text((FIXTURES / "results.as_scored.json").read_text(encoding="utf-8"), encoding="utf-8")

    rc = report.main(["--run", "r1", "--runs-root", str(runs_root), "--as-scored", str(as_scored_path),
                       "--out", str(run_dir / "REPORT.md")])
    assert rc == 0
    text = (run_dir / "REPORT.md").read_text(encoding="utf-8")

    assert "**As scored:** A passed 51/51, B passed 49/51" in text
    assert "**Corrected:** A passed 51/51, B passed 51/51" in text

    as_scored = _load("results.as_scored.json")
    results = _load("results.json")
    assert report.task_totals(as_scored)["B"] == 49
    assert report.task_totals(results)["B"] == 51


# ----------------------------------------------------------------------------
# Blind review: real review.md + real blind_key.json de-anonymised
# ----------------------------------------------------------------------------
def test_blind_review_deanonymised_table_matches_the_real_verdicts(tmp_path):
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "r1"
    _write_run(run_dir, "results.json", "costs.json", "blind_key.json")
    review_path = tmp_path / "review.md"
    review_path.write_text((FIXTURES / "review.md").read_text(encoding="utf-8"), encoding="utf-8")

    rc = report.main(["--run", "r1", "--runs-root", str(runs_root), "--review", str(review_path),
                       "--out", str(run_dir / "REPORT.md")])
    assert rc == 0
    text = (run_dir / "REPORT.md").read_text(encoding="utf-8")

    assert "### De-anonymised" in text
    # task 1 better = A; tasks 2 and 3 better = B; task 4 tie
    # (search only the De-anonymised table -- the per-task table earlier in the
    # report also has rows starting "| 1 |" etc, for a different purpose)
    deanon_section = text.split("### De-anonymised", 1)[1].split("### Reviewer output", 1)[0]
    row1 = next(l for l in deanon_section.splitlines() if l.startswith("| 1 |"))
    row2 = next(l for l in deanon_section.splitlines() if l.startswith("| 2 |"))
    row3 = next(l for l in deanon_section.splitlines() if l.startswith("| 3 |"))
    row4 = next(l for l in deanon_section.splitlines() if l.startswith("| 4 |"))
    assert [c.strip() for c in row1.split("|")][4] == "A"
    assert [c.strip() for c in row2.split("|")][4] == "B"
    assert [c.strip() for c in row3.split("|")][4] == "B"
    assert [c.strip() for c in row4.split("|")][4] == "tie"

    # the raw reviewer text is embedded verbatim too
    assert "task1 X: none found" in text


def test_parse_review_table_and_deanonymize_directly():
    review_text = (FIXTURES / "review.md").read_text(encoding="utf-8")
    blind_key = _load("blind_key.json")

    rows = report.parse_review_table(review_text)
    assert rows is not None and len(rows) == 4

    deanon = report.deanonymize_review_rows(rows, blind_key)
    better_by_task = {r["task"]: r["better"] for r in deanon}
    assert better_by_task == {"1": "A", "2": "B", "3": "B", "4": "tie"}


def test_blind_review_pending_without_review_flag(tmp_path):
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "r1"
    _write_run(run_dir, "results.json", "costs.json")

    rc = report.main(["--run", "r1", "--runs-root", str(runs_root), "--out", str(run_dir / "REPORT.md")])
    assert rc == 0
    text = (run_dir / "REPORT.md").read_text(encoding="utf-8")
    assert "Blind Review" in text
    assert "pending" in text


# ----------------------------------------------------------------------------
# --notes: drops the "(operator to fill in)" placeholders
# ----------------------------------------------------------------------------
def test_notes_flag_embeds_verbatim_and_drops_placeholders(tmp_path):
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "r1"
    _write_run(run_dir, "results.json", "costs.json", "decisions.json")
    notes_path = tmp_path / "notes.md"
    notes_path.write_text("Nothing unusual; all decisions dispatched as printed.\n", encoding="utf-8")

    rc = report.main(["--run", "r1", "--runs-root", str(runs_root), "--notes", str(notes_path),
                       "--out", str(run_dir / "REPORT.md")])
    assert rc == 0
    text = (run_dir / "REPORT.md").read_text(encoding="utf-8")

    assert "## Operator Notes" in text
    assert "Nothing unusual; all decisions dispatched as printed." in text
    assert "(operator to fill in)" not in text


def test_routing_decisions_do_not_print_a_dispatch_kind_line(tmp_path):
    """Regression: 'Dispatch: implement' printed the task KIND, not what was
    actually dispatched -- removed entirely; that's what operator notes are for."""
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "r1"
    _write_run(run_dir, "results.json", "costs.json", "decisions.json")

    rc = report.main(["--run", "r1", "--runs-root", str(runs_root), "--out", str(run_dir / "REPORT.md")])
    assert rc == 0
    text = (run_dir / "REPORT.md").read_text(encoding="utf-8")
    assert "Dispatch:" not in text


# ----------------------------------------------------------------------------
# Hard errors / graceful degradation (unchanged behavior, still verified)
# ----------------------------------------------------------------------------
def test_requires_results_json(tmp_path):
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "r1"
    _write_run(run_dir, "costs.json")
    rc = report.main(["--run", "r1", "--runs-root", str(runs_root), "--out", str(run_dir / "REPORT.md")])
    assert rc != 0


def test_requires_costs_json(tmp_path):
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "r1"
    _write_run(run_dir, "results.json")
    rc = report.main(["--run", "r1", "--runs-root", str(runs_root), "--out", str(run_dir / "REPORT.md")])
    assert rc != 0


def test_detects_secrets(tmp_path):
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "r1"
    _write_run(run_dir, "results.json")
    costs = _load("costs.json")
    costs["api_key"] = "sk_test_" + "x" * 20
    (run_dir / "costs.json").write_text(json.dumps(costs), encoding="utf-8")

    rc = report.main(["--run", "r1", "--runs-root", str(runs_root), "--out", str(run_dir / "REPORT.md")])
    assert rc != 0


# ----------------------------------------------------------------------------
# Phase 2: --compare-run (Jev-routed r1 vs Claude-routed r2 vs r2's own baseline)
# ----------------------------------------------------------------------------
def _write_compare_run_pair(runs_root: Path) -> None:
    """r1 gets the real fixtures as-is. r2 is derived from them by swapping the
    decisions for Claude-style records (fixtures/decisions_claude_router.json)
    -- costs.json/results.json are reused unchanged, since this test is about
    the comparison MECHANICS (row layout, which decisions render where), not
    about a real second run having different numbers."""
    _write_run(runs_root / "r1", "results.json", "costs.json", "decisions.json")
    r2_dir = runs_root / "r2"
    _write_run(r2_dir, "results.json", "costs.json")
    (r2_dir / "decisions.json").write_text(
        (FIXTURES / "decisions_claude_router.json").read_text(encoding="utf-8"), encoding="utf-8")


def test_compare_run_renders_three_rows_per_task_with_real_costs(tmp_path):
    runs_root = tmp_path / "runs"
    _write_compare_run_pair(runs_root)

    rc = report.main(["--run", "r2", "--runs-root", str(runs_root), "--compare-run", "r1",
                       "--out", str(runs_root / "r2" / "REPORT.md")])
    assert rc == 0
    text = (runs_root / "r2" / "REPORT.md").read_text(encoding="utf-8")

    assert "## Comparison" in text
    compare_table = text.split("## Comparison", 1)[1].split("### What each router chose", 1)[0]

    task3_rows = [l for l in compare_table.splitlines() if l.startswith("| 3 |")]
    assert len(task3_rows) == 3
    labels = [row.split("|")[2].strip() for row in task3_rows]
    assert labels == ["Jev-routed", "Claude-routed", "Baseline"]

    # task 3's real per-task cost from costs.json fixture: A $1.35, B $2.37
    jev_row, claude_row, baseline_row = task3_rows
    assert "$1.35" in jev_row
    assert "$1.35" in claude_row  # same underlying costs.json in this fixture pairing
    assert "$2.37" in baseline_row

    total_rows = [l for l in compare_table.splitlines() if l.startswith("| **Total**")]
    assert len(total_rows) == 3
    assert any("$3.4739" in l for l in total_rows)
    assert any("$9.0933" in l for l in total_rows)


def test_compare_run_router_choice_table_shows_jev_and_claude(tmp_path):
    runs_root = tmp_path / "runs"
    _write_compare_run_pair(runs_root)

    rc = report.main(["--run", "r2", "--runs-root", str(runs_root), "--compare-run", "r1",
                       "--out", str(runs_root / "r2" / "REPORT.md")])
    assert rc == 0
    text = (runs_root / "r2" / "REPORT.md").read_text(encoding="utf-8")

    choice_section = text.split("### What each router chose", 1)[1].split("##", 1)[0]
    # task 1: Jev's real one-liner vs. Claude's rendered fields
    row1 = next(l for l in choice_section.splitlines() if l.startswith("| 1 |"))
    assert "c-85de41" in row1
    assert "model=sonnet mode=agent" in row1
    # task 3: Jev fanned out to 3xopus; Claude's LATEST (after-probe-equivalent) decision is fable fan-out x3
    row3 = next(l for l in choice_section.splitlines() if l.startswith("| 3 |"))
    assert "fan-out 3xopus" in row3
    assert "model=fable mode=fan-out x3" in row3


def test_compare_run_claude_decisions_render_in_routing_decisions_section(tmp_path):
    runs_root = tmp_path / "runs"
    _write_compare_run_pair(runs_root)

    rc = report.main(["--run", "r2", "--runs-root", str(runs_root), "--compare-run", "r1",
                       "--out", str(runs_root / "r2" / "REPORT.md")])
    assert rc == 0
    text = (runs_root / "r2" / "REPORT.md").read_text(encoding="utf-8")

    assert "- Model: haiku" in text
    assert "- Confidence: 0.95" in text
    assert "Dispatch:" not in text  # still gone for Claude records too


def test_compare_run_flags_a_baseline_mismatch_explicitly(tmp_path):
    runs_root = tmp_path / "runs"
    _write_compare_run_pair(runs_root)

    # perturb r2's own baseline cost (per_task, which the comparison actually
    # sums from) so it disagrees with r1's
    r2_costs = _load("costs.json")
    r2_costs["per_task"]["1"]["B"]["usd"] = 12345.0
    r2_costs["grand_total_usd"]["B"] = 12345.0
    (runs_root / "r2" / "costs.json").write_text(json.dumps(r2_costs), encoding="utf-8")

    rc = report.main(["--run", "r2", "--runs-root", str(runs_root), "--compare-run", "r1",
                       "--out", str(runs_root / "r2" / "REPORT.md")])
    assert rc == 0
    text = (runs_root / "r2" / "REPORT.md").read_text(encoding="utf-8")
    assert "the two runs' baselines (Arm B) differ" in text
    assert "12345" in text


def test_compare_run_missing_run_is_a_hard_error(tmp_path):
    runs_root = tmp_path / "runs"
    _write_run(runs_root / "r2", "results.json", "costs.json")

    rc = report.main(["--run", "r2", "--runs-root", str(runs_root), "--compare-run", "does-not-exist",
                       "--out", str(runs_root / "r2" / "REPORT.md")])
    assert rc != 0


def test_handles_missing_optional_files(tmp_path):
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "r1"
    _write_run(run_dir, "results.json", "costs.json")
    # decisions.json, manifest.json, blind_key.json all absent

    rc = report.main(["--run", "r1", "--runs-root", str(runs_root), "--out", str(run_dir / "REPORT.md")])
    assert rc == 0
    text = (run_dir / "REPORT.md").read_text(encoding="utf-8")
    assert "Arm A Routing Decisions" in text
    assert "Threats to Validity" in text
    assert "Reproduce" in text
