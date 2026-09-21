"""selftest for import_baseline.py: byte-for-byte copy (including .git,
uncommitted changes, and untracked files), refuses a dirty target (before
touching anything), leaves the source run untouched, and marks the target
manifest's imported slots with imported_from."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import import_baseline


def _git(*args: str, cwd) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), check=True,
                           capture_output=True, text=True).stdout


def _init_slot(path: Path, *, extra_file: str | None = None, untracked_file: str | None = None) -> None:
    path.mkdir(parents=True)
    (path / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.name", "ab-harness"],
        ["git", "config", "user.email", "ab-harness@localhost"],
        ["git", "add", "-A"],
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "baseline"],
    ):
        subprocess.run(cmd, cwd=str(path), check=True, capture_output=True, text=True)
    if extra_file:
        # an uncommitted (tracked-modified) change, left uncommitted -- exactly
        # the state a solved-but-never-committed agent copy is expected to be in
        (path / "app.py").write_text(extra_file, encoding="utf-8")
    if untracked_file:
        (path / untracked_file).write_text("# untracked\n", encoding="utf-8")


def _make_run(runs_root: Path, run_id: str, arms=("A", "B"), **slot_kwargs) -> Path:
    run_dir = runs_root / run_id
    for arm in arms:
        for n in (1, 2, 3, 4):
            _init_slot(run_dir / arm / f"task{n}", **slot_kwargs)
    return run_dir


def test_byte_for_byte_copy_including_git_and_untracked_files(tmp_path):
    runs_root = tmp_path / "runs"
    source_dir = _make_run(runs_root, "src", arms=("B",),
                            extra_file="def f():\n    return 2  # fixed\n",
                            untracked_file="NOTES.md")
    target_dir = _make_run(runs_root, "tgt", arms=("A", "B"))

    rc = import_baseline.main(["--run", "tgt", "--from-run", "src", "--arm", "B", "--runs-root", str(runs_root)])
    assert rc == 0

    for n in (1, 2, 3, 4):
        src_slot = source_dir / "B" / f"task{n}"
        tgt_slot = target_dir / "B" / f"task{n}"
        assert (tgt_slot / "app.py").read_text() == (src_slot / "app.py").read_text() == "def f():\n    return 2  # fixed\n"
        assert (tgt_slot / "NOTES.md").exists()
        # .git carried over too: same HEAD, same (uncommitted) status
        assert _git("rev-parse", "HEAD", cwd=tgt_slot) == _git("rev-parse", "HEAD", cwd=src_slot)
        assert _git("status", "--porcelain", "--ignored", cwd=tgt_slot) == \
               _git("status", "--porcelain", "--ignored", cwd=src_slot)

    # Arm A slots in the target are untouched by a B import
    for n in (1, 2, 3, 4):
        assert (target_dir / "A" / f"task{n}" / "app.py").read_text() == "def f():\n    return 1\n"


def test_refuses_a_dirty_target_before_touching_anything(tmp_path):
    runs_root = tmp_path / "runs"
    _make_run(runs_root, "src", arms=("B",))
    target_dir = _make_run(runs_root, "tgt", arms=("B",))
    (target_dir / "B" / "task2" / "stray.txt").write_text("oops\n", encoding="utf-8")

    before = {n: (target_dir / "B" / f"task{n}" / "app.py").read_text() for n in (1, 2, 3, 4)}

    rc = import_baseline.main(["--run", "tgt", "--from-run", "src", "--arm", "B", "--runs-root", str(runs_root)])
    assert rc != 0

    # nothing was copied into ANY slot, not even the ones checked before task 2
    after = {n: (target_dir / "B" / f"task{n}" / "app.py").read_text() for n in (1, 2, 3, 4)}
    assert before == after


def test_refuses_a_target_with_more_than_one_commit(tmp_path):
    runs_root = tmp_path / "runs"
    _make_run(runs_root, "src", arms=("B",))
    target_dir = _make_run(runs_root, "tgt", arms=("B",))
    slot = target_dir / "B" / "task1"
    (slot / "app.py").write_text("def f():\n    return 3\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(slot), check=True, capture_output=True, text=True)
    subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "a second commit"],
                    cwd=str(slot), check=True, capture_output=True, text=True)

    rc = import_baseline.main(["--run", "tgt", "--from-run", "src", "--arm", "B", "--runs-root", str(runs_root)])
    assert rc != 0


def test_source_run_is_never_modified(tmp_path):
    runs_root = tmp_path / "runs"
    source_dir = _make_run(runs_root, "src", arms=("B",), untracked_file="scratch.md")
    _make_run(runs_root, "tgt", arms=("B",))

    before_hashes = {n: import_baseline.tree_hash(source_dir / "B" / f"task{n}") for n in (1, 2, 3, 4)}

    rc = import_baseline.main(["--run", "tgt", "--from-run", "src", "--arm", "B", "--runs-root", str(runs_root)])
    assert rc == 0

    after_hashes = {n: import_baseline.tree_hash(source_dir / "B" / f"task{n}") for n in (1, 2, 3, 4)}
    assert before_hashes == after_hashes


def test_manifest_entries_copied_and_marked_imported_from(tmp_path):
    runs_root = tmp_path / "runs"
    source_dir = _make_run(runs_root, "src", arms=("B",))
    target_dir = _make_run(runs_root, "tgt", arms=("A", "B"))

    source_manifest = {
        f"B/task{n}": [{"arm": "B", "task": n, "role": "engineer", "transcript": f"/fake/b{n}.jsonl"}]
        for n in (1, 2, 3, 4)
    }
    (source_dir / "manifest.json").write_text(json.dumps(source_manifest), encoding="utf-8")
    (target_dir / "manifest.json").write_text(json.dumps({f"A/task{n}": [] for n in (1, 2, 3, 4)}
                                                          | {f"B/task{n}": [] for n in (1, 2, 3, 4)}),
                                               encoding="utf-8")

    rc = import_baseline.main(["--run", "tgt", "--from-run", "src", "--arm", "B", "--runs-root", str(runs_root)])
    assert rc == 0

    target_manifest = json.loads((target_dir / "manifest.json").read_text(encoding="utf-8"))
    for n in (1, 2, 3, 4):
        entries = target_manifest[f"B/task{n}"]
        assert len(entries) == 1
        assert entries[0]["imported_from"] == "src"
        assert entries[0]["transcript"] == f"/fake/b{n}.jsonl"
    # A slots in the target manifest untouched
    assert target_manifest["A/task1"] == []


def test_tree_hash_detects_a_single_byte_difference(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir(); b.mkdir()
    (a / "f.txt").write_text("hello", encoding="utf-8")
    (b / "f.txt").write_text("hellp", encoding="utf-8")
    assert import_baseline.tree_hash(a) != import_baseline.tree_hash(b)


def test_refuses_a_from_run_containing_a_path_separator(tmp_path):
    # "r/../r" is lexically different from "r" (the old identity check was a
    # plain Path equality), but it names the very directory copy_tree_exact
    # is about to rmtree as the source it is about to copy from.
    runs_root = tmp_path / "runs"
    run_dir = _make_run(runs_root, "r", arms=("B",))
    before_hashes = {n: import_baseline.tree_hash(run_dir / "B" / f"task{n}") for n in (1, 2, 3, 4)}

    rc = import_baseline.main(["--run", "r", "--from-run", "r/../r", "--arm", "B", "--runs-root", str(runs_root)])
    assert rc != 0

    after_hashes = {n: import_baseline.tree_hash(run_dir / "B" / f"task{n}") for n in (1, 2, 3, 4)}
    assert before_hashes == after_hashes


def test_refuses_a_run_id_that_escapes_the_runs_root(tmp_path):
    runs_root = tmp_path / "runs"
    _make_run(runs_root, "src", arms=("B",), extra_file="def f():\n    return 99  # from src\n")
    # a pristine-looking run sitting right next to (not under) the runs root
    outside_dir = _make_run(tmp_path, "outside", arms=("B",))
    before = {n: (outside_dir / "B" / f"task{n}" / "app.py").read_text() for n in (1, 2, 3, 4)}

    rc = import_baseline.main(["--run", "../outside", "--from-run", "src", "--arm", "B",
                                "--runs-root", str(runs_root)])
    assert rc != 0

    after = {n: (outside_dir / "B" / f"task{n}" / "app.py").read_text() for n in (1, 2, 3, 4)}
    assert before == after


def test_refuses_a_target_run_dir_that_is_a_symlink_to_the_source(tmp_path):
    # source is pristine on purpose, so the pre-existing pristine check would
    # (wrongly) let this through on its own -- only a resolve-based identity
    # check catches it. "tgt" is a symlink at the RUN level, not the task
    # level, so target_dirs[n] (tgt/B/taskN) is a plain dir reached through a
    # symlinked ancestor: shutil.rmtree happily deletes through it, unlike
    # rmtree on a symlink leaf, which os raises on.
    runs_root = tmp_path / "runs"
    source_dir = _make_run(runs_root, "src", arms=("B",))
    target_link = runs_root / "tgt"
    target_link.symlink_to(source_dir, target_is_directory=True)

    before = {n: (source_dir / "B" / f"task{n}" / "app.py").read_text() for n in (1, 2, 3, 4)}

    rc = import_baseline.main(["--run", "tgt", "--from-run", "src", "--arm", "B", "--runs-root", str(runs_root)])
    assert rc != 0

    # nothing was deleted or replaced -- the symlink itself, and the real
    # source directory it points at, are both untouched
    assert target_link.is_symlink()
    after = {n: (source_dir / "B" / f"task{n}" / "app.py").read_text() for n in (1, 2, 3, 4)}
    assert before == after


def test_refuses_when_untracked_files_are_hidden_by_git_config(tmp_path):
    runs_root = tmp_path / "runs"
    _make_run(runs_root, "src", arms=("B",))
    target_dir = _make_run(runs_root, "tgt", arms=("B",))
    target_slot = target_dir / "B" / "task1"
    (target_slot / "NOTES.txt").write_text("do not delete\n", encoding="utf-8")
    subprocess.run(["git", "config", "status.showUntrackedFiles", "no"],
                    cwd=str(target_slot), check=True, capture_output=True, text=True)

    # sanity: this is exactly the config that blinds a plain `git status
    # --porcelain` to the untracked file
    assert _git("status", "--porcelain", cwd=target_slot) == ""

    rc = import_baseline.main(["--run", "tgt", "--from-run", "src", "--arm", "B", "--runs-root", str(runs_root)])
    assert rc != 0
    assert (target_slot / "NOTES.txt").exists()


def test_refuses_run_ids_that_differ_only_by_case(tmp_path):
    # Path.resolve() does not normalise case. On a case-insensitive
    # filesystem (the default on Mac and Windows), "r" and "R" resolve to
    # two different-looking strings that are nonetheless the same directory
    # on disk -- only os.path.samefile catches that.
    runs_root = tmp_path / "runs"
    run_dir = _make_run(runs_root, "r", arms=("B",))

    if not (runs_root / "R").exists():
        pytest.skip("filesystem is case-sensitive: 'r' and 'R' are different directories here")

    before_hashes = {n: import_baseline.tree_hash(run_dir / "B" / f"task{n}") for n in (1, 2, 3, 4)}

    rc = import_baseline.main(["--run", "r", "--from-run", "R", "--arm", "B", "--runs-root", str(runs_root)])
    assert rc != 0

    after_hashes = {n: import_baseline.tree_hash(run_dir / "B" / f"task{n}") for n in (1, 2, 3, 4)}
    assert before_hashes == after_hashes


def test_refuses_a_slot_level_symlink_between_source_and_target(tmp_path):
    # The run dirs themselves ("src", "tgt") are two distinct real
    # directories -- the run-level identity check passes. Only "tgt/B" is a
    # symlink to "src/B", which the run-level resolve/samefile check cannot
    # see; only a per-slot samefile check catches it.
    runs_root = tmp_path / "runs"
    source_dir = _make_run(runs_root, "src", arms=("B",))
    target_dir = runs_root / "tgt"
    target_dir.mkdir(parents=True)
    (target_dir / "B").symlink_to(source_dir / "B", target_is_directory=True)

    before = {n: (source_dir / "B" / f"task{n}" / "app.py").read_text() for n in (1, 2, 3, 4)}

    rc = import_baseline.main(["--run", "tgt", "--from-run", "src", "--arm", "B", "--runs-root", str(runs_root)])
    assert rc != 0

    assert (target_dir / "B").is_symlink()
    after = {n: (source_dir / "B" / f"task{n}" / "app.py").read_text() for n in (1, 2, 3, 4)}
    assert before == after


def test_refuses_a_source_slot_that_is_a_symlink_to_a_non_git_directory(tmp_path):
    # Not even a git repo -- a fake baseline. Only a plain is_symlink() check
    # on the slot itself catches this; it isn't outside the runs root, isn't
    # the same file as anything, and (being a directory of arbitrary files)
    # isn't judged by is_pristine_baseline at all, since that only runs on
    # the TARGET slot.
    runs_root = tmp_path / "runs"
    source_dir = _make_run(runs_root, "src", arms=("B",))
    target_dir = _make_run(runs_root, "tgt", arms=("B",))

    external_dir = tmp_path / "external"
    external_dir.mkdir()
    (external_dir / "app.py").write_text("def f():\n    return 666  # not a git repo\n", encoding="utf-8")
    shutil.rmtree(source_dir / "B" / "task1")
    (source_dir / "B" / "task1").symlink_to(external_dir, target_is_directory=True)

    before_target_hash = import_baseline.tree_hash(target_dir / "B" / "task1")

    rc = import_baseline.main(["--run", "tgt", "--from-run", "src", "--arm", "B", "--runs-root", str(runs_root)])
    assert rc != 0

    after_target_hash = import_baseline.tree_hash(target_dir / "B" / "task1")
    assert after_target_hash == before_target_hash
    assert not (target_dir / "manifest.json").exists()


def test_refuses_a_target_slot_that_is_a_symlink_to_a_different_source_slot(tmp_path):
    # tgt/B/task3 points at src/B/task2 -- a genuinely pristine repo, just
    # the wrong one. src/task2's different content is baked into its single
    # commit (via amend, not left dirty), so the existing pristine check
    # would (wrongly) accept it on its own -- the old per-slot samefile
    # check only ever compared task N against task N, so it never saw this
    # either; only a plain is_symlink() check on the slot itself does, and
    # it must run for every task before ANY task is copied, or tasks 1-2
    # get replaced first.
    runs_root = tmp_path / "runs"
    source_dir = _make_run(runs_root, "src", arms=("B",))
    target_dir = _make_run(runs_root, "tgt", arms=("B",))

    task2_dir = source_dir / "B" / "task2"
    (task2_dir / "app.py").write_text("def f():\n    return 99  # amended, still pristine\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(task2_dir), check=True, capture_output=True, text=True)
    subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "--amend", "-q", "-m", "baseline"],
                    cwd=str(task2_dir), check=True, capture_output=True, text=True)

    shutil.rmtree(target_dir / "B" / "task3")
    (target_dir / "B" / "task3").symlink_to(source_dir / "B" / "task2", target_is_directory=True)

    before_source = {n: (source_dir / "B" / f"task{n}" / "app.py").read_text() for n in (1, 2, 3, 4)}
    before_target = {n: (target_dir / "B" / f"task{n}" / "app.py").read_text() for n in (1, 2, 4)}

    rc = import_baseline.main(["--run", "tgt", "--from-run", "src", "--arm", "B", "--runs-root", str(runs_root)])
    assert rc != 0

    after_source = {n: (source_dir / "B" / f"task{n}" / "app.py").read_text() for n in (1, 2, 3, 4)}
    assert before_source == after_source
    after_target = {n: (target_dir / "B" / f"task{n}" / "app.py").read_text() for n in (1, 2, 4)}
    assert before_target == after_target
    assert (target_dir / "B" / "task3").is_symlink()


def test_refuses_a_source_arm_directory_that_is_a_symlink(tmp_path):
    runs_root = tmp_path / "runs"
    source_dir = _make_run(runs_root, "src", arms=("B",))
    target_dir = _make_run(runs_root, "tgt", arms=("B",))
    # a second, real run elsewhere inside the runs root -- still "inside",
    # so run-level containment alone would not catch this
    elsewhere_dir = _make_run(runs_root, "elsewhere", arms=("B",),
                               extra_file="def f():\n    return 55  # from elsewhere\n")

    shutil.rmtree(source_dir / "B")
    (source_dir / "B").symlink_to(elsewhere_dir / "B", target_is_directory=True)

    before_target = {n: (target_dir / "B" / f"task{n}" / "app.py").read_text() for n in (1, 2, 3, 4)}
    before_elsewhere = {n: (elsewhere_dir / "B" / f"task{n}" / "app.py").read_text() for n in (1, 2, 3, 4)}

    rc = import_baseline.main(["--run", "tgt", "--from-run", "src", "--arm", "B", "--runs-root", str(runs_root)])
    assert rc != 0

    assert (source_dir / "B").is_symlink()
    after_target = {n: (target_dir / "B" / f"task{n}" / "app.py").read_text() for n in (1, 2, 3, 4)}
    assert before_target == after_target
    after_elsewhere = {n: (elsewhere_dir / "B" / f"task{n}" / "app.py").read_text() for n in (1, 2, 3, 4)}
    assert before_elsewhere == after_elsewhere
