#!/usr/bin/env python3
"""make_env.py: build the one shared interpreter used by both arms' agents and
by score.py, so solutions are graded in the environment they were written in.

Lives outside this repo by default (~/.cache/shopapi-dev/venv, override with
--venv) so its path never reveals the harness or the repo -- briefs name this
interpreter directly. Idempotent: reruns reuse the existing venv and just
reinstall from requirements.txt (pip itself is idempotent for pinned installs).

Usage: make_env.py [--venv <path>] [--requirements <path>]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

DEFAULT_VENV = Path("~/.cache/shopapi-dev/venv").expanduser()


def venv_python(venv_dir: Path) -> Path:
    # POSIX layout only (this machine is darwin); Scripts/ (Windows) is not needed here.
    return venv_dir / "bin" / "python"


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--venv", help=f"default: {DEFAULT_VENV}")
    p.add_argument("--requirements", help="default: requirements.txt next to this script")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    venv_dir = Path(args.venv).expanduser() if args.venv else DEFAULT_VENV
    requirements = (Path(args.requirements) if args.requirements
                    else Path(__file__).resolve().parent / "requirements.txt")

    if not requirements.exists():
        print(f"make_env: requirements file not found: {requirements}", file=sys.stderr)
        return 1

    py = venv_python(venv_dir)
    if not py.exists():
        venv_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)

    subprocess.run([str(py), "-m", "pip", "install", "--upgrade", "pip"], check=True)
    subprocess.run([str(py), "-m", "pip", "install", "-r", str(requirements)], check=True)

    lock_path = Path(__file__).resolve().parent / "requirements.lock"
    freeze = subprocess.run([str(py), "-m", "pip", "freeze"], check=True,
                             capture_output=True, text=True).stdout
    lock_path.write_text(freeze, encoding="utf-8")

    print(str(py))
    return 0


if __name__ == "__main__":
    sys.exit(main())
