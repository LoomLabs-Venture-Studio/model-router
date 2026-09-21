"""Loads skills/complexity/scripts/route.py by file path so the test suite exercises
the real functions, not a copy of them. The skill ships as files (no package, no
install step), so it is loaded the same way here: importlib against the path, not
a normal import.

Tests live at the repo root on purpose (see PLAYBOOK.md, "skill publish-hardening"):
a packaged skill (dist/*.skill) must not carry its own test suite.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ROUTE_PY = REPO_ROOT / "skills" / "complexity" / "scripts" / "route.py"


def _load_route_module():
    spec = importlib.util.spec_from_file_location("complexity_route", ROUTE_PY)
    assert spec and spec.loader, f"could not build a module spec for {ROUTE_PY}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def route_mod():
    """The route.py module, loaded once per test session."""
    return _load_route_module()


@pytest.fixture(autouse=True)
def isolated_log(tmp_path, monkeypatch):
    """Every test gets its own decision log. Never touch the real
    ~/.claude/complexity-router/decisions.jsonl."""
    log_file = tmp_path / "decisions.jsonl"
    monkeypatch.setenv("COMPLEXITY_LOG", str(log_file))
    return log_file


@pytest.fixture
def run_cli(isolated_log):
    """Run route.py as a real subprocess (argparse, exit codes, stdout/stderr) against
    the same isolated log. Never passes --jev and never touches .env or the network."""

    def _run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(ROUTE_PY), *args],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(REPO_ROOT),
        )

    return _run
