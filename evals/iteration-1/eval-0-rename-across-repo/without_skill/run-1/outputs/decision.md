# Routing decision: "rename get_user to fetch_user across the repo"

Repository: `/home/claude/complexity-workspace/fixture/shopapi` (git, clean tree at `c4c1184`, 14 files, 146 lines total).

---

## 1. Routing decision (as shown to the user)

**Doing this inline — no subagents, no verifier agent, no approval gate.**

`get_user` is a single function (`app/core/users.py:3`) with 15 occurrences in 7 files:

| File | Occurrences | Kind |
|---|---|---|
| `app/core/users.py` | 1 | definition |
| `app/core/pricing.py` | 2 | import + call |
| `app/routes/users.py` | 3 | import + 2 calls |
| `app/routes/orders.py` | 2 | import + call |
| `app/routes/admin.py` | 2 | import + call |
| `app/routes/checkout.py` | 2 | import + call |
| `tests/test_users.py` | 3 | import + call + test name `test_get_user_missing` |

I checked the things that make renames go wrong and none apply: no sibling symbols that share the prefix (`get_users`, `get_user_by_*`), no string or `getattr` references, no `__all__`, nothing in `README.md` or `migrations/`. The one judgment call is the test function `test_get_user_missing`; I'll rename it to `test_fetch_user_missing` so the test keeps matching the function it exercises, and I'll call that out in the summary.

Plan: one substitution restricted to those 7 files, then verify with a zero-hit grep for `get_user`, `python -m compileall`, and an import smoke test of `app.core.users` / `app.core.pricing`. I'll hand you `git diff --stat` to review and will not commit unless you ask. About 10 tool calls.

One heads-up: `pytest` and `fastapi` aren't installed in this environment and `shop.db` has no schema, so the test suite can't act as the check here; the grep + compile + import checks are the verification.

---

## 2. Dispatch plan

**Pieces:** 1 (the whole task). **Assignment:** main session, inline, on the session model (Fable 5.1). **Subagent briefs sent:** none. **Separate verifier:** none. **Human gate before execution:** none; the user reviews the diff after.

### 2a. Inline execution plan (the exact steps I would run)

```
Step 0 — Baseline (already done during triage)
  git status --short                      -> empty
  grep -rn get_user .                     -> 15 lines / 7 files (recorded above)
  grep -rnE '[A-Za-z0-9_]get_user|get_user[A-Za-z0-9_]' --include='*.py' .
                                          -> only tests/test_users.py:3 test_get_user_missing

Step 1 — Replace (explicit file list, not a recursive sweep; plain substitution is
         safe because Step 0 proved the only affixed occurrence is the test name,
         which we want renamed)
  sed -i 's/get_user/fetch_user/g' \
    app/core/users.py app/core/pricing.py \
    app/routes/users.py app/routes/orders.py app/routes/admin.py app/routes/checkout.py \
    tests/test_users.py

Step 2 — Verify (all deterministic; every one must pass)
  a. grep -rn get_user --include='*.py' --include='*.md' --include='*.sql' .   -> prints nothing
  b. grep -rn fetch_user .                                                     -> 15 lines, same 7 files
  c. python3 -m compileall -q app tests                                        -> exit 0
  d. cd <scratchpad> && PYTHONPATH=/home/claude/complexity-workspace/fixture/shopapi \
       python3 -c "from app.core.users import fetch_user; from app.core.pricing import price_for; print('ok')"
       (run from the scratchpad, not the repo: app/core/db.py creates shop.db in the cwd on import)
  e. git diff --stat                                                           -> 7 files changed
     git diff                                                                  -> read it once; only get_user->fetch_user hunks

Step 3 — Report to the user
  - git diff --stat output
  - note the test rename (test_get_user_missing -> test_fetch_user_missing)
  - note pytest could not be run here (not installed, no DB schema) and what was checked instead
  - no commit made

Rollback if any check fails: git checkout -- .   then report, do not improvise a second approach.
```

### 2b. Contingency brief (NOT sent; recorded for the case where the main session is mid-way through a large unrelated task and wants this kept out of its context)

Route: one subagent, model **haiku** (mechanical, fully pre-specified, deterministic checks). No verifier agent; on return the main session re-runs check (a) and `git diff --stat` itself (2 tool calls) instead of spending a model on review.

Brief, verbatim:

```
Rename the Python function `get_user` to `fetch_user` everywhere in
/home/claude/complexity-workspace/fixture/shopapi.

Facts already verified — do not re-explore the repo:
- Definition: app/core/users.py:3. Exactly 15 occurrences in exactly these 7 files:
  app/core/users.py, app/core/pricing.py, app/routes/users.py, app/routes/orders.py,
  app/routes/admin.py, app/routes/checkout.py, tests/test_users.py.
- The only occurrence with adjacent identifier characters is the test function
  `test_get_user_missing` (tests/test_users.py:3). Rename it to `test_fetch_user_missing`.
- There are no other symbols containing `get_user` (no get_users, get_user_by_*), no
  string or getattr references, no __all__, and no mentions in README.md or migrations/.
- Git working tree is clean at commit c4c1184.

Do:
1. In those 7 files only, replace every `get_user` with `fetch_user`. Plain substitution
   is safe given the facts above; do not widen the file list.
2. Verify, and paste the raw output of each check into your report:
   a. grep -rn get_user --include='*.py' --include='*.md' --include='*.sql' .
      -> must print nothing
   b. grep -rn fetch_user .
      -> 15 lines across the same 7 files
   c. python3 -m compileall -q app tests
      -> exit 0
   d. From your scratchpad directory (NOT from inside the repo — app/core/db.py creates
      shop.db in the cwd on import):
      PYTHONPATH=/home/claude/complexity-workspace/fixture/shopapi python3 -c \
        "from app.core.users import fetch_user; from app.core.pricing import price_for; print('ok')"
      -> prints ok
   e. git diff --stat
      -> exactly 7 files changed
3. Do NOT run pytest (it is not installed, and shop.db has no schema so the tests fail
   for unrelated reasons). Do NOT install packages. Do NOT commit. Do NOT create any file
   inside the repo.
4. If any check fails: run `git checkout -- .` to restore the tree, then report which
   check failed and its output. Do not retry with a different approach.

Report: the `git diff --stat` output, the five check outputs, and anything unexpected.
Keep it under 15 lines.
```

---

## 3. Rationale

The work is smaller than any brief that describes it — 15 pre-verified occurrences in 7 files with no ambiguity — so delegating would only add a second read of the repo, spawn latency, and a hand-off I would still have to check, and fanning out is actively harmful because the files share one definition and parallel editors would collide. Verification is a deterministic grep/compile/import triple that needs no second model, and the change is local, reversible with `git checkout`, and exactly what was asked, so nothing warrants a human gate before execution; the user's review point is the diff afterwards.
