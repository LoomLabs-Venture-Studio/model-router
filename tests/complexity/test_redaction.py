"""C1 (#1, #2): the configured API key (and any 'Bearer <token>') must never reach
stdout, stderr, or the decision log, in plain or escaped form.

Before this sprint's fix:
  (a) `_error_excerpt` redacted the raw, still-JSON-encoded body text. For a
      non-object JSON value (a bare array or string) the key was never
      re-extracted from a parsed value, so a key containing a JSON metacharacter
      (a quote or backslash -- both legal, since the only check on the key is
      that it is printable ASCII) survived in its wire-escaped form: the literal
      substring search for the plain key missed the escaped version, and a
      program that decoded the "redacted" excerpt again would recover the key.
      Reproduced directly below in test_error_excerpt_redacts_a_key_containing_json_metacharacters.
  (b) `task`, `--override`, and `outcome --note` were persisted and printed
      verbatim: nothing redacted them before they reached the log or stdout.
      Reproduced in test_task_containing_the_key_is_redacted_in_log_and_stdout and
      the override/outcome-note tests below.

Every key used here is a DUMMY value, never a real credential. Tests that need a
"no key configured" baseline build their own environment (HOME/PATH only) and run
with cwd set to a fresh tmp_path with no .env, rather than relying on the shared
`run_cli` fixture (whose cwd is the repo root, which may have a real, gitignored
.env locally) -- so "is a key configured" is always exactly what the test set,
never a side effect of whatever the machine running the suite happens to have.
"""
from __future__ import annotations

import itertools
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ROUTE_PY = REPO_ROOT / "skills" / "complexity" / "scripts" / "route.py"

DUMMY_KEY = "DUMMY_KEY_123"
DUMMY_KEY_WITH_QUOTE = 'DUMMY"KEY_456'
DUMMY_KEY_WITH_BACKSLASH = "DUMMY\\KEY_789"


