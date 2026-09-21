"""C3 (#17, #18, #22): log robustness.

Before this sprint's fix:
  (a) COMPLEXITY_LOG=decisions.jsonl (a bare filename, no directory component)
      crashed append_log's `os.makedirs(os.path.dirname(path), exist_ok=True)`
      with FileNotFoundError, since os.path.dirname("decisions.jsonl") == "" and
      os.makedirs("") always raises. Reproduced in
      test_basename_complexity_log_does_not_crash.
  (b) An interrupted write leaving the log without a trailing newline meant the
      next append glued its record onto the broken one, corrupting both for every
      reader. Reproduced in test_append_after_a_truncated_final_line_isolates_it.
  (c) read_log caught json.JSONDecodeError but not "valid JSON, wrong shape" --
      `null`, `[]`, or a bare number/string all parse fine and used to be handed
      to callers as-is, which crashed the first `.get()` call downstream (see
      cmd_stats's original AttributeError). Reproduced in
      test_read_log_skips_non_object_lines.
  (d) show/outcome/stats --last loaded the entire file even when they only needed
      recent history. read_log_tail bounds that. Reproduced in
      test_read_log_tail_does_not_read_bytes_before_the_window and
      test_read_log_tail_drops_a_partial_first_line.

Every test here talks to route.py's own functions directly (via the route_mod
fixture) or drives the CLI as a subprocess with COMPLEXITY_LOG pointed at a
tmp_path file -- never the real ~/.claude/complexity-router/decisions.jsonl.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ROUTE_PY = REPO_ROOT / "skills" / "complexity" / "scripts" / "route.py"


# ----------------------------------------------------------------------------
# (a) a basename COMPLEXITY_LOG (no directory component) must not crash
# ----------------------------------------------------------------------------
def test_basename_complexity_log_does_not_crash(route_mod, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("COMPLEXITY_LOG", "decisions.jsonl")
    path = route_mod.append_log({"event": "decision", "id": "c-abc123", "ts": "2026-01-01T00:00:00+00:00"})
    assert path == "decisions.jsonl"
    assert (tmp_path / "decisions.jsonl").exists()


def test_basename_complexity_log_via_real_cli_does_not_crash(tmp_path):
    """Drives the real CLI as a subprocess, cwd pinned to tmp_path -- never
    run_cli's REPO_ROOT, since a relative COMPLEXITY_LOG would otherwise create
    the log inside the repository working tree itself."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
           "HOME": str(tmp_path), "COMPLEXITY_LOG": "decisions.jsonl"}
    result = subprocess.run(
        [sys.executable, str(ROUTE_PY), "route", "--scores", "S1 R1 A1 K1 I1 P1 V1",
         "--kind", "implement", "--task", "t"],
        capture_output=True, text=True, timeout=10, cwd=str(tmp_path), env=env,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "decisions.jsonl").exists()


# ----------------------------------------------------------------------------
# (b) a missing trailing newline is fixed before the next append
# ----------------------------------------------------------------------------
def test_append_after_a_truncated_final_line_isolates_it(route_mod, isolated_log):
    isolated_log.write_bytes(b'{"event": "decision", "id": "c-partial", "ts": "x"')  # no closing brace, no newline
    route_mod.append_log({"event": "decision", "id": "c-full", "ts": "2026-01-01T00:00:00+00:00"})
    lines = isolated_log.read_bytes().split(b"\n")
    assert lines[0] == b'{"event": "decision", "id": "c-partial", "ts": "x"'
    # the second record parses cleanly on its own line -- not glued to the first
    assert json.loads(lines[1])["id"] == "c-full"


def test_ensure_trailing_newline_is_a_noop_when_already_present(route_mod, isolated_log):
    isolated_log.write_text('{"event": "decision", "id": "c-one", "ts": "x"}\n', encoding="utf-8")
    before = isolated_log.read_bytes()
    route_mod._ensure_trailing_newline(str(isolated_log))
    assert isolated_log.read_bytes() == before


def test_ensure_trailing_newline_on_missing_file_does_nothing(route_mod, tmp_path):
    missing = tmp_path / "nope.jsonl"
    route_mod._ensure_trailing_newline(str(missing))  # must not raise
    assert not missing.exists()


def test_append_writes_one_full_line_in_a_single_write_call(route_mod, isolated_log, monkeypatch):
    """Keep the actual record append as one write() of one whole line -- the
    newline-fixup pass is a separate, earlier step, not folded into this write."""
    calls = []
    real_open = open

    def spy_open(path, mode="r", *a, **kw):
        f = real_open(path, mode, *a, **kw)
        if "a" in mode:
            real_write = f.write

            def spy_write(data):
                calls.append(data)
                return real_write(data)
            f.write = spy_write
        return f

    monkeypatch.setattr(route_mod, "open", spy_open, raising=False)
    route_mod.append_log({"event": "decision", "id": "c-x", "ts": "t"})
    assert len(calls) == 1
    assert calls[0].endswith("\n")


