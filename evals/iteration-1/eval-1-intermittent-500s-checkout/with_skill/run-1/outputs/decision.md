# Routing decision: intermittent 500s on /checkout under load

Repo: `/home/claude/complexity-workspace/fixture/shopapi` (FastAPI + SQLite, 15 files, 146 lines, one commit `c4c1184`).
Session model: Fable 5.1. Decision log: `decisions.jsonl` beside this file (ids `c-29b430`, `c-41ce3c`).

---

## 1. The routing decision (as shown to the user)

### Pass 1: the task as posed

```
complexity  S2? R3 A2 K2 I1 P0 V2  kind=implement  difficulty 7/9
route       probe first (Explore, haiku, read-only) -> re-score S/P/R/V -> route again · task tier so far: opus
why         route again after the probe; probe: scope unsure
verifier    yes · opus, independent (give it the artifact and the task, not the reasoning)
gate        K2: changes ship through review, no direct deploy
id          c-29b430   log .../with_skill/outputs/decisions.jsonl
```

Why: R3 ("intermittent under load" is the textbook concurrency anchor) and K2 (checkout writes payments) put this at opus with a mandatory verifier; S is unsure because I did not yet know how many services sit on the checkout path, so the policy asks for a probe before choosing a shape.

**Probe result.** The card calls for a haiku Explore agent. I ran the probe myself instead: the whole repository is 146 lines across 15 files, which is less text than the probe brief plus the agent's overhead, and the task allowed reading the repo. In a larger repo this would have been dispatched (the brief is in section 2, step 0). Facts only, as the probe template asks:

- Checkout path: `app/routes/checkout.py` (22 lines) -> `app/core/db.py`, `app/core/pricing.py`, `app/core/users.py`, `app/core/auth.py`. Five files, all tiny. Nothing else is involved; no services, queues, or external calls.
- `app/core/db.py:2` opens **one module-level `sqlite3` connection with `check_same_thread=False`** that every request thread shares. `db.py:3` creates `_lock = threading.Lock()` and **nothing ever acquires it**. Every FastAPI handler here is a sync `def`, so Starlette runs them concurrently on a threadpool. Unserialized concurrent use of one sqlite3 connection raises `ProgrammingError`/`InterfaceError`/`OperationalError` nondeterministically, i.e. exactly "intermittent 500s under load". This affects every route; /checkout is the one doing 2N+2 statements per request, so it surfaces there first.
- `checkout.py:8,18,21`: `_inflight` is a module dict written at 18 and `del`-ed at 21 with no `try/finally`, and nothing reads it. Two concurrent checkouts for the same `user_id` -> the second `del` raises `KeyError` -> 500.
- `checkout.py:19-20`: payment insert and cart delete are two separate autocommits, not one transaction. A failure between them charges the user and leaves the cart, so a retry charges again. Data-consistency defect on a payments write.
- `checkout.py:16`: `query(...)[0][0]` with no guard; a cart row whose sku is missing from `products` (schema has no foreign keys) is an `IndexError` -> 500. Data-dependent, not load-dependent.
- Amplifier, not a cause: `get_user` runs at line 12 (result unused) and again per cart item inside `price_for`.
- Tests: `tests/test_pricing.py`, `tests/test_users.py`, two trivial unit tests, no checkout/HTTP/concurrency coverage, no load test, no CI, no deps file. The tests hit the real `./shop.db` through the module-level connection (no fixture), so any new test needs a temp-DB fixture.
- Independent pieces: 1. The checkout fix depends on the transaction primitive the db layer exposes, and the tests depend on both.
- Out of scope but seen: `billing.refund`, `orders.delete_order` have no auth dependency; `admin.set_tier` never calls `require_admin`. Separate ticket.

### Pass 2: re-scored after the probe

```
complexity  S1 R3 A1 K2 I1 P0 V2  kind=implement  difficulty 6/9
route       inline · at least opus (delegate one agent at opus if this session is below it)
why         small and sequential; main thread already has the context
verifier    yes · opus, independent (give it the artifact and the task, not the reasoning)
gate        K2: changes ship through review, no direct deploy
id          c-41ce3c   log .../with_skill/outputs/decisions.jsonl
```

Why: R3 and K2 hold the tier at opus and force the independent verifier; S dropped 2->1 (five known files, fix touches two plus tests) and A dropped 2->1 (cause identified, one product default to take), so with P0 and I1 the policy keeps the fix **inline**. This session is Fable, above the opus floor, so no implementer agent is needed. No fan-out: the pieces share `db.py`'s interface and are not independent.