def run_route(tmp_path, args, key: str | None = None, extra_env: dict | None = None):
    """Run route.py as a subprocess with a fully explicit, minimal environment (no
    inherited TYPESAFE_API_KEY, no inherited anything) and cwd=tmp_path (never
    REPO_ROOT, so a real local .env can never be picked up). `key`, if given, is
    set as TYPESAFE_API_KEY; omit it to test the "no key configured" path."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"), "HOME": str(tmp_path)}
    if key is not None:
        env["TYPESAFE_API_KEY"] = key
    if extra_env:
        env.update(extra_env)
    return subprocess.run([sys.executable, str(ROUTE_PY), *args], capture_output=True, text=True,
                           timeout=10, cwd=str(tmp_path), env=env)


# ----------------------------------------------------------------------------
# Pure-function tests: redact_secrets, _redact_json_value, _error_excerpt
# ----------------------------------------------------------------------------
def test_redact_secrets_replaces_the_literal_key(route_mod):
    assert route_mod.redact_secrets(f"rotate TYPESAFE_API_KEY={DUMMY_KEY}", DUMMY_KEY) == \
        "rotate TYPESAFE_API_KEY=[redacted]"


def test_redact_secrets_is_a_noop_with_no_key_configured(route_mod):
    text = "rotate TYPESAFE_API_KEY=some-value, nothing to see here"
    assert route_mod.redact_secrets(text, None) == text
    assert route_mod.redact_secrets(text, "") == text


def test_redact_secrets_redacts_bearer_tokens_even_with_no_key(route_mod):
    out = route_mod.redact_secrets("Authorization: Bearer abc.def.ghi", None)
    assert out == "Authorization: Bearer [redacted]"
    assert "abc.def.ghi" not in out


def test_redact_json_value_recurses_into_nested_dicts_and_lists(route_mod):
    value = {
        "detail": [{"loc": ["header", "authorization"], "msg": f"bad token {DUMMY_KEY} here"}],
        "note": DUMMY_KEY,
        "count": 3,
        "ok": True,
        "nothing": None,
    }
    redacted = route_mod._redact_json_value(value, DUMMY_KEY)
    assert DUMMY_KEY not in json.dumps(redacted)
    assert redacted["count"] == 3 and redacted["ok"] is True and redacted["nothing"] is None
    assert redacted["detail"][0]["msg"] == "bad token [redacted] here"


# ----------------------------------------------------------------------------
# C1(a): _error_excerpt on a body that is valid JSON but not a dict at top level,
# and/or where the key contains a character JSON has to escape.
# ----------------------------------------------------------------------------
def test_error_excerpt_redacts_a_key_inside_a_bare_json_array(route_mod):
    body = json.dumps([DUMMY_KEY]).encode()
    out = route_mod._error_excerpt(body, DUMMY_KEY)
    assert DUMMY_KEY not in out


def test_error_excerpt_redacts_a_key_containing_json_metacharacters(route_mod):
    """The actual pre-fix leak: a bare JSON string at the top level containing a
    key with a quote character. The wire text escapes the quote as `\\"`, so a
    literal substring search for the plain key over the RAW (still JSON-encoded)
    text misses it -- which is exactly what the pre-fix `_error_excerpt` did for
    any non-dict JSON value. Fixed: redaction runs on the value AFTER json.loads
    has already turned `\\"` back into `"`, so it matches regardless of how the
    wire escaped it."""
    body = json.dumps(f"bad token {DUMMY_KEY_WITH_QUOTE} here").encode()
    out = route_mod._error_excerpt(body, DUMMY_KEY_WITH_QUOTE)
    assert DUMMY_KEY_WITH_QUOTE not in out
    # and decoding the excerpt again must not recover it either
    assert DUMMY_KEY_WITH_QUOTE not in out.encode().decode("unicode_escape", "replace")


def test_error_excerpt_redacts_a_key_containing_a_backslash(route_mod):
    body = json.dumps(f"bad token {DUMMY_KEY_WITH_BACKSLASH} here").encode()
    out = route_mod._error_excerpt(body, DUMMY_KEY_WITH_BACKSLASH)
    assert DUMMY_KEY_WITH_BACKSLASH not in out


def test_error_excerpt_redacts_a_dict_body_with_error_field(route_mod):
    body = json.dumps({"error": f"invalid request: {DUMMY_KEY}"}).encode()
    out = route_mod._error_excerpt(body, DUMMY_KEY)
    assert out == "invalid request: [redacted]"


def test_error_excerpt_redacts_a_key_nested_under_an_undocumented_field_name(route_mod):
    """FastAPI-style 422 bodies use "detail", not "error"/"message"; the excerpt
    falls back to the whole (redacted) dict in that case, and the key must not
    survive that fallback either."""
    body = json.dumps({"detail": [{"msg": f"bad value {DUMMY_KEY}"}]}).encode()
    out = route_mod._error_excerpt(body, DUMMY_KEY)
    assert DUMMY_KEY not in out


def test_error_excerpt_redacts_bearer_pattern_in_json_body(route_mod):
    body = json.dumps({"error": "rejected header Bearer abc.def.ghi"}).encode()
    out = route_mod._error_excerpt(body, DUMMY_KEY)
    assert "abc.def.ghi" not in out
    assert "Bearer [redacted]" in out


def test_error_excerpt_redacts_plain_text_non_json_body(route_mod):
    body = f"plain text error: {DUMMY_KEY} was rejected".encode()
    out = route_mod._error_excerpt(body, DUMMY_KEY)
    assert DUMMY_KEY not in out


def test_error_excerpt_omits_body_when_key_only_present_escaped_in_nonjson_text(route_mod):
    """A non-JSON body that isn't safely redactable (the key is present only in a
    backslash-escaped textual form a plain substring search would miss) must not
    be printed at all -- the caller falls back to the status code alone."""
    escaped = "".join(f"\\u{ord(c):04x}" for c in DUMMY_KEY)
    body = f"error trace containing {escaped} somewhere".encode()
    out = route_mod._error_excerpt(body, DUMMY_KEY)
    assert DUMMY_KEY not in out
    assert escaped not in out  # the raw escape sequence itself must not leak either


def test_error_excerpt_with_no_key_configured_changes_nothing_but_bearer(route_mod):
    body = b"plain text, nothing secret, Bearer sometoken here"
    out = route_mod._error_excerpt(body, "")
    assert out == "plain text, nothing secret, Bearer [redacted] here"


# ----------------------------------------------------------------------------
# F1 (blocking, PLAYBOOK.md "skill publish-hardening", Task F): a body holding the
# key in BOTH plain and encoded form, or double-escaped, must not leak a form that
# decodes back to the key. Both reproductions are exactly the ones the QA slice
# and the CTO confirmed:
#
#   before this fix, _error_excerpt(b'DUMMY_KEY_123 / DUMMY_KEY_123', key)
#   returned '[redacted] / DUMMY_KEY_123' -- the plain occurrence
#   was replaced, but the escaped one, sitting right next to it, was not, and
#   decoding the "redacted" excerpt again recovers the key. Cause:
#   _redact_nonjson_body only checked "is the key present ONLY in escaped form"
#   (`key in unescaped and key not in raw_text`), so a mixed body sailed through.
#
#   before this fix, a JSON body double-escaping the key (the wire text has TWO
#   backslashes before each \uXXXX, so one JSON decode only peels off one layer,
#   leaving literal backslash-u sequences in the decoded string) came back as
#   'DUMMY_KEY_123' -- not the plain key, but one more
#   unicode-escape decode recovers it. _redact_json_value only ever applied a
#   single literal replace to a decoded JSON string, never a second decode pass.
#
# The fix (_key_reachable_by_decoding) replaces this with a bounded fixpoint
# search: JSON-decode (walking every string), unicode-escape, percent-decode, and
# base64-decode, applied repeatedly for up to 5 rounds, in any combination. If the
# key turns up anywhere other than the plain text a literal replace already
# handles, the whole body is withheld -- never partially redacted.
# ----------------------------------------------------------------------------
def test_error_excerpt_omits_body_holding_the_key_both_plain_and_unicode_escaped(route_mod):
    escaped = "".join(f"\\u{ord(c):04x}" for c in DUMMY_KEY)
    body = f"{DUMMY_KEY} / {escaped}".encode()
    out = route_mod._error_excerpt(body, DUMMY_KEY)
    assert DUMMY_KEY not in out
    assert out == "(error body omitted: contained the key in an encoded form)"
    # and the excerpt itself must not decode back to the key either
    assert DUMMY_KEY not in out.encode().decode("unicode_escape", "replace")


def test_error_excerpt_omits_a_double_escaped_json_body(route_mod):
    """The wire text has two backslashes before each \\uXXXX (the outer JSON
    string escapes a backslash as `\\\\`), so a single JSON decode leaves the
    escape sequence literal in the decoded string -- it takes one more
    unicode-escape pass to reveal the key."""
    double_escaped = "".join(f"\\\\u{ord(c):04x}" for c in DUMMY_KEY)
    body = json.dumps({"error": f"bad token {double_escaped} here"}).encode()
    out = route_mod._error_excerpt(body, DUMMY_KEY)
    assert DUMMY_KEY not in out
    assert out == "(error body omitted: contained the key in an encoded form)"
    assert DUMMY_KEY not in out.encode().decode("unicode_escape", "replace")


def test_error_excerpt_omits_a_percent_encoded_key(route_mod):
    import urllib.parse
    # DUMMY_KEY is all letters/digits/underscore, all "unreserved" per RFC 3986,
    # so quote() would never encode any of it -- pick a key with a character that
    # actually gets percent-encoded, to exercise the decoder for real.
    key = "DUMMY:KEY@123!"
    quoted = urllib.parse.quote(key, safe="")
    assert quoted != key, "test setup: key must actually change under percent-encoding"
    body = f"redirect blocked: token={quoted}".encode()
    out = route_mod._error_excerpt(body, key)
    assert key not in out
    assert out == "(error body omitted: contained the key in an encoded form)"


def test_error_excerpt_omits_a_base64_encoded_key(route_mod):
    import base64
    encoded = base64.b64encode(DUMMY_KEY.encode()).decode()
    # Embedded in surrounding text (not itself valid base64 as a whole string) --
    # the decoder must find the blob, not just try the whole body verbatim.
    body = f"bad credential: {encoded} rejected".encode()
    out = route_mod._error_excerpt(body, DUMMY_KEY)
    assert DUMMY_KEY not in out
    assert out == "(error body omitted: contained the key in an encoded form)"


# ----------------------------------------------------------------------------
# F1 addition (team-lead review of 898fe53): HTML numeric/hex character
# references decode back to the key -- a realistic source is a proxy's or
# upstream server's HTML error page. `html.unescape` handles decimal (&#NNN;),
# hex (&#xHH;), and named (&amp;) entities.
# ----------------------------------------------------------------------------
def test_error_excerpt_omits_a_decimal_html_entity_encoded_key(route_mod):
    entities = "".join(f"&#{ord(c)};" for c in DUMMY_KEY)
    body = f"error page: {entities}".encode()
    out = route_mod._error_excerpt(body, DUMMY_KEY)
    assert DUMMY_KEY not in out
    assert out == "(error body omitted: contained the key in an encoded form)"


def test_error_excerpt_omits_a_hex_html_entity_encoded_key(route_mod):
    entities = "".join(f"&#x{ord(c):x};" for c in DUMMY_KEY)
    body = f"error page: {entities}".encode()
    out = route_mod._error_excerpt(body, DUMMY_KEY)
    assert DUMMY_KEY not in out
    assert out == "(error body omitted: contained the key in an encoded form)"


def test_error_excerpt_omits_a_key_mixed_plain_html_entity_characters(route_mod):
    # Alternate plain and &#NNN;-encoded characters within the same run.
    mixed = "".join(c if i % 2 == 0 else f"&#{ord(c)};" for i, c in enumerate(DUMMY_KEY))
    body = f"error page: {mixed}".encode()
    out = route_mod._error_excerpt(body, DUMMY_KEY)
    assert DUMMY_KEY not in out
    assert out == "(error body omitted: contained the key in an encoded form)"


def test_error_excerpt_omits_an_html_entity_key_nested_inside_json(route_mod):
    entities = "".join(f"&#{ord(c)};" for c in DUMMY_KEY)
    body = json.dumps({"error": f"bad token: {entities}"}).encode()
    out = route_mod._error_excerpt(body, DUMMY_KEY)
    assert DUMMY_KEY not in out
    assert out == "(error body omitted: contained the key in an encoded form)"


def test_error_excerpt_omits_a_key_present_both_plain_and_html_entity_encoded(route_mod):
    entities = "".join(f"&#{ord(c)};" for c in DUMMY_KEY)
    body = f"{DUMMY_KEY} / {entities}".encode()
    out = route_mod._error_excerpt(body, DUMMY_KEY)
    assert DUMMY_KEY not in out
    assert out == "(error body omitted: contained the key in an encoded form)"


# ----------------------------------------------------------------------------
# F1 property test: compose 0 to 3 layers of {json.dumps, unicode-escape the key,
# embed in a JSON object, embed in a JSON list, put the plain key alongside the
# current text} and confirm the excerpt never decodes back to the key through up
# to 5 rounds of the same decoders -- an independent check, not a call into
# route.py's own _key_reachable_by_decoding, so this cannot pass merely because
# the implementation agrees with itself.
# ----------------------------------------------------------------------------
def _uescape_key(text: str, key: str) -> str:
    escaped_key = "".join(f"\\u{ord(c):04x}" for c in key)
    return text.replace(key, escaped_key)


def _html_entity_encode_key(text: str, key: str) -> str:
    entities = "".join(f"&#{ord(c)};" for c in key)
    return text.replace(key, entities)


_PROPERTY_LAYERS = [
    lambda t, k: json.dumps(t),
    _uescape_key,
    _html_entity_encode_key,
    lambda t, k: json.dumps({"error": t}),
    lambda t, k: json.dumps([t]),
    lambda t, k: f"{k} / {t}",
]


def _independent_decode_once(text: str) -> list[str]:
    """One decode layer, written independently of route.py's _decode_candidates
    (same idea, separate implementation) so the property test below is a genuine
    check on the excerpt, not a tautology against the code under test."""
    out: list[str] = []

    def walk(v):
        if isinstance(v, str):
            yield v
        elif isinstance(v, dict):
            for k, vv in v.items():
                if isinstance(k, str):
                    yield k
                yield from walk(vv)
        elif isinstance(v, list):
            for vv in v:
                yield from walk(vv)

    try:
        out.extend(s for s in walk(json.loads(text)) if s != text)
    except json.JSONDecodeError:
        pass
    try:
        unescaped = text.encode("utf-8", "backslashreplace").decode("unicode_escape")
        if unescaped != text:
            out.append(unescaped)
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    import html
    unescaped_html = html.unescape(text)
    if unescaped_html != text:
        out.append(unescaped_html)
    import urllib.parse
    try:
        unquoted = urllib.parse.unquote(text, errors="strict")
        if unquoted != text:
            out.append(unquoted)
    except (UnicodeDecodeError, ValueError):
        pass
    return out


def _key_recoverable_within_rounds(text: str, key: str, rounds: int = 5) -> bool:
    seen = {text}
    frontier = [text]
    for _ in range(rounds):
        nxt = []
        for t in frontier:
            for candidate in _independent_decode_once(t):
                if candidate in seen:
                    continue
                seen.add(candidate)
                if key in candidate:
                    return True
                nxt.append(candidate)
        if not nxt:
            break
        frontier = nxt
    return False


def test_error_excerpt_property_never_leaks_through_composed_encodings(route_mod):
    """Exhaustive over the 6 layer transforms (json.dumps, unicode-escape the
    key, HTML-entity-encode the key, embed in a JSON object, embed in a JSON
    list, put the plain key alongside the current text) at depths 0 to 3
    (1 + 6 + 36 + 216 = 259 bodies), as PLAYBOOK.md's F1 criterion specifies
    (the HTML-entity layer added per the team lead's follow-up review). Every
    one must either be safely redacted (no trace of the key) or fully omitted
    -- and in neither case may the key be recoverable by decoding the excerpt
    itself."""
    key = "PROPTEST_DUMMY_KEY_9f2a"
    n = len(_PROPERTY_LAYERS)
    cases = 0
    for depth in range(4):
        for combo in itertools.product(_PROPERTY_LAYERS, repeat=depth):
            text = key
            for layer in combo:
                text = layer(text, key)
            body = text.encode("utf-8", "replace")
            out = route_mod._error_excerpt(body, key)
            assert key not in out, f"leaked plainly for depth {depth} combo {combo!r}"
            assert not _key_recoverable_within_rounds(out, key), \
                f"leaked via decoding for depth {depth} combo {combo!r}: {out!r}"
            cases += 1
    assert cases == sum(n ** d for d in range(4))


# ----------------------------------------------------------------------------
# CLI-level: task / override / outcome note redaction before print and before log
# ----------------------------------------------------------------------------
def test_task_containing_the_key_is_redacted_in_log_and_stdout(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    result = run_route(tmp_path, [
        "route", "--scores", "S1 R1 A1 K1 I1 P1 V1", "--kind", "implement",
        "--task", f"rotate TYPESAFE_API_KEY={DUMMY_KEY}", "--explain",
    ], key=DUMMY_KEY, extra_env={"COMPLEXITY_LOG": str(log_file)})
    assert result.returncode == 0, result.stderr
    assert DUMMY_KEY not in result.stdout
    assert "[redacted]" in result.stdout
    logged = log_file.read_text(encoding="utf-8")
    assert DUMMY_KEY not in logged
    assert "[redacted]" in logged


def test_override_containing_the_key_is_redacted_in_log_and_stdout(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    result = run_route(tmp_path, [
        "route", "--scores", "S1 R1 A1 K1 I1 P1 V1", "--kind", "implement",
        "--task", "t", "--override", f"user said use TYPESAFE_API_KEY={DUMMY_KEY} anyway",
        "--explain",
    ], key=DUMMY_KEY, extra_env={"COMPLEXITY_LOG": str(log_file)})
    assert result.returncode == 0, result.stderr
    assert DUMMY_KEY not in result.stdout
    logged = log_file.read_text(encoding="utf-8")
    assert DUMMY_KEY not in logged


def test_outcome_note_containing_the_key_is_redacted_in_log(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    route_result = run_route(tmp_path, [
        "route", "--scores", "S1 R1 A1 K1 I1 P1 V1", "--kind", "implement", "--task", "t",
    ], key=DUMMY_KEY, extra_env={"COMPLEXITY_LOG": str(log_file)})
    assert route_result.returncode == 0, route_result.stderr
    decision_id = json.loads(log_file.read_text().splitlines()[-1])["id"]

    result = run_route(tmp_path, [
        "outcome", "--id", decision_id, "--result", "ok",
        "--note", f"had to paste TYPESAFE_API_KEY={DUMMY_KEY} to debug it",
    ], key=DUMMY_KEY, extra_env={"COMPLEXITY_LOG": str(log_file)})
    assert result.returncode == 0, result.stderr
    assert DUMMY_KEY not in result.stdout
    logged = log_file.read_text(encoding="utf-8")
    assert DUMMY_KEY not in logged


def test_score_route_task_containing_the_key_is_redacted(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    result = run_route(tmp_path, [
        "score", "--task", f"rotate TYPESAFE_API_KEY={DUMMY_KEY}", "--route", "--explain",
    ], key=DUMMY_KEY, extra_env={"COMPLEXITY_LOG": str(log_file)})
    assert result.returncode == 0, result.stderr
    assert DUMMY_KEY not in result.stdout
    logged = log_file.read_text(encoding="utf-8")
    assert DUMMY_KEY not in logged


def test_task_with_no_key_configured_and_no_secret_pattern_is_unaffected(tmp_path):
    """When no key is configured (and the text has no 'Bearer ...' in it), the
    redaction pass changes nothing: same output with or without a key configured,
    for text that never contained one."""
    log_file = tmp_path / "decisions.jsonl"
    result = run_route(tmp_path, [
        "route", "--scores", "S1 R1 A1 K1 I1 P1 V1", "--kind", "implement",
        "--task", "an ordinary task with nothing secret in it", "--explain",
    ], key=None, extra_env={"COMPLEXITY_LOG": str(log_file)})
    assert result.returncode == 0, result.stderr
    assert "task        an ordinary task with nothing secret in it" in result.stdout


def test_task_with_bearer_pattern_is_redacted_even_with_no_key_configured(tmp_path):
    log_file = tmp_path / "decisions.jsonl"
    result = run_route(tmp_path, [
        "route", "--scores", "S1 R1 A1 K1 I1 P1 V1", "--kind", "implement",
        "--task", "saw this in the logs: Authorization: Bearer sk-liveSECRETTOKEN", "--explain",
    ], key=None, extra_env={"COMPLEXITY_LOG": str(log_file)})
    assert result.returncode == 0, result.stderr
    assert "sk-liveSECRETTOKEN" not in result.stdout
    assert "Bearer [redacted]" in result.stdout
    logged = log_file.read_text(encoding="utf-8")
    assert "sk-liveSECRETTOKEN" not in logged
