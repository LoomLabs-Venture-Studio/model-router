# Decision: "rename get_user to fetch_user across the repo"

Repo: `/home/claude/complexity-workspace/fixture/shopapi` (FastAPI, ~110 lines, 14 files, 1 commit, clean tree).
Router log: `decisions.jsonl` in this directory, id `c-0fdae2`.

## 1. Routing decision (as shown to the user)

```
complexity  S2 R0 A0 K1 I2 P0 V1  kind=implement  difficulty 1/9
route       single agent · general-purpose · haiku · effort default
why         keep the reading out of the main context (S>=2)
verifier    none
id          c-0fdae2   log /home/claude/complexity-workspace/iteration-1/rename-across-repo/with_skill/outputs/decisions.jsonl
task        rename get_user to fetch_user across the repo (shopapi: 15 occurrences in 7 files)
```

Why: S2 drove the single agent (15 occurrences in 7 files across `app/core`, `app/routes`, and `tests`, found by search); R0 A0 K1 kept it at haiku with no verifier (mechanical rename of an internal helper, not an HTTP path or schema, reversible in git); V1 because `grep` + `py_compile` + an import check is a complete mechanical oracle. P0: the definition and all call sites must land together, and 15 edits is far too small to shard.

What I checked before routing (read-only, in a scratch copy where a run was needed):

- `grep -rn get_user`: 15 lines in 7 files. 14 are the identifier itself (1 def, 6 imports, 7 calls); the 15th is the test name `test_get_user_missing`. No `get_users`, `get_user_by_*`, `_get_user`, or string-literal hits, and nothing in `README.md` or `migrations/*.sql`. A plain global replace within those 7 files is therefore safe and also renames the test to match.
- `get_user` is an internal helper in `app/core/users.py`; the public surface is the HTTP routes, whose paths do not change. No `__init__.py`/`__all__` re-exports.
- Baseline test state: both tests already fail with `sqlite3.OperationalError: no such table: users` (the migration is never applied; there is no conftest). `pytest` and `fastapi` are not installed in this environment either. So "pytest passes" is not a usable acceptance criterion; the brief uses a stdlib-only oracle and tells the agent not to "fix" the pre-existing failure.
- Importing `app.core.db` creates `shop.db` in the cwd as a side effect; the brief tells the agent to clean that up so the diff stays to the 7 files.

## 2. Dispatch plan

Mode: one foreground `Agent` call, `subagent_type: general-purpose`, `model: haiku` (set explicitly; do not inherit the session model). No verifier, no human gate, no probe (scope is already measured). Effort: default (nothing to set).

Brief sent to the agent, verbatim:

```
Goal
  In the repo at /home/claude/complexity-workspace/fixture/shopapi, rename the Python function
  `get_user` to `fetch_user` everywhere it is defined, imported, or called. Behavior is unchanged.

Done means
  1. `grep -rn get_user --include=*.py .` (from the repo root) returns zero lines.
  2. `grep -rn fetch_user --include=*.py .` returns 15 lines in 7 files.
  3. `python3 -m py_compile app/core/users.py app/core/pricing.py app/routes/users.py app/routes/orders.py app/routes/checkout.py app/routes/admin.py tests/test_users.py` exits 0.
  4. `python3 -c "from app.core.users import fetch_user; from app.core.pricing import price_for"` exits 0
     (stdlib-only import check; the route modules need fastapi, which may not be installed).
  5. `git status --short` shows exactly these 7 modified files and nothing untracked.
  6. If `python3 -m pytest -q` is runnable, run it once. Expected: the same 2 failures as before the rename,
     both `sqlite3.OperationalError: no such table: users` (see Already ruled out). Do not try to make them pass.

Context you need
  All 15 occurrences (this is the complete list; the repo has been searched):
    app/core/users.py:3        def get_user(user_id: int):          <- the definition
    app/core/pricing.py:1      from app.core.users import get_user
    app/core/pricing.py:4          u = get_user(user_id)
    app/routes/users.py:3      from app.core.users import get_user
    app/routes/users.py:8          return get_user(user_id)
    app/routes/users.py:12         u = get_user(user_id)
    app/routes/orders.py:4     from app.core.users import get_user
    app/routes/orders.py:13        if not get_user(user_id):
    app/routes/checkout.py:6   from app.core.users import get_user
    app/routes/checkout.py:12      u = get_user(user_id)
    app/routes/admin.py:4      from app.core.users import get_user
    app/routes/admin.py:14         if not get_user(user_id):
    tests/test_users.py:1      from app.core.users import get_user
    tests/test_users.py:3      def test_get_user_missing():         <- rename to test_fetch_user_missing
    tests/test_users.py:4          assert get_user(999999) is None
  There are no `get_users`, `get_user_by_*`, `_get_user`, or string-literal occurrences, so a plain
  `s/get_user/fetch_user/g` over exactly these 7 files does the whole job, including the test name.
  Per-file Edit calls are equally fine. Either way, run the checks under Done means afterwards.

Already ruled out
  - Do not add a backward-compatibility alias (`get_user = fetch_user`). The user asked for a rename and
    this repo is the only consumer.
  - Both tests already fail before your change with `sqlite3.OperationalError: no such table: users`
    (migrations/001_init.sql is never applied and there is no conftest). That is pre-existing and out of
    scope: do not add a conftest, create tables, or otherwise touch DB setup.
  - README.md and migrations/*.sql contain no occurrences; do not edit them.

Constraints
  - Edit only the 7 files listed above. Do not touch app/core/db.py, app/core/auth.py, app/main.py,
    app/routes/billing.py, app/routes/health.py, migrations/, or README.md.
  - Change nothing except the identifier: no signature, behavior, formatting, or HTTP path changes
    (`/users/{user_id}` etc. are URL paths, not the function; leave them alone).
  - Do not commit, do not create branches, do not `pip install` anything.
  - Importing the app creates `shop.db` in the cwd and py_compile creates `__pycache__/`. Delete both
    when you are done so only the 7 intended files show in `git status --short`.

Return
  Under 20 lines:
  - `git diff --stat`
  - one line per check 1-5 with its actual output (line counts / exit status), plus the pytest summary
    line if it ran
  - "done", or the single thing that blocked you
```

After the agent returns:

1. Read only the return block. If check 1 is not zero lines, or check 3/4 failed, or `git status` shows extra files: send one retry to the same haiku agent with the failing check pasted. A second miss escalates to sonnet with the same brief.
2. Report to the user: the `git diff --stat`, the check results, and the note that the two test failures are pre-existing (`no such table: users`) and unrelated to the rename.
3. Close the loop: `route.py outcome --id c-0fdae2 --result ok` (or `retry` / `escalated` / `failed`).

Override handling: if the user says "just do it here", inline is a perfectly good answer for this repo (the whole thing is ~110 lines and already in context); I would re-run with `--override inline` so the log records the divergence, then do the same 7 edits and 5 checks myself.

## 3. Rationale

This is the skill's textbook rename (S2 R0 A0 K1 I2 P0 V1, difficulty 1): the only real work is 15 mechanical edits plus a grep-and-compile check, so a single haiku agent does it correctly for near-zero cost while keeping seven files of edit churn out of the main context, and there is nothing a verifier or a human gate would catch that the zero-hit grep and import check do not. The repo reading changed the brief far more than the score: the suite is broken at baseline and the runner is not installed, so without a stdlib oracle and an explicit "do not fix the tests" line, a cheap agent would either report a spurious failure or start repairing DB setup.
