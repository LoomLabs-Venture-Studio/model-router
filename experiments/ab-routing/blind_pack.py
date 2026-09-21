#!/usr/bin/env python3
"""blind_pack.py: package task solutions as anonymous X/Y diffs for blind review.

For each task (1-4) and each arm (A/B), produces the full diff of the copy
against its baseline commit, INCLUDING uncommitted changes and new untracked
files, EXCLUDING shop.db, *.db, __pycache__/, .pytest_cache/, *.pyc. Never
modifies the task copy itself.

Randomly assigns the two arms to labels X and Y independently per task, using
random.Random(seed); default seed is drawn from os.urandom and recorded in
<run>/blind_key.json (mapping each label to its arm). Refuses to overwrite an
existing blind_key.json (a re-pack would change the labels after someone may
have seen them).

The reviewer's materials -- the 8 diffs, ticket1..4.md (rendered from
briefs/task<n>.md with the literal {PROJECT_DIR} placeholder replaced by the
neutral fake path /srv/shopapi), and REVIEW_PROMPT.md -- are written to
--review-dir, which defaults to a directory OUTSIDE the run directory
(<runs-root>/../review/<run-id>). This is deliberate: the run directory holds
blind_key.json and the un-anonymised A/ and B/ copies, so handing a reviewer
any path inside it (even a subdirectory) risks them wandering up and
de-anonymising themselves. Every file written to --review-dir is checked for
leaks: no absolute run path, no "/A/" or "/B/" path segment, and no mention of
"ab-routing", "the experiment", or "arm(s)" (case-insensitive, word-bounded
for "arm" so "farm"/"charm" don't false-positive).

Usage: blind_pack.py --run <id> [--runs-root <path>] [--review-dir <path>] [--seed N]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path

HERE = Path(__file__).resolve().parent
BRIEFS_DIR = HERE / "briefs"
DEFAULT_RUNS_ROOT = Path("~/.cache/ab-routing/runs").expanduser()
TASKS = (1, 2, 3, 4)
ARMS = ("A", "B")
EXCLUDE_PATTERNS = ("shop.db", "*.db", "__pycache__", ".pytest_cache", "*.pyc")
FAKE_PROJECT_DIR = "/srv/shopapi"

# CTO-written, copied verbatim from ~/.cache/shopapi-dev/review/r1/REVIEW_PROMPT.md
# (2026-09-19). Do not add anything that names arms, models, routing, cost, or the
# experiment -- doing so would itself tell the reviewer those things exist.
REVIEW_PROMPT_TEMPLATE = """# Code review: two candidate patches per ticket

This folder holds four tickets for a small FastAPI service (`ticket1.md` .. `ticket4.md`)
and, for each ticket, two independent candidate patches against the same starting code:
`task<n>_X.diff` and `task<n>_Y.diff`. X and Y labels are arbitrary and are not consistent
across tickets: whoever wrote task1_X did not necessarily write task2_X.

You are read-only. Review only what is in this folder. Do not look elsewhere on the
filesystem, do not try to run anything, and do not speculate about who or what wrote a
patch. Judge each patch on its own against the ticket text.

For each ticket, read the ticket, then both diffs, and assess each patch on:

- **Correctness (1-5):** does it implement every rule in the ticket, including the stated
  order of checks? Cite the rule number for anything missed or wrong.
- **Scope discipline (1-5):** did it change only what the ticket asked? Note anything
  touched that the ticket says to leave alone, and anything added beyond the ticket.
- **Code quality (1-5):** clarity, structure, duplication, robustness of the approach.

Return exactly this, and nothing else:

1. One table with a row per ticket and these columns:
   `ticket | better (X / Y / tie) | X correctness | X scope | X quality | Y correctness | Y scope | Y quality`
2. Under the table, for each ticket, two lines: `task<n> X: <single most important defect, or "none found">`
   and the same for Y. Give a file and line from the diff where you can.
3. One sentence per ticket explaining the `better` verdict.

