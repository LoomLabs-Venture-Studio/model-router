"""See hidden_tests/task2/conftest.py for why this must be pytest_configure,
not a fixture, and why reset uses a separate connection."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _shopdb import build_shop_db, reset_seed_data  # noqa: E402

import pytest  # noqa: E402


def pytest_configure(config):
    target = Path(config.getoption("--target")).resolve()
    build_shop_db(target)
    os.chdir(target)
    sys.path.insert(0, str(target))


@pytest.fixture(autouse=True)
def _reset_db():
    reset_seed_data(Path("shop.db"))
    yield