# ----------------------------------------------------------------------------
# (c) readers skip lines that parse but aren't a JSON object
# ----------------------------------------------------------------------------
def test_read_log_skips_non_object_lines(route_mod, isolated_log):
    isolated_log.write_text(
        "null\n[]\n42\n\"just a string\"\ntrue\n"
        '{"event": "decision", "id": "c-real", "ts": "t"}\n'
        "not json at all\n",
        encoding="utf-8",
    )
    rows = route_mod.read_log()
    assert rows == [{"event": "decision", "id": "c-real", "ts": "t"}]


def test_read_log_tail_skips_non_object_lines(route_mod, isolated_log):
    isolated_log.write_text(
        'null\n{"event": "decision", "id": "c-real", "ts": "t"}\n[]\n', encoding="utf-8"
    )
    rows = route_mod.read_log_tail()
    assert rows == [{"event": "decision", "id": "c-real", "ts": "t"}]


def test_stats_on_a_log_of_only_garbage_lines_does_not_crash(run_cli, isolated_log):
    isolated_log.write_text("null\n[]\n42\n\"just a string\"\n", encoding="utf-8")
    result = run_cli("stats")
    assert result.returncode == 0, result.stderr
    assert "no decisions logged yet" in result.stdout


def test_show_on_a_log_of_only_garbage_lines_exits_cleanly(run_cli, isolated_log):
    isolated_log.write_text("null\n[]\n", encoding="utf-8")
    result = run_cli("show")
    assert result.returncode != 0
    assert "Traceback" not in result.stderr


# ----------------------------------------------------------------------------
# (d) read_log_tail is bounded, and drops a (likely partial) first line
# ----------------------------------------------------------------------------
def test_read_log_tail_does_not_read_bytes_before_the_window(route_mod, isolated_log):
    lines = [json.dumps({"event": "decision", "id": f"c-{i:04d}", "ts": "t"}) for i in range(500)]
    isolated_log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    full_size = isolated_log.stat().st_size

    tail = route_mod.read_log_tail(max_bytes=200)
    assert 0 < len(tail) < 500
    # every id present in the tail must be from near the END of the file
    ids = [r["id"] for r in tail]
    assert ids == sorted(ids)  # order preserved
    assert ids[-1] == "c-0499"
    assert full_size > 200 * 5  # sanity: the file is meaningfully bigger than the window


def test_read_log_tail_drops_a_partial_first_line(route_mod, tmp_path, monkeypatch):
    """Build a log where a small max_bytes window is known to start mid-line, and
    confirm the (partial, unparseable-if-kept) first line never appears in the
    result -- read_log_tail must not emit a record built from a truncated line."""
    monkeypatch.setenv("COMPLEXITY_LOG", str(tmp_path / "decisions.jsonl"))
    row_a = json.dumps({"event": "decision", "id": "c-aaaaaaaaaaaa", "ts": "t", "pad": "x" * 50})
    row_b = json.dumps({"event": "decision", "id": "c-bbbbbbbbbbbb", "ts": "t", "pad": "y" * 50})
    (tmp_path / "decisions.jsonl").write_text(row_a + "\n" + row_b + "\n", encoding="utf-8")

    # a window that starts partway through row_a's line
    window = len(row_b) + 20
    tail = route_mod.read_log_tail(max_bytes=window)
    ids = [r["id"] for r in tail]
    assert "c-bbbbbbbbbbbb" in ids
    assert "c-aaaaaaaaaaaa" not in ids  # its line was only partially inside the window


def test_read_log_tail_on_a_small_log_returns_everything(route_mod, isolated_log):
    isolated_log.write_text(
        '{"event": "decision", "id": "c-one", "ts": "t"}\n'
        '{"event": "decision", "id": "c-two", "ts": "t"}\n',
        encoding="utf-8",
    )
    tail = route_mod.read_log_tail()  # default 1 MiB window, file is tiny
    assert [r["id"] for r in tail] == ["c-one", "c-two"]


def test_read_log_tail_on_missing_file_returns_empty(route_mod, tmp_path, monkeypatch):
    monkeypatch.setenv("COMPLEXITY_LOG", str(tmp_path / "nope.jsonl"))
    assert route_mod.read_log_tail() == []


def test_stats_last_n_uses_the_bounded_tail(run_cli, isolated_log):
    """--last N must work end-to-end against a log too big to be read whole in
    the same call path a naive read_log() would take -- functionally verified via
    a moderate log plus read_log_tail's own unit coverage above for the byte
    bound itself."""
    rows = []
    for i in range(20):
        rows.append(json.dumps({
            "event": "decision", "id": f"c-{i:04d}", "ts": "t", "mode": "inline",
            "model": "haiku", "scores_used": {"K": 0}, "scores_given": {}, "unsure": [],
            "notes": [], "override": None,
        }))
    isolated_log.write_text("\n".join(rows) + "\n", encoding="utf-8")
    result = run_cli("stats", "--last", "5")
    assert result.returncode == 0, result.stderr
    assert "5 decisions" in result.stdout


