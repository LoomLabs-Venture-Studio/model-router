"""B2 (#7): the keyword heuristic must be linear-time and input-bounded.

Before this sprint, `score_heuristic`'s filename regex (an unanchored `[\\w/-]+`
character class that includes the separator characters it is scanning between,
followed by a literal `.` and an extension alternation) could backtrack
catastrophically: a long run of characters with no matching extension made the
engine retry every possible split point at every start position. A 100,000-character
task with no real content reproduced a multi-minute hang (see PLAYBOOK.md, sprint
"skill publish-hardening", criterion B2 / Codex finding #7).

These tests pin two independent guarantees: every heuristic pattern finishes in
linear time on adversarial input (no real fix would need more than a couple of
seconds even at 1,000,000 characters), and the scorer additionally bounds how much
of the task text it looks at, so pathological input can never cost more than a
fixed amount of work regardless of the regex engine's behavior.
"""
from __future__ import annotations

import time

import pytest

ADVERSARIAL_INPUTS = {
    "all-a": "a" * 1_000_000,
    "a-slash": "a/" * 500_000,
    "a-dash-underscore": "a-b_" * 250_000,
    "word-dot-word": "word.word " * 100_000,
}


@pytest.mark.parametrize("name,text", sorted(ADVERSARIAL_INPUTS.items()))
def test_heuristic_is_linear_time(route_mod, name, text):
    assert len(text) == 1_000_000, f"{name}: fixture must be exactly 1,000,000 chars"
    start = time.monotonic()
    route_mod.score_heuristic(text)
    elapsed = time.monotonic() - start
    assert elapsed < 2.0, f"{name}: score_heuristic took {elapsed:.2f}s on 1,000,000 chars"


def test_heuristic_bounds_its_input(route_mod):
    """The scorer scores at most the first N characters of the task, N a named
    module constant. Padding a task past that bound with more of the same
    character must not change the score: everything past N is invisible to it."""
    limit = route_mod.HEURISTIC_TASK_MAX_CHARS
    assert isinstance(limit, int) and limit > 0

    base = "fix the bug in `app/server.py` " + "x" * (limit - 40)
    scores_at_limit, unsure_at_limit, kind_at_limit, truncated_at_limit = (
        route_mod.score_heuristic(base)
    )
    padded = base + "y" * 5000
    scores_padded, unsure_padded, kind_padded, truncated_padded = route_mod.score_heuristic(padded)

    assert scores_at_limit == scores_padded
    assert unsure_at_limit == unsure_padded
    assert kind_at_limit == kind_padded
    assert truncated_padded is True


def test_heuristic_returns_truncated_flag(route_mod):
    short = "fix the bug in `app/server.py`"
    scores, unsure, kind, truncated = route_mod.score_heuristic(short)
    assert truncated is False
    assert isinstance(scores, dict) and isinstance(unsure, set) and isinstance(kind, str)


def test_heuristic_filename_detection_still_works(route_mod):
    """The linear-time fix must not regress the thing the regex was for: a real
    filename mention should still be picked up as scope evidence."""
    scores, unsure, kind, _truncated = route_mod.score_heuristic(
        "fix the bug in `app/server.py` please"
    )
    assert scores["S"] == 1
    assert "S" in unsure


def test_cli_score_reports_truncation_without_disturbing_the_route_line(run_cli):
    """(b): note the truncation in the existing 'note:' line, and only there --
    the routing one-liner that follows (what the hook parses) must be unaffected."""
    huge_task = "a" * 200_000
    result = run_cli("score", "--task", huge_task, "--route", "--no-log", "--kind", "implement")
    assert result.returncode == 0, result.stderr
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    note_lines = [l for l in lines if l.startswith("note:")]
    assert note_lines, "expected the heuristic's note: line"
    assert "truncat" in note_lines[0]
    # last line is still the plain routing one-liner, shaped like a decision line
    assert lines[-1].startswith("[c-") and lines[-1].endswith("]")


def test_route_score_finishes_well_under_budget_on_adversarial_input(run_cli):
    """Reproduces the exact smoke test named in the sprint spec: this must not
    take anywhere near 8 seconds any more."""
    huge_task = "a" * 100_000
    start = time.monotonic()
    result = run_cli("score", "--task", huge_task, "--no-log")
    elapsed = time.monotonic() - start
    assert result.returncode == 0, result.stderr
    assert elapsed < 2.0, f"took {elapsed:.2f}s"
