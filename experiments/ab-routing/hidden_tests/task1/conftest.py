"""Task 1's checks are black-box: each test gets its own throwaway copy of the
--target clone (via work_dir) and drives it entirely through subprocesses, so
nothing here ever runs code from the solution in-process."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest


@pytest.fixture
def work_dir(tmp_path, request) -> Path:
    """A fresh, isolated copy of the --target clone's source (no shop.db, no
    caches) for this test alone to mutate and run subprocess pytest against."""
    target = Path(request.config.getoption("--target")).resolve()
    work = tmp_path / "work"
    shutil.copytree(target, work,
                     ignore=shutil.ignore_patterns(".git", "shop.db", "__pycache__", ".pytest_cache"))
    return work
