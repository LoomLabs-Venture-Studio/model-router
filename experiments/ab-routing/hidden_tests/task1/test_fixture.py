"""Task 1: test database fixture. Black-box, subprocess-driven per the CTO spec --
these tests never import the solution's code in-process; they run the shared
interpreter's own `pytest` against a throwaway copy of the solution and inspect
its exit code / JUnit report / the literal bytes of a shop.db file.

Each test gets its own fresh copy via the work_dir fixture (see conftest.py),
so tests never interfere with each other regardless of run order.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _junit import case_by_name, read_junit_cases  # noqa: E402

SUBPROCESS_TIMEOUT = 60

EXTRA_INSERT_TEST = (
    "from app.core.db import execute, query\n"
    "def test_insert_then_see_user_1_in_same_test():\n"
    "    execute(\"insert into users(id, email, tier) values (1, 'iso@x', 'free')\", ())\n"
    "    rows = query(\"select id from users where id = 1\", ())\n"
    "    assert rows and rows[0][0] == 1\n"
)
EXTRA_MISSING_TEST = (
    "from app.core.users import get_user\n"
    "def test_user_1_not_visible_here():\n"
    "    assert get_user(1) is None\n"
)
EXTRA_ALL_TABLES_TEST = (
    "from app.core.db import query\n"
    "def test_can_query_every_migrated_table():\n"
    "    for table in ('users', 'products', 'cart', 'orders', 'payments'):\n"
    "        query(f'select * from {table}', ())\n"
)


def _run_pytest(cwd: Path, args: list[str], junit_path: Path | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "pytest", "-q", *args]
    if junit_path is not None:
        cmd.append(f"--junitxml={junit_path}")
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ----------------------------------------------------------------------------
# Rule 1: clean checkout, no shop.db -> `pytest` exits 0, both original tests pass
# ----------------------------------------------------------------------------
@pytest.mark.guard
def test_original_tests_are_still_collected_on_clean_checkout(work_dir):
    """A solution must not "fix" the suite by deleting or renaming the two
    original tests -- they must still exist and be collectible. (--collect-only
    reports nothing useful via --junitxml, so this reads stdout instead.)"""
    assert not (work_dir / "shop.db").exists()
    result = _run_pytest(work_dir, ["--collect-only"])
    assert "test_get_user_missing" in result.stdout
    assert "test_price_for_unknown_user" in result.stdout


@pytest.mark.target
def test_original_tests_pass_on_clean_checkout(work_dir, tmp_path):
    """Rule 1: on a clean checkout with no shop.db, pytest exits 0 and the two
    existing tests pass (currently they error: 'no such table: users')."""
    assert not (work_dir / "shop.db").exists()
    junit = tmp_path / "result.xml"
    result = _run_pytest(work_dir, [], junit_path=junit)
    cases = read_junit_cases(junit)
    for name in ("test_get_user_missing", "test_price_for_unknown_user"):
        case = case_by_name(cases, name)
        assert case is not None, f"{name} did not run"
        assert not case["failed"], f"{name} failed or errored"
    assert result.returncode == 0, result.stdout + result.stderr


# ----------------------------------------------------------------------------
# Rule 2: tests get a database built from the migration, in a temp location
# ----------------------------------------------------------------------------
@pytest.mark.target
def test_extra_test_can_query_every_migrated_table_on_clean_checkout(work_dir, tmp_path):
    """Rule 2: a dropped-in test can query every table from the migration
    without error, with no shop.db present beforehand."""
    _write(work_dir / "tests" / "test_extra_all_tables.py", EXTRA_ALL_TABLES_TEST)
    junit = tmp_path / "result.xml"
    result = _run_pytest(work_dir, ["tests/test_extra_all_tables.py"], junit_path=junit)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.target
def test_isolated_test_gets_a_working_database_without_requesting_a_fixture(work_dir, tmp_path):
    """Rule 3 ("no test should have to request a fixture by name"): a single,
    bare test with zero fixture arguments still gets a working migrated
    database automatically."""
    _write(work_dir / "tests" / "test_extra_missing.py", EXTRA_MISSING_TEST)
    junit = tmp_path / "result.xml"
    result = _run_pytest(work_dir, ["tests/test_extra_missing.py"], junit_path=junit)
    assert result.returncode == 0, result.stdout + result.stderr


# ----------------------------------------------------------------------------
# Rule 3: isolation is automatic and order-independent
# ----------------------------------------------------------------------------
@pytest.mark.target
def test_isolation_holds_when_insert_test_runs_before_missing_test(work_dir, tmp_path):
    _write(work_dir / "tests" / "test_extra_insert.py", EXTRA_INSERT_TEST)
    _write(work_dir / "tests" / "test_extra_missing.py", EXTRA_MISSING_TEST)
    junit = tmp_path / "result.xml"
    result = _run_pytest(work_dir, ["tests/test_extra_insert.py", "tests/test_extra_missing.py"],
                          junit_path=junit)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.target
def test_isolation_holds_when_missing_test_runs_before_insert_test(work_dir, tmp_path):
    _write(work_dir / "tests" / "test_extra_insert.py", EXTRA_INSERT_TEST)
    _write(work_dir / "tests" / "test_extra_missing.py", EXTRA_MISSING_TEST)
    junit = tmp_path / "result.xml"
    result = _run_pytest(work_dir, ["tests/test_extra_missing.py", "tests/test_extra_insert.py"],
                          junit_path=junit)
    assert result.returncode == 0, result.stdout + result.stderr


def _assert_all_present_and_passing(cases: list[dict], names: tuple[str, ...]) -> None:
    """The brief never forbids a solution adding its own tests, and a hidden
    test may check only what the brief states -- so this checks that nothing
    failed and that each EXPECTED name is present and passed, not an exact
    test count (a solution-authored extra test file is allowed and is already
    covered by "nothing failed")."""
    failed_names = [c["name"] for c in cases if c["failed"]]
    assert not failed_names, f"test(s) failed or errored: {failed_names}"
    for name in names:
        case = case_by_name(cases, name)
        assert case is not None, f"{name} did not run"
        assert not case["failed"], f"{name} failed or errored"


@pytest.mark.target
def test_full_suite_passes_with_extra_tests_appended_after_originals(work_dir, tmp_path):
    """The original tests and freshly dropped-in tests must all pass together
    in one session, in this file order. A solution may add its own tests too;
    only the expected tests, and nothing failing, are checked."""
    _write(work_dir / "tests" / "test_extra_insert.py", EXTRA_INSERT_TEST)
    _write(work_dir / "tests" / "test_extra_missing.py", EXTRA_MISSING_TEST)
    junit = tmp_path / "result.xml"
    result = _run_pytest(work_dir, [], junit_path=junit)
    cases = read_junit_cases(junit)
    _assert_all_present_and_passing(cases, (
        "test_get_user_missing", "test_price_for_unknown_user",
        "test_insert_then_see_user_1_in_same_test", "test_user_1_not_visible_here",
    ))
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.target
def test_full_suite_passes_with_extra_tests_inserted_before_originals(work_dir, tmp_path):
    """Same as above, but the extra tests are collected first (tests/ sorts
    test_extra_* before test_pricing.py/test_users.py alphabetically anyway;
    this pins that assumption explicitly rather than relying on it)."""
    _write(work_dir / "tests" / "test_aaa_extra_insert.py", EXTRA_INSERT_TEST.replace(
        "test_insert_then_see_user_1_in_same_test", "test_aaa_insert_then_see_user_1"))
    _write(work_dir / "tests" / "test_aaa_extra_missing.py", EXTRA_MISSING_TEST.replace(
        "test_user_1_not_visible_here", "test_aaa_user_1_not_visible"))
    junit = tmp_path / "result.xml"
    result = _run_pytest(work_dir, [], junit_path=junit)
    cases = read_junit_cases(junit)
    _assert_all_present_and_passing(cases, (
        "test_get_user_missing", "test_price_for_unknown_user",
        "test_aaa_insert_then_see_user_1", "test_aaa_user_1_not_visible",
    ))
    assert result.returncode == 0, result.stdout + result.stderr


# ----------------------------------------------------------------------------
# Rule 4: an existing shop.db in the project directory is left alone
# ----------------------------------------------------------------------------
@pytest.mark.target
def test_running_tests_with_writes_does_not_modify_a_preexisting_shop_db(work_dir, tmp_path):
    """Rule 4: a shop.db that already exists in the project directory must not
    be modified by running the suite -- even when a dropped-in test performs a
    write. (The two original tests are read-only, so this only bites a
    solution whose isolation mechanism reuses the real file instead of a
    temporary one.)"""
    db_path = work_dir / "shop.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript((work_dir / "migrations" / "001_init.sql").read_text(encoding="utf-8"))
    conn.execute("insert into products(sku, price) values ('sentinel', 1.23)")
    conn.commit()
    conn.close()
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()

    _write(work_dir / "tests" / "test_extra_write.py",
           "from app.core.db import execute\n"
           "def test_writes_a_row():\n"
           "    execute(\"insert into users(id, email, tier) values (1, 'w@x', 'free')\", ())\n")
    junit = tmp_path / "result.xml"
    _run_pytest(work_dir, ["tests/test_extra_write.py"], junit_path=junit)

    after = hashlib.sha256(db_path.read_bytes()).hexdigest()
    assert before == after, "a pre-existing shop.db must not be modified by running the tests"


# ----------------------------------------------------------------------------
# Rule 5: outside the tests, the app still defaults to shop.db in the cwd
# ----------------------------------------------------------------------------
@pytest.mark.guard
def test_default_db_location_unchanged_outside_pytest(work_dir, tmp_path):
    """A solution's test-isolation mechanism must not leak into normal (non-
    test) execution: run the app's own code in a plain subprocess, never under
    pytest, with a hand-built shop.db in that process's cwd, and confirm it is
    the one used."""
    run_cwd = tmp_path / "outside"
    run_cwd.mkdir()
    db = run_cwd / "shop.db"
    conn = sqlite3.connect(str(db))
    conn.executescript((work_dir / "migrations" / "001_init.sql").read_text(encoding="utf-8"))
    conn.execute("insert into users(id, email, tier) values (7, 'z@x', 'free')")
    conn.commit()
    conn.close()

    env = dict(os.environ)
    env["PYTHONPATH"] = str(work_dir)
    result = subprocess.run(
        [sys.executable, "-c", "from app.core.users import get_user; print(get_user(7))"],
        cwd=str(run_cwd), env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "7" in result.stdout and "z@x" in result.stdout
