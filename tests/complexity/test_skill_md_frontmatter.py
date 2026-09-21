"""F9 (#28, PLAYBOOK.md "skill publish-hardening", Task F): SKILL.md's
`allowed-tools` frontmatter must be a YAML list holding BOTH the unquoted and
the quoted form of the route.py command.

Before this fix, `allowed-tools` was a single scalar:
    allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/route.py *)
Claude Code's permissions docs say a Bash rule matches "everything before the
first `*`, as written" -- so a documented command quoting the executable path
(the shape SKILL.md's own workflow section already uses, e.g.
`"${CLAUDE_SKILL_DIR}/scripts/route.py" route ...`) would not match this
unquoted rule and would prompt for approval every time. The fix adds the
quoted form as a second list entry rather than replacing the first, so a
caller using either quoting style is covered.

No PyYAML in this environment (and the skill itself stays stdlib-only, so
adding a YAML dependency just to test its own frontmatter would be an odd
trade), so this test parses the small, well-known frontmatter shape directly
rather than pulling in a parser: a `---`-delimited block with three top-level
keys (name, description, allowed-tools), the last a `- `-prefixed block
sequence. It also pins `name` and `description` byte-for-byte against the
value the sprint's PLAYBOOK.md required be left untouched by this fix.
"""
from __future__ import annotations

import re
from pathlib import Path

SKILL_MD = Path(__file__).resolve().parents[2] / "skills" / "complexity" / "SKILL.md"

# Byte-for-byte pin: F9 required allowed-tools to change without touching
# these two fields. Captured from the file as it stood at the start of this
# fix; a change to either here means SKILL.md's frontmatter shifted in a way
# this sprint's constraints didn't call for.
EXPECTED_NAME = "complexity"
EXPECTED_DESCRIPTION = (
    "Measure a task's complexity on a 7-dimension rubric, then route it. Decides whether "
    "to do the work inline, hand it to one subagent, or fan out to several, and picks the "
    "model tier (haiku, sonnet, opus, fable) plus effort, verifier, and human gate for each. "
    "Use this before spawning any Agent or subagent, before starting any coding, research, "
    "or analysis task with more than one step, whenever you are choosing which model should "
    "handle a piece of work, and whenever the user asks how hard something is, whether to "
    'fan out, or which model to use. Also use it when the user says /complexity, "triage '
    'this", "size this", "route this", or "what model for this". Skip only for '
    "conversational questions and single-step edits."
)


def _frontmatter_lines() -> list[str]:
    text = SKILL_MD.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?\n)---\n", text, re.DOTALL)
    assert m, "SKILL.md must start with a --- delimited YAML frontmatter block"
    return m.group(1).splitlines()


def test_frontmatter_is_well_formed_and_has_exactly_the_expected_keys():
    lines = _frontmatter_lines()
    top_level_keys = [ln.split(":", 1)[0] for ln in lines if re.match(r"^[a-zA-Z-]+:", ln)]
    assert top_level_keys == ["name", "description", "allowed-tools", "license"]


def test_license_is_mit():
    """F14 (board decision, PLAYBOOK.md "skill publish-hardening", Task F)."""
    lines = _frontmatter_lines()
    assert "license: MIT" in lines


def test_name_and_description_are_byte_identical_to_before_this_fix():
    lines = _frontmatter_lines()
    name_line = next(ln for ln in lines if ln.startswith("name:"))
    desc_line = next(ln for ln in lines if ln.startswith("description:"))
    assert name_line == f"name: {EXPECTED_NAME}"
    assert desc_line == f"description: {EXPECTED_DESCRIPTION}"


def test_allowed_tools_is_a_yaml_list_with_both_quoted_and_unquoted_forms():
    lines = _frontmatter_lines()
    idx = next(i for i, ln in enumerate(lines) if ln.startswith("allowed-tools:"))
    assert lines[idx] == "allowed-tools:", "allowed-tools must be a block (list), not a scalar"

    items = []
    for ln in lines[idx + 1:]:
        if re.match(r"^[a-zA-Z-]+:", ln):  # next top-level key: end of the list
            break
        m = re.match(r"^\s*-\s+(.*)$", ln)
        assert m, f"expected a '- ' list item, got {ln!r}"
        items.append(m.group(1))

    assert "Bash(${CLAUDE_SKILL_DIR}/scripts/route.py *)" in items
    assert 'Bash("${CLAUDE_SKILL_DIR}/scripts/route.py" *)' in items
    assert len(items) == 2


def test_allowed_tools_entries_match_up_to_their_own_first_star():
    """Sanity check on the shape Claude Code's matcher cares about: each entry
    must literally start with "Bash(" and its text up to (not including) the
    first "*" must be exactly the unquoted or the quoted route.py path -- the
    part a Bash permission rule matches "as written", per Claude Code's
    permissions docs."""
    expected_prefixes = {
        "Bash(${CLAUDE_SKILL_DIR}/scripts/route.py ",
        'Bash("${CLAUDE_SKILL_DIR}/scripts/route.py" ',
    }
    lines = _frontmatter_lines()
    idx = next(i for i, ln in enumerate(lines) if ln.startswith("allowed-tools:"))
    seen_prefixes = set()
    for ln in lines[idx + 1:]:
        if re.match(r"^[a-zA-Z-]+:", ln):
            break
        item = re.match(r"^\s*-\s+(.*)$", ln).group(1)
        assert item.startswith("Bash(")
        seen_prefixes.add(item.split("*", 1)[0])
    assert seen_prefixes == expected_prefixes