# ----------------------------------------------------------------------------
# F2 (PLAYBOOK.md "skill publish-hardening", Task F): COMPLEXITY_LOG pointing at
# something that can't be appended to (a directory, an empty path, an unwritable
# directory, an unreadable-but-existing file) used to make `route`/`score --route`
# die with an uncaught OSError traceback BEFORE printing anything -- the decision
# was computed and then simply lost. Fixed: `_try_append_log` catches the write
# failure, warns once on stderr, and the normal stdout output (the one-liner, the
# card, or the --json document) still prints, exit 0. `show`/`stats`/`outcome`
# read the log to do their job at all, so an unreadable log there is a real
# failure for them: one-line error on stderr, non-zero exit, no traceback.
#
# Every test below drives the real CLI as a subprocess with an explicit,
# minimal environment and cwd=tmp_path (never REPO_ROOT), so a relative
# COMPLEXITY_LOG can't land inside the repository working tree.
# ----------------------------------------------------------------------------
def _run_isolated(tmp_path, *args, extra_env=None):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"), "HOME": str(tmp_path)}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(ROUTE_PY), *args],
        capture_output=True, text=True, timeout=10, cwd=str(tmp_path), env=env,
    )


def _running_as_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def test_route_with_log_path_a_directory_still_prints_decision_and_warns(tmp_path):
    log_dir = tmp_path / "decisions.jsonl"
    log_dir.mkdir()
    result = _run_isolated(tmp_path, "route", "--scores", "S1 R1 A1 K1 I1 P1 V1",
                            "--kind", "implement", "--task", "t",
                            extra_env={"COMPLEXITY_LOG": str(log_dir)})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().startswith("[c-"), result.stdout
    assert "Traceback" not in result.stderr
    assert "complexity: decision not logged" in result.stderr


def test_route_with_empty_complexity_log_still_prints_decision_and_warns(tmp_path):
    result = _run_isolated(tmp_path, "route", "--scores", "S1 R1 A1 K1 I1 P1 V1",
                            "--kind", "implement", "--task", "t",
                            extra_env={"COMPLEXITY_LOG": ""})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().startswith("[c-"), result.stdout
    assert "Traceback" not in result.stderr
    assert "complexity: decision not logged" in result.stderr


def test_route_with_unwritable_log_directory_still_prints_decision_and_warns(tmp_path):
    if _running_as_root():
        pytest.skip("root can write anywhere; this case can't be exercised as root")
    log_dir = tmp_path / "nowrite"
    log_dir.mkdir()
    log_dir.chmod(0o500)
    try:
        result = _run_isolated(tmp_path, "route", "--scores", "S1 R1 A1 K1 I1 P1 V1",
                                "--kind", "implement", "--task", "t",
                                extra_env={"COMPLEXITY_LOG": str(log_dir / "decisions.jsonl")})
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip().startswith("[c-"), result.stdout
        assert "Traceback" not in result.stderr
        assert "complexity: decision not logged" in result.stderr
    finally:
        log_dir.chmod(0o700)


def test_route_with_unreadable_log_file_still_prints_decision_and_warns(tmp_path):
    if _running_as_root():
        pytest.skip("root can read/write anything; this case can't be exercised as root")
    log_file = tmp_path / "decisions.jsonl"
    log_file.write_text("", encoding="utf-8")
    log_file.chmod(0o000)
    try:
        result = _run_isolated(tmp_path, "route", "--scores", "S1 R1 A1 K1 I1 P1 V1",
                                "--kind", "implement", "--task", "t",
                                extra_env={"COMPLEXITY_LOG": str(log_file)})
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip().startswith("[c-"), result.stdout
        assert "Traceback" not in result.stderr
        assert "complexity: decision not logged" in result.stderr
    finally:
        log_file.chmod(0o644)


def test_score_route_json_with_unwritable_log_still_prints_the_document(tmp_path):
    """The --json document (not just the plain one-liner) must also still print,
    exactly as normal, when the log can't be written."""
    log_dir = tmp_path / "decisions.jsonl"
    log_dir.mkdir()
    result = _run_isolated(tmp_path, "score", "--task", "rename a function", "--route", "--json",
                            extra_env={"COMPLEXITY_LOG": str(log_dir)})
    assert result.returncode == 0, result.stderr
    doc = json.loads(result.stdout)
    assert doc["one_liner"].startswith("[c-")
    assert "complexity: decision not logged" in result.stderr


