#!/usr/bin/env python3
"""import_baseline.py: import one arm's task copies from a previous run into a
new run, byte for byte -- so the new run's scorer computes against the exact
same baseline solutions (same transcripts, same cost) instead of a second live
run of that arm.

Refuses unless every target slot already exists and is pristine (a git repo,
clean working tree including ignored files, exactly one commit -- i.e.
untouched since make_run.py created it). Replaces each target slot's entire
working tree, including .git, with a byte-for-byte copy of the source slot
(so HEAD and diffs are identical -- nothing is excluded, unlike make_run.py's
tracked-files-only copy: an imported baseline is the EXACT prior artefact, not
a fresh checkout). Verified with a recursive content hash of both trees, and
the source run is hashed again afterward to prove it was never touched.

Copies only the source manifest's entries for that arm (into the same slot
keys in the target manifest), each with "imported_from": "<source-run>" added.
Nothing else in either manifest is read or written.

Usage: import_baseline.py --run <target> --from-run <source> --arm B|A [--runs-root <path>]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_RUNS_ROOT = Path("~/.cache/ab-routing/runs").expanduser()
TASKS = (1, 2, 3, 4)


def is_pristine_baseline(task_dir: Path) -> tuple[bool, str]:
    """True if task_dir is a git repo with a fully clean tree (no modified,
    untracked, or ignored files) and exactly one commit -- i.e. still exactly
    what make_run.py produced."""
    if not (task_dir / ".git").is_dir():
        return False, "not a git repository"
    status = subprocess.run(["git", "-C", str(task_dir), "status", "--porcelain",
                              "--untracked-files=all", "--ignored"],
                             capture_output=True, text=True)
    if status.returncode != 0:
        return False, (status.stderr or "git status failed").strip()
    if status.stdout.strip():
        return False, f"not pristine: {status.stdout.strip()!r}"
    log = subprocess.run(["git", "-C", str(task_dir), "log", "--format=%H"],
                          capture_output=True, text=True)
    if log.returncode != 0:
        return False, (log.stderr or "git log failed").strip()
    commits = [l for l in log.stdout.splitlines() if l.strip()]
    if len(commits) != 1:
        return False, f"expected exactly one commit, found {len(commits)}"
    return True, ""


def tree_hash(root: Path) -> str:
    """A recursive content hash of every file under root (relative path plus
    bytes), independent of filesystem iteration order -- used to verify a
    byte-for-byte copy and that the source tree is unchanged afterward."""
    h = hashlib.sha256()
    for path in sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.relative_to(root).as_posix()):
        rel = path.relative_to(root).as_posix()
        h.update(rel.encode("utf-8") + b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def copy_tree_exact(src: Path, dest: Path) -> None:
    """A byte-for-byte replacement of dest's entire contents with src's --
    including .git, so HEAD and diffs are identical. Nothing is excluded."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, symlinks=True)


def invalid_run_id_reason(run_id: str) -> str | None:
    """None if run_id is safe to use as a single path component under
    runs_root; otherwise the reason it is refused. Rejects anything that
    could make `runs_root / run_id` name a directory other than a direct
    child of runs_root (e.g. "..", a path with a separator)."""
    if run_id in ("", ".", ".."):
        return "must not be empty, '.', or '..'"
    if "/" in run_id or "\\" in run_id:
        return "must not contain a path separator"
    if Path(run_id).name != run_id:
        return "must be a single path component"
    return None