The one default I am taking (A1): `_inflight` is treated as the double-submit guard it was evidently meant to be, so a concurrent checkout for the same user gets a **409**, not a 500 and not a second payment. Say so if you would rather they queue.

Bottom line for the user: I will do the fix here (this session), then send the diff to an independent opus reviewer that has not seen my reasoning, and the change goes out through normal PR review, not a direct deploy. No irreversible step, so no human gate beyond review.

---

## 2. Dispatch plan

| Step | What | Where / model | Type | Brief |
|---|---|---|---|---|
| 0 | Probe: size the task | Explore, haiku, read-only | probe | Executed inline (repo is 146 lines); brief below is what would have been sent |
| 1 | Diagnose and fix | **Inline, this session (Fable; policy floor opus, effort high)** | implement | Work plan below; it doubles as the brief for one `general-purpose` opus agent if the session were below opus |
| 2 | Independent verification | One `Agent` call: `subagent_type: general-purpose`, `model: opus`, foreground | review (read-only) | Verifier brief below; gets the diff and the task, not my reasoning |
| 3 | Close the loop | `route.py outcome --id c-41ce3c --result ok\|retry\|escalated\|failed` | - | After the verifier returns |
| gate | K2 | Changes ship through PR review; I do not commit, push, or deploy | - | - |

Not used: fan-out (P0), sonnet or haiku on the fix path (K2 floors shards at sonnet and R3 needs opus anyway), human sign-off (K<3, V<3).

### Step 0 brief (probe; not dispatched, executed inline)

```
Read-only. Size this task, do not start it: "We're seeing intermittent 500s on /checkout under load. Find the cause and fix it."
Repo: /home/claude/complexity-workspace/fixture/shopapi
Return:
  - files or modules on the /checkout request path, with a count
  - how the app runs handlers (sync/async, threadpool, workers) and how the DB connection is created and shared
  - anything shared across requests (module-level state, globals, caches, locks) with file:line
  - test coverage of checkout and of the db layer (suite name, or "none"); any load test or CI config
  - what changed recently (git log for the checkout path)
  - anything that makes this bigger than it sounds (other services, queues, external calls, migrations, feature flags)
  - how many independent pieces a fix splits into
Ten lines or fewer. Facts only, no diagnosis.
```

### Step 1: inline work plan (verbatim brief if delegated to one opus `general-purpose` agent)

```
Goal
  /checkout returns no 5xx under concurrent load, and a checkout either records the payment and empties
  the cart together or does neither.

Done means
  - tests/test_checkout.py exists and passes three runs in a row:
      (a) 20+ threads POST /checkout for the same user_id concurrently: zero 5xx, exactly one payment row,
          cart empty, every other request 409 (or 200 with no duplicate payment if you have a reason to
          queue instead; say which you chose);
      (b) 20+ threads for 20 distinct users concurrently: zero 5xx, exactly one payment per user;
      (c) a cart row whose sku is not in products returns a 4xx, not a 500.
  - The two existing tests still pass, and every test runs against a temporary database, never ./shop.db.
  - `git status` shows changes only under app/core/db.py, app/routes/checkout.py, tests/.

Context you need
  - Repo: /home/claude/complexity-workspace/fixture/shopapi (FastAPI, SQLite, one commit c4c1184).
  - Every handler is a sync `def`, so Starlette runs them on a threadpool: requests are concurrent across threads.
  - app/core/db.py:2 opens one module-level sqlite3 connection with check_same_thread=False that all request
    threads share; db.py:3 creates `_lock` and nothing acquires it; `execute` commits after every statement.
  - app/routes/checkout.py: `_inflight` (line 8) is a module dict set at line 18 and `del`-ed at line 21 with
    no try/finally, and nothing reads it; the payment insert (19) and cart delete (20) are separate autocommits;
    line 16 does `query(...)[0][0]` with no guard; get_user runs at line 12 (unused) and again per item inside
    price_for (app/core/pricing.py:4).
  - Schema: migrations/001_init.sql; no foreign keys. Tests: tests/test_pricing.py, tests/test_users.py, both
    trivial, both currently depend on ./shop.db existing with the schema.
  - Default design unless you find a reason not to: keep the single connection, actually use `_lock`, and add a
    `transaction()` context manager in db.py that holds the lock, runs BEGIN IMMEDIATE, and commits or rolls
    back. Treat `_inflight` as the double-submit guard it was meant to be: check-and-set under a lock, 409 on a
    duplicate, release in `finally`. SQLite serializes writers regardless, so a process-wide lock costs little;
    per-thread connections are the alternative if you want reads to overlap, but that is a bigger diff to a
    payments path.
  - Trap: if `transaction()` holds `_lock` and query/execute also take `_lock`, a plain threading.Lock deadlocks
    on the first nested call. Use an RLock, or have the inner calls skip the lock inside a transaction, and
    cover that path in the threaded test.
  - Make the DB path configurable (env var or a module-level setter) so the test fixture can point it at a
    temp file created from migrations/001_init.sql.

Already ruled out
  - Not the cause: require_auth (stateless), the pricing math, the schema.
  - Out of scope, do not fix here, mention in your return instead: billing refund, orders delete and admin
    set_tier lack auth checks.
  - Do not add a dependency, an ORM, or a connection-pool library.

Constraints
  - Edit only app/core/db.py, app/routes/checkout.py, tests/. Do not change the /checkout request or the
    success response shape; new status codes for new error paths are fine.
  - Do not run against or modify ./shop.db.
  - Do not commit, push, or deploy.

Return
  The diff, the pytest output from three consecutive runs, and five lines: the root cause(s) with file:line,
  the design you chose and why, and any product question the reviewer should decide (409 vs queue).
```