@pytest.mark.parametrize("cmd", [["show"], ["stats"], ["outcome", "--id", "c-000000000000", "--result", "ok"]])
def test_show_stats_outcome_on_a_log_path_that_is_a_directory_clean_error(tmp_path, cmd):
    log_dir = tmp_path / "decisions.jsonl"
    log_dir.mkdir()
    result = _run_isolated(tmp_path, *cmd, extra_env={"COMPLEXITY_LOG": str(log_dir)})
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    combined = result.stdout + result.stderr
    assert "can't read the decision log" in combined


@pytest.mark.parametrize("cmd", [["show"], ["stats"], ["outcome", "--id", "c-000000000000", "--result", "ok"]])
def test_show_stats_outcome_on_an_unreadable_log_file_clean_error(tmp_path, cmd):
    if _running_as_root():
        pytest.skip("root can read anything; this case can't be exercised as root")
    log_file = tmp_path / "decisions.jsonl"
    log_file.write_text(json.dumps({"event": "decision", "id": "c-x", "ts": "t"}) + "\n", encoding="utf-8")
    log_file.chmod(0o000)
    try:
        result = _run_isolated(tmp_path, *cmd, extra_env={"COMPLEXITY_LOG": str(log_file)})
        assert result.returncode != 0
        assert "Traceback" not in result.stderr
        combined = result.stdout + result.stderr
        assert "can't read the decision log" in combined
    finally:
        log_file.chmod(0o644)


def test_outcome_with_unwritable_log_after_a_valid_read_is_a_clean_error(tmp_path):
    """The id-existence check reads the log fine (it's a normal file); only the
    append at the end fails. Must still be a clean, non-zero-exit error, not a
    false 'recorded' claim."""
    if _running_as_root():
        pytest.skip("root can write anywhere; this case can't be exercised as root")
    log_file = tmp_path / "decisions.jsonl"
    log_file.write_text(json.dumps({"event": "decision", "id": "c-real", "ts": "t"}) + "\n", encoding="utf-8")
    log_file.chmod(0o444)
    try:
        result = _run_isolated(tmp_path, "outcome", "--id", "c-real", "--result", "ok",
                                extra_env={"COMPLEXITY_LOG": str(log_file)})
        assert result.returncode != 0
        assert "Traceback" not in result.stderr
        assert "recorded" not in result.stdout
        assert "complexity: outcome not recorded" in (result.stdout + result.stderr)
    finally:
        log_file.chmod(0o644)


# ----------------------------------------------------------------------------
# F3: a torn or non-UTF-8 byte anywhere in the log must not crash a reader with
# UnicodeDecodeError -- every reader decodes with replacement and skips whatever
# line that produces if it then fails to parse as a JSON object.
# ----------------------------------------------------------------------------
def test_read_log_tolerates_a_non_utf8_byte_and_skips_the_damaged_line(route_mod, isolated_log):
    good = json.dumps({"event": "decision", "id": "c-good", "ts": "t"})
    isolated_log.write_bytes(f"{good}\n".encode("utf-8") + b'{"event":"decision","id":"c-bad","t":"\xc3"}\n')
    rows = route_mod.read_log()  # must not raise UnicodeDecodeError
    ids = [r.get("id") for r in rows]
    assert "c-good" in ids


def test_read_log_tail_tolerates_a_non_utf8_byte(route_mod, isolated_log):
    good = json.dumps({"event": "decision", "id": "c-good", "ts": "t"})
    isolated_log.write_bytes(f"{good}\n".encode("utf-8") + b'{"event":"decision","id":"c-bad","t":"\xc3"}\n')
    rows = route_mod.read_log_tail()  # must not raise UnicodeDecodeError
    ids = [r.get("id") for r in rows]
    assert "c-good" in ids


def test_show_on_a_log_with_a_torn_byte_does_not_crash(run_cli, isolated_log):
    isolated_log.write_bytes(b'{"event":"decision","id":"c-ffffffffffff","t":"\xc3"}\n')
    result = run_cli("show", "c-ffffffffffff")
    assert "Traceback" not in result.stderr
    # the record IS found (the line still parses, with a replacement char in the
    # damaged field); it's simply incomplete, not a crash -- see cmd_show's own
    # KeyError/TypeError handling for that case.
    assert result.returncode != 0


def test_stats_on_a_log_with_a_torn_byte_does_not_crash(run_cli, isolated_log):
    good = json.dumps({"event": "decision", "id": "c-good", "ts": "t", "mode": "inline",
                        "model": "haiku", "scores_used": {"K": 0}, "scores_given": {},
                        "unsure": [], "notes": [], "override": None})
    isolated_log.write_bytes(
        f"{good}\n".encode("utf-8") + b'{"event":"decision","id":"c-bad","t":"\xc3"}\n'
    )
    result = run_cli("stats")
    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    assert "2 decisions" in result.stdout