def path_is_inside(path: Path, root: Path) -> bool:
    """True if path is root itself or somewhere underneath it. Both must
    already be resolved (Path.resolve())."""
    return path == root or root in path.parents


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run", required=True, help="target run id")
    p.add_argument("--from-run", required=True, help="source run id (same --runs-root)")
    p.add_argument("--arm", required=True, choices=("A", "B"))
    p.add_argument("--runs-root", help=f"default: {DEFAULT_RUNS_ROOT}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    for flag, run_id in (("--run", args.run), ("--from-run", args.from_run)):
        reason = invalid_run_id_reason(run_id)
        if reason is not None:
            print(f"import_baseline: {flag} {run_id!r} is invalid: {reason}", file=sys.stderr)
            return 1

    runs_root = Path(args.runs_root).expanduser() if args.runs_root else DEFAULT_RUNS_ROOT
    target_run_dir = runs_root / args.run
    source_run_dir = runs_root / args.from_run

    # Defence in depth beyond the single-component check above: resolve
    # symlinks and ".." before trusting either run dir, since the check above
    # only constrains the literal --run/--from-run strings, not what they
    # (or runs_root) might resolve to on disk.
    resolved_runs_root = runs_root.resolve()
    resolved_target_run_dir = target_run_dir.resolve()
    resolved_source_run_dir = source_run_dir.resolve()

    if not path_is_inside(resolved_target_run_dir, resolved_runs_root):
        print(f"import_baseline: target run {args.run!r} resolves outside the runs root "
              f"({resolved_target_run_dir})", file=sys.stderr)
        return 1
    if not path_is_inside(resolved_source_run_dir, resolved_runs_root):
        print(f"import_baseline: source run {args.from_run!r} resolves outside the runs root "
              f"({resolved_source_run_dir})", file=sys.stderr)
        return 1
    if resolved_source_run_dir == resolved_target_run_dir:
        print("import_baseline: --run and --from-run resolve to the same directory", file=sys.stderr)
        return 1
    if path_is_inside(resolved_target_run_dir, resolved_source_run_dir) or \
            path_is_inside(resolved_source_run_dir, resolved_target_run_dir):
        print("import_baseline: --run and --from-run must not be nested inside each other", file=sys.stderr)
        return 1

    if not target_run_dir.is_dir():
        print(f"import_baseline: target run {args.run!r} not found at {target_run_dir}", file=sys.stderr)
        return 1
    if not source_run_dir.is_dir():
        print(f"import_baseline: source run {args.from_run!r} not found at {source_run_dir}", file=sys.stderr)
        return 1
    # A run directory must be a real directory, not a symlink to one --
    # otherwise every check below that trusts "inside the run dir" or "this
    # slot vs that slot" can be aimed at a different directory than the one
    # it appears to be.
    if target_run_dir.is_symlink():
        print(f"import_baseline: refusing -- target run directory is a symlink: {target_run_dir}", file=sys.stderr)
        return 1
    if source_run_dir.is_symlink():
        print(f"import_baseline: refusing -- source run directory is a symlink: {source_run_dir}", file=sys.stderr)
        return 1
    # Path.resolve() does not normalise case, so on a case-insensitive
    # filesystem "r" and "R" resolve to two different-looking strings that
    # are nonetheless the same directory; os.path.samefile asks the
    # filesystem itself (same device + inode), so it catches that and any
    # other alias the string-based checks above miss.
    if os.path.samefile(source_run_dir, target_run_dir):
        print(f"import_baseline: --run {args.run!r} and --from-run {args.from_run!r} "
              f"are the same directory on disk", file=sys.stderr)
        return 1
    # Same reasoning as the run directory above, one level down: the arm
    # directory must be real too, or every slot beneath it is reached
    # through an alias none of the slot-level checks below can see coming.
    if (source_run_dir / args.arm).is_symlink():
        print(f"import_baseline: refusing -- source arm directory is a symlink: "
              f"{source_run_dir / args.arm}", file=sys.stderr)
        return 1
    if (target_run_dir / args.arm).is_symlink():
        print(f"import_baseline: refusing -- target arm directory is a symlink: "
              f"{target_run_dir / args.arm}", file=sys.stderr)
        return 1

    source_dirs = {n: source_run_dir / args.arm / f"task{n}" for n in TASKS}
    target_dirs = {n: target_run_dir / args.arm / f"task{n}" for n in TASKS}

    for n in TASKS:
        if not source_dirs[n].is_dir():
            print(f"import_baseline: source slot not found: {source_dirs[n]}", file=sys.stderr)
            return 1
        if not target_dirs[n].is_dir():
            print(f"import_baseline: target slot not found: {target_dirs[n]}", file=sys.stderr)
            return 1
        # A task slot must be a real directory, not a symlink -- e.g. to a
        # directory outside the runs root entirely (a fake baseline import),
        # or to a different slot (task 3 pointing at task 2's pristine repo,
        # which would otherwise look like a legitimate copy source/target to
        # every check below). This must run for every slot before ANY slot
        # is hashed or copied, so a bad task 3 can't be found after tasks 1
        # and 2 were already replaced.
        if source_dirs[n].is_symlink():
            print(f"import_baseline: refusing -- source slot is a symlink: {source_dirs[n]}", file=sys.stderr)
            return 1
        if target_dirs[n].is_symlink():
            print(f"import_baseline: refusing -- target slot is a symlink: {target_dirs[n]}", file=sys.stderr)
            return 1
        # Defence in depth: even without a symlink at any single level, a
        # slot must resolve to somewhere inside its own run directory.
        if not path_is_inside(source_dirs[n].resolve(), resolved_source_run_dir):
            print(f"import_baseline: refusing -- source slot resolves outside its run directory: "
                  f"{source_dirs[n]}", file=sys.stderr)
            return 1
        if not path_is_inside(target_dirs[n].resolve(), resolved_target_run_dir):
            print(f"import_baseline: refusing -- target slot resolves outside its run directory: "
                  f"{target_dirs[n]}", file=sys.stderr)
            return 1
        # Same identity check as above, but at slot granularity: catches a
        # slot-level alias (e.g. a symlinked arm directory) that the run-level
        # check above cannot see, since it only compares the two run dirs.
        if os.path.samefile(source_dirs[n], target_dirs[n]):
            print(f"import_baseline: refusing -- source and target slot are the same directory "
                  f"on disk: {source_dirs[n]}", file=sys.stderr)
            return 1
        ok, reason = is_pristine_baseline(target_dirs[n])
        if not ok:
            print(f"import_baseline: refusing -- target {target_dirs[n]} is not pristine ({reason})",
                  file=sys.stderr)
            return 1

    source_hashes_before = {n: tree_hash(source_dirs[n]) for n in TASKS}

    for n in TASKS:
        copy_tree_exact(source_dirs[n], target_dirs[n])
        dest_hash = tree_hash(target_dirs[n])
        if dest_hash != source_hashes_before[n]:
            print(f"import_baseline: copy verification failed for task {n}: "
                  f"source hash {source_hashes_before[n]} != target hash {dest_hash}", file=sys.stderr)
            return 1
        print(f"import_baseline: task {n}: imported {source_dirs[n]} -> {target_dirs[n]} "
              f"(hash {dest_hash[:12]})")

    source_hashes_after = {n: tree_hash(source_dirs[n]) for n in TASKS}
    if source_hashes_after != source_hashes_before:
        changed = [n for n in TASKS if source_hashes_after[n] != source_hashes_before[n]]
        print(f"import_baseline: the source run was modified by this operation for task(s) "
              f"{changed} (this must never happen)", file=sys.stderr)
        return 1

    source_manifest_path = source_run_dir / "manifest.json"
    target_manifest_path = target_run_dir / "manifest.json"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8")) if source_manifest_path.exists() else {}
    target_manifest = json.loads(target_manifest_path.read_text(encoding="utf-8")) if target_manifest_path.exists() else {}

    for n in TASKS:
        slot_key = f"{args.arm}/task{n}"
        target_manifest[slot_key] = [
            {**entry, "imported_from": args.from_run} for entry in source_manifest.get(slot_key, [])
        ]

    target_manifest_path.write_text(json.dumps(target_manifest, indent=2) + "\n", encoding="utf-8")
    print(f"import_baseline: wrote {target_manifest_path} (arm {args.arm} imported from {args.from_run!r})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
