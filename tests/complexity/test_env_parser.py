"""B4 (#26, #28): route.py's `.env` parser must accept the same shapes of
`export`-prefixed lines that the prompt hook already recognizes as "a key is set".

Before this sprint, `_read_env_file` only stripped a literal `"export "` (one
space) prefix. A line written as `export<TAB>TYPESAFE_API_KEY="value"` (a tab,
which `export` accepts and which the hook's presence check already tolerated)
was left with a key of `"export\\tTYPESAFE_API_KEY"`, which never matches
`TYPESAFE_API_KEY`, so the Jev path silently never ran even though a key was
configured (see PLAYBOOK.md, sprint "skill publish-hardening", criterion B4).

Every value here is `DUMMY-...`, never a real credential, and none of these
tests touch the network: they call the `.env` file parser directly.
"""
from __future__ import annotations


def write_env(path, text):
    path.write_text(text, encoding="utf-8")


def test_export_with_tab_is_recognized(route_mod, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_env(tmp_path / ".env", "export\tTYPESAFE_API_KEY=DUMMY-tab-value\n")
    assert route_mod._read_env_file(".env") == {"TYPESAFE_API_KEY": "DUMMY-tab-value"}


def test_export_with_multiple_spaces_is_recognized(route_mod, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_env(tmp_path / ".env", "export   TYPESAFE_API_KEY=DUMMY-spaces\n")
    assert route_mod._read_env_file(".env") == {"TYPESAFE_API_KEY": "DUMMY-spaces"}


def test_plain_export_single_space_still_works(route_mod, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_env(tmp_path / ".env", "export TYPESAFE_API_KEY=DUMMY-plain\n")
    assert route_mod._read_env_file(".env") == {"TYPESAFE_API_KEY": "DUMMY-plain"}


def test_no_export_prefix_still_works(route_mod, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_env(tmp_path / ".env", "TYPESAFE_API_KEY=DUMMY-noexport\n")
    assert route_mod._read_env_file(".env") == {"TYPESAFE_API_KEY": "DUMMY-noexport"}


def test_double_quoted_value_is_unwrapped(route_mod, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_env(tmp_path / ".env", 'export\tTYPESAFE_API_KEY="DUMMY-quoted"\n')
    assert route_mod._read_env_file(".env") == {"TYPESAFE_API_KEY": "DUMMY-quoted"}


def test_single_quoted_value_is_unwrapped(route_mod, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_env(tmp_path / ".env", "export\tTYPESAFE_API_KEY='DUMMY-quoted'\n")
    assert route_mod._read_env_file(".env") == {"TYPESAFE_API_KEY": "DUMMY-quoted"}


def test_crlf_line_endings_are_handled(route_mod, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_bytes(b"export\tTYPESAFE_API_KEY=DUMMY-crlf\r\n")
    assert route_mod._read_env_file(".env") == {"TYPESAFE_API_KEY": "DUMMY-crlf"}


def test_comments_and_blank_lines_are_skipped(route_mod, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_env(
        tmp_path / ".env",
        "\n# a comment about the key\n\nexport\tTYPESAFE_API_KEY=DUMMY-real\n# trailing comment\n\n",
    )
    assert route_mod._read_env_file(".env") == {"TYPESAFE_API_KEY": "DUMMY-real"}


def test_missing_file_is_not_an_error(route_mod, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert route_mod._read_env_file(".env") == {}


def test_get_api_key_reads_tab_after_export_from_dotenv(route_mod, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    write_env(tmp_path / ".env", "export\tTYPESAFE_API_KEY=DUMMY-end-to-end\n")
    assert route_mod.get_api_key() == "DUMMY-end-to-end"


def test_environment_still_wins_over_dotenv(route_mod, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_env(tmp_path / ".env", "export\tTYPESAFE_API_KEY=DUMMY-from-file\n")
    monkeypatch.setenv("TYPESAFE_API_KEY", "DUMMY-from-env")
    assert route_mod.get_api_key() == "DUMMY-from-env"


# ----------------------------------------------------------------------------
# F8 (#27, PLAYBOOK.md "skill publish-hardening", Task F): a packaged skill
# ships without the repo-only `.env.example`, so nothing inside
# skills/complexity/ may point a user at it -- the setup instruction must be
# self-contained (a `.env` file the user creates themselves).
# ----------------------------------------------------------------------------
def test_no_env_example_pointer_remains_inside_the_skill(route_mod):
    import pathlib
    skill_dir = pathlib.Path(route_mod.__file__).resolve().parents[1]
    assert skill_dir.name == "complexity"
    offenders = []
    for path in skill_dir.rglob("*"):
        if path.is_file() and path.suffix in (".py", ".sh", ".md"):
            if ".env.example" in path.read_text(encoding="utf-8", errors="replace"):
                offenders.append(str(path))
    assert not offenders, f".env.example still referenced in: {offenders}"
