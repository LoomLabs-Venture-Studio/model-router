"""Task 2 (and 3, 4) drive the app through TestClient against a shared shop.db
built once per session. app.core.db connects to ./shop.db at IMPORT time, so
the db must exist, and this process must already be chdir'd into the target
with it on sys.path, BEFORE any test module (which imports the app) is
collected -- pytest_configure runs before collection, so it happens here, not
in a fixture.

Per-test isolation resets rows through a SEPARATE sqlite3 connection (see
_shopdb.reset_seed_data), never through app.core.db's own connection or
private names, so a solution that refactors internals within the contract
isn't penalised.
"""
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