Keep the whole answer under 60 lines.
"""

LEAK_PATTERNS = [
    (re.compile(r"/A/task\d"), "'/A/task<n>' path segment"),
    (re.compile(r"/B/task\d"), "'/B/task<n>' path segment"),
    (re.compile(r"ab-routing", re.IGNORECASE), "'ab-routing'"),
    (re.compile(r"experiment", re.IGNORECASE), "'experiment'"),
    (re.compile(r"\barms?\b", re.IGNORECASE), "'arm'/'arms'"),
]


def default_review_dir(runs_root: Path, run_id: str) -> Path:
    return runs_root.parent / "review" / run_id


def find_baseline_commit(task_dir: Path) -> str:
    """Find the root commit (first commit) of the repository."""
    out = subprocess.run(["git", "-C", str(task_dir), "rev-list", "--max-parents=0", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.strip()
    return out


def get_diff_against_baseline(task_dir: Path, baseline: str, exclude_patterns: tuple[str, ...]) -> str:
    """Full diff of the working tree against the baseline commit: tracked changes
    plus untracked files, built read-only (nothing here modifies task_dir)."""
    diff_out = subprocess.run(["git", "-C", str(task_dir), "diff", baseline],
                               capture_output=True, text=True)
    diff_text = diff_out.stdout

    status_out = subprocess.run(["git", "-C", str(task_dir), "ls-files", "--others", "--exclude-standard"],
                                 check=True, capture_output=True, text=True)
    untracked = [line.strip() for line in status_out.stdout.splitlines() if line.strip()]

    def should_exclude(path: str) -> bool:
        for pattern in exclude_patterns:
            if "*" in pattern:
                if fnmatch(path, pattern):
                    return True
            elif path == pattern or path.endswith(f"/{pattern}"):
                return True
        return False

    for untracked_file in (f for f in untracked if not should_exclude(f)):
        full_path = task_dir / untracked_file
        if not full_path.is_file():
            continue
        try:
            content = full_path.read_bytes()
        except OSError:
            continue
        diff_text += f"diff --git a/{untracked_file} b/{untracked_file}\n"
        diff_text += "new file mode 100644\n"
        diff_text += f"index 0000000..{hash(content) & 0xfffffff:07x}\n"
        diff_text += "--- /dev/null\n"
        diff_text += f"+++ b/{untracked_file}\n"
        try:
            text_content = content.decode("utf-8")
            for line in text_content.splitlines(keepends=True):
                diff_text += f"+{line}"
        except UnicodeDecodeError:
            diff_text += "+[binary content]\n"

    return diff_text


def check_no_leaks(text: str, run_dir: Path, label: str) -> None:
    """Raises ValueError naming the leak and the file if text reveals the run
    directory, an arm-labelled path segment, or the words this folder must
    never contain (see module docstring)."""
    if str(run_dir) in text:
        raise ValueError(f"{label}: contains the run directory's absolute path")
    for pattern, description in LEAK_PATTERNS:
        if pattern.search(text):
            raise ValueError(f"{label}: contains {description}")


def render_ticket(task_n: int) -> str:
    brief_path = BRIEFS_DIR / f"task{task_n}.md"
    return brief_path.read_text(encoding="utf-8").replace("{PROJECT_DIR}", FAKE_PROJECT_DIR)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", required=True)
    parser.add_argument("--runs-root", help=f"default: {DEFAULT_RUNS_ROOT}")
    parser.add_argument("--review-dir", help="default: <runs-root>/../review/<run-id>")
    parser.add_argument("--seed", type=int, help="random seed; default: os.urandom")
    args = parser.parse_args(argv)

    runs_root = Path(args.runs_root).expanduser() if args.runs_root else DEFAULT_RUNS_ROOT
    run_dir = runs_root / args.run

    if not run_dir.is_dir():
        print(f"blind_pack: run {args.run!r} not found at {run_dir}", file=sys.stderr)
        return 1

    blind_key_path = run_dir / "blind_key.json"
    if blind_key_path.exists():
        print(f"blind_pack: blind_key.json already exists at {blind_key_path}; refusing to overwrite",
              file=sys.stderr)
        return 1

    review_dir = Path(args.review_dir).expanduser() if args.review_dir else default_review_dir(runs_root, args.run)
    review_dir.mkdir(parents=True, exist_ok=True)

    if args.seed is not None:
        seed = args.seed
    else:
        seed = int.from_bytes(os.urandom(4), "big")
    rng = random.Random(seed)

    mapping = {"seed": seed, "tasks": {}}
    pending_files: dict[Path, str] = {}

    for task_n in TASKS:
        arms_shuffled = list(ARMS)
        rng.shuffle(arms_shuffled)
        x_arm, y_arm = arms_shuffled
        mapping["tasks"][str(task_n)] = {"X": x_arm, "Y": y_arm}

        for label, arm in (("X", x_arm), ("Y", y_arm)):
            task_dir = run_dir / arm / f"task{task_n}"
            if not task_dir.is_dir():
                print(f"blind_pack: task directory not found: {task_dir}", file=sys.stderr)
                return 1
            try:
                baseline = find_baseline_commit(task_dir)
                diff_text = get_diff_against_baseline(task_dir, baseline, EXCLUDE_PATTERNS)
            except (OSError, subprocess.CalledProcessError) as e:
                print(f"blind_pack: failed to generate diff for {arm}/task{task_n}: {e}", file=sys.stderr)
                return 1
            pending_files[review_dir / f"task{task_n}_{label}.diff"] = diff_text

        try:
            pending_files[review_dir / f"ticket{task_n}.md"] = render_ticket(task_n)
        except OSError as e:
            print(f"blind_pack: failed to render ticket{task_n}.md: {e}", file=sys.stderr)
            return 1

    pending_files[review_dir / "REVIEW_PROMPT.md"] = REVIEW_PROMPT_TEMPLATE

    # Validate every file before writing any of them, so a leak never partially lands.
    for path, text in pending_files.items():
        try:
            check_no_leaks(text, run_dir, path.name)
        except ValueError as e:
            print(f"blind_pack: refusing to write review materials: {e}", file=sys.stderr)
            return 1

    for path, text in pending_files.items():
        path.write_text(text, encoding="utf-8")

    blind_key_path.write_text(json.dumps(mapping, indent=2) + "\n", encoding="utf-8")

    print(f"blind_pack: wrote {review_dir} ({len(TASKS) * 2} diffs, {len(TASKS)} tickets, "
          f"REVIEW_PROMPT.md), mapping to {blind_key_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
