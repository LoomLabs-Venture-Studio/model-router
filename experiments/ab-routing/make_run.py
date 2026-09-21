#!/usr/bin/env python3
"""make_run.py: create a fresh A/B routing experiment run.

Makes eight independent copies of the shopapi fixture (A/task1..4, B/task1..4),
each its own tiny git repo with one "baseline" commit, so a per-task diff against
that commit is trivial later. Runs live OUTSIDE this repo (default:
~/.cache/ab-routing/runs/<run-id>/, override with --runs-root) so agents working
in a copy cannot see the harness, the other copies, or the real fixture.

Only git-tracked files under evals/fixture/shopapi are copied -- no shop.db,
__pycache__, or .pytest_cache, and the tracked fixture itself is never touched.

Usage: make_run.py --run <id> [--runs-root <path>]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

FIXTURE_REL = "evals/fixture/shopapi"
ARMS = ("A", "B")
TASKS = (1, 2, 3, 4)
DEFAULT_RUNS_ROOT = Path("~/.cache/ab-routing/runs").expanduser()


def find_repo_root() -> Path:
    """The repo root, found relative to this file first (fast path), falling back
    to `git rev-parse` if the tree has moved (e.g. this file copied elsewhere)."""
    here = Path(__file__).resolve()
    candidate = here.parents[2]
    if (candidate / FIXTURE_REL).is_dir():
        return candidate
    out = subprocess.run(["git", "-C", str(here.parent), "rev-parse", "--show-toplevel"],
                          check=True, capture_output=True, text=True).stdout.strip()
    return Path(out)


def tracked_fixture_files(repo_root: Path) -> list[str]:
    """Paths (repo-relative, forward-slash) of git-tracked files under the fixture."""
    out = subprocess.run(["git", "-C", str(repo_root), "ls-files", FIXTURE_REL],
                          check=True, capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line.strip()]


def make_slot(repo_root: Path, tracked: list[str], slot_dir: Path) -> None:
    """Copy the tracked fixture files into slot_dir and turn it into a one-commit
    git repo. Raises if slot_dir already exists (callers create run_dir atomically
    first, so this should only ever run once per slot)."""
    prefix = FIXTURE_REL + "/"
    slot_dir.mkdir(parents=True, exist_ok=False)
    for rel in tracked:
        if not rel.startswith(prefix):
            continue  # defensive; `git ls-files <dir>` should only return paths under it
        dest = slot_dir / rel[len(prefix):]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((repo_root / rel).read_bytes())

    def run(*cmd: str) -> None:
        subprocess.run(cmd, cwd=str(slot_dir), check=True, capture_output=True, text=True)

    run("git", "init", "-q")
    # Local to this throwaway repo only -- never touches the user's global git config.
    run("git", "config", "user.name", "ab-harness")
    run("git", "config", "user.email", "ab-harness@localhost")
    run("git", "add", "-A")
    # -c commit.gpgsign=false: this is a disposable sandbox repo with no human present
    # to satisfy a signing prompt; it never touches the tracked project's own history.
    run("git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "baseline")


def write_manifest_skeleton(run_dir: Path) -> None:
    manifest = {f"{arm}/task{n}": [] for arm in ARMS for n in TASKS}
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run", required=True, help="run id, e.g. 2026-09-19-01")
    p.add_argument("--runs-root", help=f"default: {DEFAULT_RUNS_ROOT}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    runs_root = Path(args.runs_root).expanduser() if args.runs_root else DEFAULT_RUNS_ROOT
    run_dir = runs_root / args.run

    try:
        run_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        print(f"make_run: run {args.run!r} already exists at {run_dir}; refusing to overwrite", file=sys.stderr)
        return 1

    repo_root = find_repo_root()
    tracked = tracked_fixture_files(repo_root)
    if not tracked:
        print(f"make_run: no tracked files found under {FIXTURE_REL} in {repo_root}", file=sys.stderr)
        return 1

    for arm in ARMS:
        for n in TASKS:
            make_slot(repo_root, tracked, run_dir / arm / f"task{n}")

    write_manifest_skeleton(run_dir)
    print(f"make_run: created {run_dir} (8 copies, {len(tracked)} tracked files each)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