### Step 2: verifier brief (verbatim; one `general-purpose` agent, `model: opus`, read-only)

Agent call: `description: "Independent review of checkout concurrency fix"`, `subagent_type: "general-purpose"`, `model: "opus"`, foreground (its verdict is needed before closing the loop).

```
Independent review. Do not edit any file. Original task: "We're seeing intermittent 500s on /checkout under
load. Find the cause and fix it."

Artifact: the uncommitted working-tree changes in /home/claude/complexity-workspace/fixture/shopapi against
commit c4c1184. Get them with `git status --short` and `git diff`; new files are untracked, read them directly.
You have not seen how the change was produced; judge it on its own.

Context you need
  FastAPI app with sync `def` handlers, so requests run concurrently on a threadpool. SQLite through
  app/core/db.py. The endpoint is app/routes/checkout.py. The payments table is money-adjacent data.
  Schema in migrations/001_init.sql. Run tests with `pytest` from the repo root.

Check
  1. Does it do what the task asked: is every load-correlated 500 path on /checkout closed? Reason about
     interleavings of two or more requests, same user and different users, not just the happy path. Name any
     path left open.
  2. Concurrency: is every DB access either serialized or per-thread? Can one thread try to acquire the same
     non-reentrant lock twice (deadlock)? Is the lock released on every exception path?
  3. Atomicity: are the payment insert and the cart delete committed together or not at all? What happens if an
     exception is raised between them?
  4. Any in-flight or double-submit guard: set and cleared under the same lock, cleared on every exit path
     (exceptions and rejection included), and does it reject a concurrent same-user checkout rather than crash
     or double-charge?
  5. Regressions: do users, orders, billing, admin and health still work with the changed db layer? Does the
     change serialize more than SQLite already requires?
  6. Tests: run `pytest` three times; a threaded test must be stable. Confirm the new threaded test would have
     caught the bug: `git worktree add <scratch-dir> c4c1184`, copy the new test file(s) in, run pytest there,
     expect red; remove the worktree afterwards. If it is green there, the test does not exercise the race.
  7. Is any claim in comments, docstrings or the summary unsupported by the code?

Out of scope (do not report as failures): billing refund, orders delete and admin set_tier lacking auth checks
are known and tracked separately.

Return
  pass / pass with notes / fail, then the three most important findings, each as file:line, one sentence, and
  the interleaving or input that triggers it. Under 25 lines.
```

---

## 3. Rationale

R3 (a thread-shared SQLite connection with an unused lock, a non-atomic payment write, and a racy guard) and K2 (payments path; `db.py` is shared by every route) set the tier at opus and make an independent verifier mandatory; the probe brought S to 1 and A to 1, so with P0 and I1 the policy keeps the fix inline in this Fable session, where the whole checkout path is already in context and the diagnosis stays close to the user. The only dispatch is therefore the opus verifier, which receives the diff and the task but not my reasoning, and the K2 gate means the change ships through PR review rather than a direct deploy.
