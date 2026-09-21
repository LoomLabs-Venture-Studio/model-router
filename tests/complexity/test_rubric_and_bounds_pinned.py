"""F11 (PLAYBOOK.md "skill publish-hardening", Task F):
  - the embedded RUBRIC (what Jev is asked and what `rubric` prints) is compared
    against references/rubric.md, so reverting an S, P, or I anchor to different
    wording fails -- these three are singled out because CLAUDE.md's Known Issue
    9 flags them as the ones Jev calibration actually depends on (P is
    over-scored on small repos; I is unanswerable from a brief).
  - LOG_TAIL_BYTES and the hook's HOOK_MAX_PROMPT_CHARS are pinned in the
    GROWING direction only (an increase is fine; the numbers must never shrink
    below a floor small enough to be clearly a mistake, not a tuning choice).
  - the 1MB hook test (test_hook.py) additionally asserts a decision line IS
    printed and that the prompt route.py actually received was bounded.

The rubric.md comparison parses its markdown tables properly (row by row, by
level number) rather than grepping for keywords, per the criterion's own
instruction. S's anchor text is identical, word for word, between the two
files, so an exact match is used there -- any edit at all fails it. P and I
are deliberately paraphrased between the detailed doc and the condensed
API/CLI text, so exact equality does not apply to them; instead each anchor
level is reduced to its significant words (number words normalized to
digits, stopwords dropped) and the two versions must share most of them. A
threshold of 0.25 sits comfortably below every level's current overlap
(the lowest is I's level 0 at ~0.31) while still failing hard on a genuinely
different anchor -- swapping in a WRONG level's wording, or another
dimension's, drives the overlap toward zero.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUBRIC_MD = REPO_ROOT / "skills" / "complexity" / "references" / "rubric.md"

_STOPWORDS = {
    "a", "an", "the", "or", "and", "of", "to", "is", "in", "that", "this", "with", "for",
    "on", "it", "as", "from", "at", "by", "be", "are", "need", "no", "yes", "not",
}
_NUM_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10",
}
_JACCARD_FLOOR = 0.25


def _significant_words(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    out = set()
    for w in words:
        w = _NUM_WORDS.get(w, w)
        if w in _STOPWORDS or len(w) <= 1:
            continue
        out.add(w)
    return out


def _parse_rubric_md_anchors(dim: str) -> dict[int, str]:
    """The four "Anchor" column cells for `dim`'s table in rubric.md, keyed by
    level. Parses the markdown table row by row (`| level | anchor | examples |`)
    rather than grepping for any particular keyword."""
    text = RUBRIC_MD.read_text(encoding="utf-8")
    heading = re.search(rf"^## {re.escape(dim)} ·.*$", text, re.MULTILINE)
    assert heading, f"no '## {dim} ·' heading found in rubric.md"
    section = text[heading.end():]
    next_heading = re.search(r"^## ", section, re.MULTILINE)
    if next_heading:
        section = section[:next_heading.start()]
    rows = re.findall(r"^\|\s*(\d)\s*\|(.+?)\|(.+?)\|\s*$", section, re.MULTILINE)
    anchors = {int(level): anchor.strip() for level, anchor, _examples in rows}
    assert set(anchors) == {0, 1, 2, 3}, f"expected levels 0-3 for {dim}, got {sorted(anchors)}"
    return anchors


def test_rubric_md_has_the_expected_table_shape_for_s_p_i():
    """Sanity check on the parser itself, independent of route.py: each of S,
    P, I has exactly one heading and a 4-row table in rubric.md."""
    for dim in ["S", "P", "I"]:
        anchors = _parse_rubric_md_anchors(dim)
        assert all(anchors[level] for level in range(4))


def test_rubric_s_anchors_are_byte_identical_to_rubric_md(route_mod):
    """S's anchor wording is identical between rubric.md and the embedded
    RUBRIC (both the Jev-facing and `rubric`-printed text) -- so this dimension
    gets the strictest possible check: any edit to either copy fails it."""
    anchors_md = _parse_rubric_md_anchors("S")
    _, levels = route_mod.RUBRIC["S"]
    for level in range(4):
        assert levels[level] == anchors_md[level], (
            f"S level {level} diverged from rubric.md:\n"
            f"  RUBRIC:    {levels[level]!r}\n"
            f"  rubric.md: {anchors_md[level]!r}"
        )


def test_rubric_p_and_i_anchors_have_not_drifted_from_rubric_md(route_mod):
    """P and I are deliberately paraphrased (condensed CLI/API wording vs.
    detailed doc prose), so this checks semantic overlap rather than exact
    text -- but a genuinely reverted or swapped anchor (wrong level, wrong
    dimension, or unrelated wording) drives the overlap toward zero and fails."""
    mismatches = []
    for dim in ["P", "I"]:
        anchors_md = _parse_rubric_md_anchors(dim)
        _, levels = route_mod.RUBRIC[dim]
        for level in range(4):
            md_words = _significant_words(anchors_md[level])
            code_words = _significant_words(levels[level])
            union = md_words | code_words
            jaccard = len(md_words & code_words) / len(union) if union else 1.0
            if jaccard < _JACCARD_FLOOR:
                mismatches.append((dim, level, jaccard, anchors_md[level], levels[level]))
    assert not mismatches, (
        "rubric.md and RUBRIC have diverged for: " +
        "; ".join(f"{d} level {lv} (jaccard={j:.2f}): md={md!r} code={code!r}"
                  for d, lv, j, md, code in mismatches)
    )


# ----------------------------------------------------------------------------
# Bounds pinned in the growing direction only
# ----------------------------------------------------------------------------
def test_log_tail_bytes_is_at_most_4_mib(route_mod):
    assert route_mod.LOG_TAIL_BYTES <= 4 * 1024 * 1024, (
        "LOG_TAIL_BYTES shrank below a sane floor -- it may only grow from here"
    )
    assert route_mod.LOG_TAIL_BYTES >= 1024, "LOG_TAIL_BYTES is implausibly tiny"


def test_hook_max_prompt_chars_is_at_most_16000():
    hook_sh = (REPO_ROOT / "skills" / "complexity" / "scripts" / "hooks" / "prompt-route.sh").read_text(
        encoding="utf-8"
    )
    m = re.search(r"HOOK_MAX_PROMPT_CHARS\s*=\s*(\d+)", hook_sh)
    assert m, "HOOK_MAX_PROMPT_CHARS constant not found in prompt-route.sh"
    value = int(m.group(1))
    assert value <= 16_000, "HOOK_MAX_PROMPT_CHARS shrank below a sane floor -- it may only grow from here"
    assert value >= 100, "HOOK_MAX_PROMPT_CHARS is implausibly tiny"
