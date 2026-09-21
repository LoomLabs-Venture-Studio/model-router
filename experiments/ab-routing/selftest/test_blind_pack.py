"""selftest for blind_pack.py: builds tiny fake runs with different uncommitted
edits and untracked files, asserts the review-dir (OUTSIDE the run dir) gets
both diffs, both tickets rendered with {PROJECT_DIR} replaced, and the exact
CTO-written REVIEW_PROMPT.md; no absolute paths/run ids/arm segments/"arm"/
"experiment"/"ab-routing" anywhere in it; task copies are untouched; second
run refuses; same seed gives same mapping; the leak check actually catches a
real leak (not a no-op)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import blind_pack

REPO_ROOT = Path(__file__).resolve().parents[3]


def _git(*args: str, cwd=None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                           capture_output=True, text=True).stdout


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "file.txt").write_text("baseline\n", encoding="utf-8")
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.name", "t"],
        ["git", "config", "user.email", "t@t"],
        ["git", "add", "-A"],
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "baseline"],
    ):
        subprocess.run(cmd, cwd=str(path), check=True, capture_output=True, text=True)


def _make_run(run_dir: Path, modify: bool = True) -> None:
    for arm in ("A", "B"):
        for task_n in (1, 2, 3, 4):
            task_dir = run_dir / arm / f"task{task_n}"
            _init_repo(task_dir)
            if modify:
                (task_dir / "file.txt").write_text("modified\n", encoding="utf-8")
                (task_dir / "new_file.py").write_text("# new\n", encoding="utf-8")


def test_review_dir_gets_diffs_tickets_and_prompt_outside_the_run_dir(tmp_path):
    runs_root = tmp_path / "runs"
    run_id = "test1"
    run_dir = runs_root / run_id
    _make_run(run_dir)

    rc = blind_pack.main(["--run", run_id, "--runs-root", str(runs_root), "--seed", "42"])
    assert rc == 0

    review_dir = blind_pack.default_review_dir(runs_root, run_id)
    assert review_dir.is_dir()
    # OUTSIDE the run directory tree (the whole point of --review-dir)
    assert run_dir not in review_dir.parents and review_dir != run_dir

    for task_n in (1, 2, 3, 4):
        for label in ("X", "Y"):
            diff_path = review_dir / f"task{task_n}_{label}.diff"
            assert diff_path.exists(), f"missing {diff_path}"
            diff_text = diff_path.read_text(encoding="utf-8")
            assert "file.txt" in diff_text
            assert "new_file.py" in diff_text
        ticket_path = review_dir / f"ticket{task_n}.md"
        assert ticket_path.exists(), f"missing {ticket_path}"

    assert (review_dir / "REVIEW_PROMPT.md").exists()

    # run_dir itself no longer gets a blind/ folder -- only blind_key.json
    assert not (run_dir / "blind").exists()
    assert (run_dir / "blind_key.json").exists()


def test_review_prompt_matches_the_cto_written_template_verbatim(tmp_path):
    runs_root = tmp_path / "runs"
    run_id = "test_prompt"
    _make_run(runs_root / run_id, modify=False)

    rc = blind_pack.main(["--run", run_id, "--runs-root", str(runs_root), "--seed", "1"])
    assert rc == 0

    review_dir = blind_pack.default_review_dir(runs_root, run_id)
    prompt_text = (review_dir / "REVIEW_PROMPT.md").read_text(encoding="utf-8")
    assert prompt_text == blind_pack.REVIEW_PROMPT_TEMPLATE
    # the old template's giveaway line must be gone
    assert "arms, models, routing, cost, or the experiment" not in prompt_text
    # and the new one names the actual files
    assert "ticket1.md" in prompt_text
    assert "task<n>_X.diff" in prompt_text


def test_ticket_renders_brief_with_project_dir_substituted(tmp_path):
    runs_root = tmp_path / "runs"
    run_id = "test_ticket"
    _make_run(runs_root / run_id, modify=False)

    rc = blind_pack.main(["--run", run_id, "--runs-root", str(runs_root), "--seed", "1"])
    assert rc == 0

    review_dir = blind_pack.default_review_dir(runs_root, run_id)
    ticket1 = (review_dir / "ticket1.md").read_text(encoding="utf-8")
    brief1 = (REPO_ROOT / "experiments" / "ab-routing" / "briefs" / "task1.md").read_text(encoding="utf-8")

    assert "{PROJECT_DIR}" not in ticket1
    assert blind_pack.FAKE_PROJECT_DIR in ticket1
    assert ticket1 == brief1.replace("{PROJECT_DIR}", blind_pack.FAKE_PROJECT_DIR)


def test_custom_review_dir_flag(tmp_path):
    runs_root = tmp_path / "runs"
    run_id = "test_custom"
    _make_run(runs_root / run_id, modify=False)
    custom_dir = tmp_path / "somewhere_else"

    rc = blind_pack.main(["--run", run_id, "--runs-root", str(runs_root), "--seed", "1",
                           "--review-dir", str(custom_dir)])
    assert rc == 0
    assert (custom_dir / "REVIEW_PROMPT.md").exists()
    assert (custom_dir / "ticket1.md").exists()


def test_no_leaks_anywhere_in_the_review_dir(tmp_path):
    runs_root = tmp_path / "runs"
    run_id = "test_leaks"
    run_dir = runs_root / run_id
    _make_run(run_dir)

    rc = blind_pack.main(["--run", run_id, "--runs-root", str(runs_root), "--seed", "7"])
    assert rc == 0

    review_dir = blind_pack.default_review_dir(runs_root, run_id)
    banned = ["/A/task", "/B/task", "ab-routing", "experiment", str(run_dir)]
    for path in review_dir.iterdir():
        text = path.read_text(encoding="utf-8")
        for word in banned:
            assert word not in text, f"{path.name} leaks {word!r}"
        assert not blind_pack.LEAK_PATTERNS[-1][0].search(text), f"{path.name} leaks the word 'arm'"


def test_check_no_leaks_catches_a_real_leak_not_a_noop():
    """The leak check itself must actually fire -- verified directly, not just
    trusted because a real run happened to come out clean."""
    run_dir = Path("/fake/run/dir")
    with pytest.raises(ValueError, match="ab-routing"):
        blind_pack.check_no_leaks("mentions the ab-routing harness", run_dir, "test")
    with pytest.raises(ValueError, match="experiment"):
        blind_pack.check_no_leaks("part of the experiment", run_dir, "test")
    with pytest.raises(ValueError, match="arm"):
        blind_pack.check_no_leaks("Arm A did better here", run_dir, "test")
    with pytest.raises(ValueError):
        blind_pack.check_no_leaks("see /A/task3/app/main.py", run_dir, "test")
    with pytest.raises(ValueError):
        blind_pack.check_no_leaks(f"lives at {run_dir}/A/task1", run_dir, "test")
    # innocent words containing "arm" as a substring must NOT trip the check
    blind_pack.check_no_leaks("the farm system uses a charm and an alarm", run_dir, "test")


def test_mapping_is_correct(tmp_path):
    runs_root = tmp_path / "runs"
    run_id = "test2"
    _make_run(runs_root / run_id, modify=False)

    rc = blind_pack.main(["--run", run_id, "--runs-root", str(runs_root), "--seed", "123"])
    assert rc == 0

    key_data = json.loads((runs_root / run_id / "blind_key.json").read_text(encoding="utf-8"))
    mapping = key_data["tasks"]
    for task_id in mapping:
        x_arm, y_arm = mapping[task_id]["X"], mapping[task_id]["Y"]
        assert {x_arm, y_arm} == {"A", "B"}


def test_task_copies_remain_untouched(tmp_path):
    runs_root = tmp_path / "runs"
    run_id = "test3"
    run_dir = runs_root / run_id
    _make_run(run_dir)

    before = {}
    for arm in ("A", "B"):
        for task_n in (1, 2, 3, 4):
            task_dir = run_dir / arm / f"task{task_n}"
            before[(arm, task_n)] = (_git("rev-parse", "HEAD", cwd=task_dir).strip(),
                                      _git("status", "--porcelain", "--ignored", cwd=task_dir))

    rc = blind_pack.main(["--run", run_id, "--runs-root", str(runs_root), "--seed", "999"])
    assert rc == 0

    after = {}
    for arm in ("A", "B"):
        for task_n in (1, 2, 3, 4):
            task_dir = run_dir / arm / f"task{task_n}"
            after[(arm, task_n)] = (_git("rev-parse", "HEAD", cwd=task_dir).strip(),
                                     _git("status", "--porcelain", "--ignored", cwd=task_dir))

    assert before == after


def test_refuses_overwrite(tmp_path):
    runs_root = tmp_path / "runs"
    run_id = "test4"
    _make_run(runs_root / run_id, modify=False)

    rc1 = blind_pack.main(["--run", run_id, "--runs-root", str(runs_root), "--seed", "1"])
    assert rc1 == 0
    rc2 = blind_pack.main(["--run", run_id, "--runs-root", str(runs_root), "--seed", "2"])
    assert rc2 != 0


def test_same_seed_gives_same_mapping(tmp_path):
    runs_root = tmp_path / "runs"
    mappings = []
    for run_id in ("seed_test_a", "seed_test_b"):
        _make_run(runs_root / run_id, modify=False)
        rc = blind_pack.main(["--run", run_id, "--runs-root", str(runs_root), "--seed", "777"])
        assert rc == 0
        key_data = json.loads((runs_root / run_id / "blind_key.json").read_text(encoding="utf-8"))
        mappings.append(key_data["tasks"])
    assert mappings[0] == mappings[1]
