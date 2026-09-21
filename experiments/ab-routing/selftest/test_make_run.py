"""selftest for make_run.py: each of the 8 copies is a clean one-commit git repo
with no shop.db/__pycache__/.pytest_cache, the tracked fixture is never touched,
and a repeated run id is refused."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import make_run

REPO_ROOT = Path(__file__).resolve().parents[3]


def _git(*args: str, cwd=None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                           capture_output=True, text=True).stdout


def test_make_run_creates_eight_clean_copies(tmp_path):
    runs_root = tmp_path / "runs"
    before = _git("status", "--porcelain", "evals/fixture", cwd=REPO_ROOT)
    assert before == "", "evals/fixture must be clean before the test runs"

    rc = make_run.main(["--run", "t1", "--runs-root", str(runs_root)])
    assert rc == 0

    run_dir = runs_root / "t1"
    slots = [run_dir / arm / f"task{n}" for arm in ("A", "B") for n in (1, 2, 3, 4)]
    assert len(slots) == 8

    for slot in slots:
        assert slot.is_dir(), slot
        assert (slot / ".git").is_dir()

        log = _git("log", "--format=%s", cwd=slot).strip().splitlines()
        assert log == ["baseline"], f"{slot} should have exactly one 'baseline' commit"

        assert not (slot / "shop.db").exists()
        assert not any(slot.rglob("__pycache__"))
        assert not any(slot.rglob(".pytest_cache"))

        # nothing untracked or modified inside the slot's own repo
        assert _git("status", "--porcelain", cwd=slot) == ""

    after = _git("status", "--porcelain", "evals/fixture", cwd=REPO_ROOT)
    assert after == "", "make_run.py must never modify the tracked fixture"


def test_make_run_refuses_to_overwrite_existing_run(tmp_path):
    runs_root = tmp_path / "runs"
    assert make_run.main(["--run", "dup", "--runs-root", str(runs_root)]) == 0
    rc = make_run.main(["--run", "dup", "--runs-root", str(runs_root)])
    assert rc != 0


def test_manifest_skeleton_lists_all_eight_slots(tmp_path):
    runs_root = tmp_path / "runs"
    make_run.main(["--run", "m1", "--runs-root", str(runs_root)])
    manifest = json.loads((runs_root / "m1" / "manifest.json").read_text(encoding="utf-8"))
    expected = {f"{arm}/task{n}" for arm in ("A", "B") for n in (1, 2, 3, 4)}
    assert set(manifest.keys()) == expected
    assert all(v == [] for v in manifest.values())
