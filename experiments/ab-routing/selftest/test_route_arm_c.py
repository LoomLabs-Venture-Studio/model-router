"""selftest for route_arm_c.py: the rendered prompt passes its own forbidden-
word assertion and carries the ticket/context verbatim; --record accepts a
valid ```json-fenced reply and rejects every invalid shape the spec lists
(prose around the JSON, unknown model, fan-out/shards mismatches, confidence
out of range, a too-long reason, a missing field, an extra field), recording
nothing on any rejection; an after-probe prompt carries the probe findings and
the "set probe_first to false" instruction, and a router that ignores that
instruction is recorded with probe_first forced false and marked ignored, not
rejected. Uses the project's own real briefs/task3.md for the ticket-verbatim
check, and a real throwaway git task copy (like route_arm_a's own selftest)
for the do_build/do_record integration -- build_context() itself is already
covered by test_route_arm_a.py and not re-tested here."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import route_arm_c

REPO_ROOT = Path(__file__).resolve().parents[3]
BRIEF_TASK3 = REPO_ROOT / "experiments" / "ab-routing" / "briefs" / "task3.md"

VALID_REPLY = ('{"model": "sonnet", "mode": "agent", "shards": 1, "probe_first": false, '
               '"verifier": "sonnet", "confidence": 0.85, "reason": "Single endpoint, clear rules."}')


def _init_task_repo(task_dir: Path) -> None:
    task_dir.mkdir(parents=True)
    (task_dir / "app").mkdir()
    (task_dir / "app" / "main.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (task_dir / "tests").mkdir()
    (task_dir / "tests" / "test_ok.py").write_text("def test_a():\n    assert True\n", encoding="utf-8")
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.name", "ab-harness"],
        ["git", "config", "user.email", "ab-harness@localhost"],
        ["git", "add", "-A"],
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "baseline"],
    ):
        subprocess.run(cmd, cwd=str(task_dir), check=True, capture_output=True, text=True)


# ----------------------------------------------------------------------------
# Prompt rendering
# ----------------------------------------------------------------------------
def test_rendered_prompt_passes_forbidden_word_check_and_has_ticket_and_context_verbatim():
    ticket_text = BRIEF_TASK3.read_text(encoding="utf-8")
    context_text = ("Fixture snapshot: 15 tracked files, 139 total Python lines. "
                     "Test command: `pytest`. Tests currently: FAIL (0 passed, 2 failed, 0 errors).")

    prompt = route_arm_c.render_prompt(ticket_text, context_text)
    route_arm_c.assert_no_forbidden_words(prompt)  # must not raise

    assert ticket_text.strip() in prompt
    assert context_text in prompt
    assert route_arm_c.ROLE_SENTENCE in prompt
    assert route_arm_c.OBJECTIVE_SENTENCE in prompt
    assert route_arm_c.REPLY_INSTRUCTIONS in prompt


def test_assert_no_forbidden_words_actually_fires():
    with pytest.raises(ValueError, match="experiment"):
        route_arm_c.assert_no_forbidden_words("this is part of an experiment")
    with pytest.raises(ValueError, match="jev"):
        route_arm_c.assert_no_forbidden_words("Jev scored this task")


def test_after_probe_prompt_has_findings_and_instruction():
    prompt = route_arm_c.render_prompt("ticket text", "context text", probe_text="found X and Y")
    assert "found X and Y" in prompt
    assert "A sizing pass has already been done; set probe_first to false." in prompt
    assert "Findings from a read-only sizing pass" in prompt


# ----------------------------------------------------------------------------
# Reply parsing
# ----------------------------------------------------------------------------
def test_extract_json_object_accepts_bare_json():
    obj = route_arm_c.extract_json_object(VALID_REPLY)
    assert obj["model"] == "sonnet"


def test_extract_json_object_accepts_fenced_json():
    fenced = f"```json\n{VALID_REPLY}\n```"
    obj = route_arm_c.extract_json_object(fenced)
    assert obj["model"] == "sonnet"


def test_extract_json_object_rejects_prose_around_json():
    with pytest.raises(ValueError):
        route_arm_c.extract_json_object(f"Sure, here you go:\n```json\n{VALID_REPLY}\n```")
    with pytest.raises(ValueError):
        route_arm_c.extract_json_object(f"{VALID_REPLY}\nHope that helps!")


# ----------------------------------------------------------------------------
# Decision validation -- every rejection the spec lists
# ----------------------------------------------------------------------------
def _valid_obj(**overrides) -> dict:
    obj = {"model": "sonnet", "mode": "agent", "shards": 1, "probe_first": False,
           "verifier": None, "confidence": 0.8, "reason": "ok"}
    obj.update(overrides)
    return obj


def test_validate_decision_accepts_a_valid_object():
    fields = route_arm_c.validate_decision(_valid_obj(), after_probe=False)
    assert fields["model"] == "sonnet"
    assert fields["probe_first_ignored"] is False


def test_validate_decision_rejects_unknown_model():
    with pytest.raises(ValueError, match="model"):
        route_arm_c.validate_decision(_valid_obj(model="gpt4"), after_probe=False)


def test_validate_decision_rejects_fanout_with_shards_1():
    with pytest.raises(ValueError, match="fan-out"):
        route_arm_c.validate_decision(_valid_obj(mode="fan-out", shards=1), after_probe=False)


def test_validate_decision_rejects_agent_with_shards_3():
    with pytest.raises(ValueError, match="agent"):
        route_arm_c.validate_decision(_valid_obj(mode="agent", shards=3), after_probe=False)


def test_validate_decision_accepts_fanout_with_shards_2_or_more():
    fields = route_arm_c.validate_decision(_valid_obj(mode="fan-out", shards=2), after_probe=False)
    assert fields["shards"] == 2


def test_validate_decision_rejects_confidence_out_of_range():
    with pytest.raises(ValueError, match="confidence"):
        route_arm_c.validate_decision(_valid_obj(confidence=1.5), after_probe=False)


def test_validate_decision_rejects_a_61_word_reason():
    long_reason = " ".join(f"word{i}" for i in range(61))
    with pytest.raises(ValueError, match="60 words"):
        route_arm_c.validate_decision(_valid_obj(reason=long_reason), after_probe=False)


def test_validate_decision_accepts_a_60_word_reason():
    reason = " ".join(f"word{i}" for i in range(60))
    fields = route_arm_c.validate_decision(_valid_obj(reason=reason), after_probe=False)
    assert fields["reason"] == reason


def test_validate_decision_rejects_missing_field():
    obj = _valid_obj()
    del obj["verifier"]
    with pytest.raises(ValueError, match="missing"):
        route_arm_c.validate_decision(obj, after_probe=False)


def test_validate_decision_rejects_extra_field():
    with pytest.raises(ValueError, match="unexpected"):
        route_arm_c.validate_decision(_valid_obj(extra_field=True), after_probe=False)


def test_validate_decision_rejects_bad_verifier():
    with pytest.raises(ValueError, match="verifier"):
        route_arm_c.validate_decision(_valid_obj(verifier="gpt4"), after_probe=False)


def test_after_probe_records_probe_first_true_as_ignored_not_rejected():
    fields = route_arm_c.validate_decision(_valid_obj(probe_first=True), after_probe=True)
    assert fields["probe_first"] is False
    assert fields["probe_first_ignored"] is True


def test_non_after_probe_probe_first_true_is_not_flagged_ignored():
    fields = route_arm_c.validate_decision(_valid_obj(probe_first=True), after_probe=False)
    assert fields["probe_first"] is True
    assert fields["probe_first_ignored"] is False


# ----------------------------------------------------------------------------
# do_build / do_record integration
# ----------------------------------------------------------------------------
def test_build_then_record_end_to_end(tmp_path):
    runs_root = tmp_path / "runs"
    task_dir = runs_root / "r" / "A" / "task3"
    _init_task_repo(task_dir)

    build_args = route_arm_c.build_arg_parser().parse_args([
        "--run", "r", "--task", "3", "--brief", str(BRIEF_TASK3), "--runs-root", str(runs_root),
    ])
    rc = route_arm_c.do_build(build_args)
    assert rc == 0

    prompt_dir = route_arm_c.default_prompt_dir(runs_root, "r")
    prompt_path = prompt_dir / "task3_router_prompt.md"
    assert prompt_path.exists()
    prompt_text = prompt_path.read_text(encoding="utf-8")
    assert BRIEF_TASK3.read_text(encoding="utf-8").strip() in prompt_text

    reply_path = tmp_path / "reply.txt"
    reply_path.write_text(f"```json\n{VALID_REPLY}\n```", encoding="utf-8")

    record_args = route_arm_c.build_arg_parser().parse_args([
        "--run", "r", "--task", "3", "--record", str(reply_path), "--runs-root", str(runs_root),
    ])
    rc = route_arm_c.do_record(record_args)
    assert rc == 0

    decisions = json.loads((runs_root / "r" / "decisions.json").read_text(encoding="utf-8"))
    assert len(decisions) == 1
    rec = decisions[0]
    assert rec["router"] == "claude"
    assert rec["arm"] == "A"
    assert rec["task"] == 3
    assert rec["model"] == "sonnet"
    assert rec["prompt_path"] == str(prompt_path)
    assert rec["prompt_sha256"] == route_arm_c.hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()


def test_nothing_is_recorded_when_record_is_rejected(tmp_path):
    runs_root = tmp_path / "runs"
    task_dir = runs_root / "r" / "A" / "task3"
    _init_task_repo(task_dir)

    build_args = route_arm_c.build_arg_parser().parse_args([
        "--run", "r", "--task", "3", "--brief", str(BRIEF_TASK3), "--runs-root", str(runs_root),
    ])
    assert route_arm_c.do_build(build_args) == 0

    bad_reply_path = tmp_path / "bad_reply.txt"
    bad_reply_path.write_text('{"model": "gpt4", "mode": "agent", "shards": 1, "probe_first": false, '
                               '"verifier": null, "confidence": 0.8, "reason": "ok"}', encoding="utf-8")
    record_args = route_arm_c.build_arg_parser().parse_args([
        "--run", "r", "--task", "3", "--record", str(bad_reply_path), "--runs-root", str(runs_root),
    ])
    rc = route_arm_c.do_record(record_args)
    assert rc != 0
    assert not (runs_root / "r" / "decisions.json").exists()
