# Routing decision: intermittent 500s on `/checkout` under load

Repo: `/home/claude/complexity-workspace/fixture/shopapi` (FastAPI + one sqlite file; 11 source files, 1 migration, 2 tests, single commit `c4c1184`).

---

## 1. Routing decision (as shown to the user)

**What I found.** I read the whole codebase (it is 11 files). `/checkout` has two race conditions and one data-dependent crash, all of which surface as 500s:

1. **`app/routes/checkout.py:18-21` — the `_inflight` dict is set and `del`'d, never checked, never locked.** Two overlapping requests for the same `user_id`: both set the key, the first `del` succeeds, the second `del _inflight[user_id]` raises `KeyError` -> 500. The same overlap also **double-charges** (both requests read the cart, both insert a `payments` row, both clear the cart). A load test that reuses one fixture user hits this almost immediately. This is almost certainly the 500 you are seeing.
2. **`app/core/db.py` — one sqlite connection shared across FastAPI's sync-endpoint threadpool with `check_same_thread=False`, and `_lock` is declared but never acquired.** Concurrent `execute`/`fetchall`/`commit` on a single connection produces sporadic `sqlite3.OperationalError` / `InterfaceError` / `ProgrammingError` on every endpoint; checkout is the most exposed because it does two commits per request.
3. **`checkout.py:16` — `query(...)[0][0]` on the product lookup.** Any cart row whose SKU is no longer in `products` (the schema has no foreign key) raises `IndexError` -> 500. Only some users' carts have stale SKUs, so it also reads as "intermittent."

Secondary defects on the same path (fix in the same change, low risk): `u = get_user(user_id)` is computed and discarded, so a missing user proceeds instead of returning 404; the `insert into payments` and `delete from cart` are two separate commits, so a failure between them leaves a payment recorded against a cart that was never cleared.

**How I will handle it: fix inline, then one verifier subagent.** The fix is roughly 40 lines across two files plus one new test file. I already have the whole codebase in context, and the three defects are coupled (same two files, and the repro test for one exercises the others), so there is nothing to parallelize and a fixer subagent would only re-read what I have already read. After the fix I will spawn **one opus verifier** that reproduces the 500 on the pre-fix commit, confirms the fix passes repeatedly, and reviews the diff for lock-scope and exception-path mistakes, which are exactly the errors a concurrency fix's own author is worst at catching.

**One behavior choice I am making; tell me if you want it different.** A checkout request that overlaps an in-flight checkout for the same user will get **409 Conflict** (today it gets a 500 and the user is charged twice). The alternative, serializing and returning the same result, is more work and changes the payment semantics; 409 is the conservative default.

**Not touching, but you should know:** `DELETE /orders/{id}` and `POST /billing/{id}/refund` have no auth dependency at all, and `POST /admin/users/{id}/tier` never calls `require_admin`. That is a separate security issue; I will not fold it into this change. Also, the repo has no deployment config. If you run multiple worker **processes** against this one sqlite file, an in-process lock does not cover cross-process write contention; I am adding a busy `timeout` on the connection as belt-and-braces, but tell me your worker count so I can confirm that layer.

**Human gate: at merge, not before the fix.** I will hand back a diff plus before/after evidence from the verifier, not a deploy.

---

## 2. Dispatch plan

### Piece A: diagnosis — done inline (fable, this session)

Already complete; see findings above. No dispatch.

### Piece B: fix + repro test — inline (fable, this session)

Not a brief, since I am doing it myself, but the spec I am holding myself to:

1. `app/core/db.py`
   - Acquire `_lock` in `query()` and `execute()`, held across `execute` + `fetchall` and across `execute` + `commit` respectively (not just around `execute`).
   - Add `timeout=` (busy timeout, e.g. 10s) to `sqlite3.connect` for cross-process contention.
   - Add a `transaction()` context manager that holds `_lock`, runs `BEGIN`, and commits on success / rolls back on exception, so checkout can do insert + delete atomically.
2. `app/routes/checkout.py`
   - Return 404 if `get_user` returns `None` (the existing `u` is currently unused).
   - Turn `_inflight` into a real guard: a module-level `threading.Lock`; check-and-set atomically; if the user is already in flight, raise `HTTPException(409, "checkout already in progress")`. Release in a `finally` so every exit path (including exceptions from `query`/`execute`/`price_for`) clears the key.
   - Replace `[0][0]` on the product lookup with an explicit check; return 422 naming the missing SKU instead of `IndexError`.
   - Perform `insert into payments` and `delete from cart` inside one `transaction()`.
   - Leave `price_for`'s signature alone so `tests/test_pricing.py` is untouched; the per-item `get_user` re-fetch is an N+1 nuisance, not a 500, and goes in a follow-up note.
3. `tests/test_checkout.py` (new)
   - Fixture: fresh sqlite file with `migrations/001_init.sql` applied, seeded with one user, two products, cart rows for that user. Note `db.py` opens the relative path `shop.db`, so tests run from the repo root and the fixture must point the module at a temp file (monkeypatch `app.core.db._conn`) rather than the real one.
   - `test_concurrent_same_user`: 20 threads POST `/checkout` for the same user at once via `TestClient`; assert exactly one 200, the rest 409, zero 5xx, exactly one `payments` row, cart empty.
   - `test_concurrent_mixed_users`: 10 users x 20 requests; assert zero 5xx and `payments` row count equals number of 200s.
   - `test_missing_product_is_4xx`: cart row with an unknown SKU; assert 422, not 500.
   - `test_missing_user_is_404`.
4. Run `pytest` (needs `pip install fastapi httpx pytest` and `shop.db` migrated) and confirm `test_users.py` and `test_pricing.py` still pass.

### Piece C: verifier — one subagent, model **opus**, spawned after Piece B lands in the working tree

Brief, verbatim:

```
You are an independent verifier. You did not write this fix; your job is to try to break it and report honestly. Do not modify the fix. Do not commit. Report only.

REPO: /home/claude/complexity-workspace/fixture/shopapi  (FastAPI + one sqlite file, `shop.db`, opened by relative path from the repo root)
PRE-FIX COMMIT: c4c1184  (the only commit)
FIX: uncommitted changes in the working tree. `git diff` shows them. Files touched: app/core/db.py, app/routes/checkout.py, tests/test_checkout.py (new).

SYMPTOM BEING FIXED: intermittent HTTP 500 on POST /checkout under load.

CLAIMED ROOT CAUSES (verify each independently; do not take my word for it):
  (1) app/routes/checkout.py: module-level `_inflight` dict was set and `del`'d without a check or a lock. Two overlapping requests for the same user_id -> second `del` raises KeyError -> 500, and both requests insert a payment (double charge).
  (2) app/core/db.py: single sqlite connection shared across the FastAPI threadpool (`check_same_thread=False`); `_lock` was declared and never acquired. Concurrent use of one connection -> sporadic sqlite3 errors on any endpoint.
  (3) app/routes/checkout.py: `query("select price from products where sku = ?")[0][0]` raises IndexError when a cart SKU is missing from `products` (no FK in schema).

CLAIMED FIX: db helpers now hold `_lock` across execute+fetchall / execute+commit and expose a `transaction()` context manager; checkout checks-and-sets `_inflight` under a lock and returns 409 on overlap, releases in `finally`, returns 404 for a missing user and 422 for a missing product, and does the payment insert + cart delete in one transaction.

ENVIRONMENT SETUP (do this first; nothing is pre-installed):
  cd /home/claude/complexity-workspace/fixture/shopapi
  pip install fastapi httpx pytest uvicorn
  Work from the repo root at all times because of the relative `shop.db` path.

PROTOCOL — do all four steps, in order:

STEP 1. Reproduce on the PRE-FIX code. The repro must FAIL here or it proves nothing.
  - `git stash` (or `git worktree add /tmp/prefix c4c1184` and work there — your choice, but say which).
  - Create a fresh shop.db from migrations/001_init.sql; seed one user (id=1, tier='basic'), two products, two cart rows for user 1.
  - Fire 20 concurrent POST /checkout?user_id=1 with header `Authorization: Bearer x` using fastapi.testclient.TestClient from a ThreadPoolExecutor.
  - Record: status code distribution, the exact exception text from the server log/traceback, and the number of rows in `payments` afterward.
  - EXPECTED on pre-fix: at least one 500 (KeyError from `del _inflight[user_id]`) and/or sqlite3 errors, and more than one payments row.
  - If TestClient does not produce overlap (all 200s, one payment row), do NOT conclude the race is unreproducible. Start `uvicorn app.main:app` in a subprocess and hit it with 20 threads using httpx. Report which harness you needed.
  - Run the seeded-stale-SKU case too (cart row with sku='nope'): expect 500 with IndexError pre-fix.

STEP 2. Confirm the fix.
  - `git stash pop` (or return to the main working tree).
  - Fresh shop.db, same seed.
  - Run `pytest tests/ -x -q` — all tests must pass, including the pre-existing tests/test_users.py and tests/test_pricing.py.
  - Run `pytest tests/test_checkout.py -q` TWENTY times in a loop (`for i in $(seq 20); do pytest tests/test_checkout.py -q || echo FAIL_RUN_$i; done`). Any failure in any run is a FAIL for the whole fix; a concurrency test that passes 19/20 is a broken fix, not flaky infrastructure.
  - Repeat the same 20-thread same-user hammer you used in Step 1 against the fixed code, with the same harness (TestClient or uvicorn): expect exactly one 200, nineteen 409, zero 5xx, exactly one payments row, empty cart.

STEP 3. Read the diff (`git diff c4c1184`) against this checklist. Answer each with PASS / FAIL / CONCERN and cite file:line.
  a. In db.py, is `_lock` held across BOTH `execute` and `fetchall` in `query()`? (Holding it only around `execute` still lets another thread's `commit` interleave with the fetch.)
  b. In db.py, is `_lock` held across BOTH `execute` and `commit` in `execute()`?
  c. Does the `transaction()` context manager hold `_lock` for its whole body, roll back on exception, and never leave the connection in a half-open transaction if the body raises?
  d. Is the `_inflight` check-and-set atomic (both the membership test and the insert under the same lock acquisition)? A check outside the lock followed by a set inside it is still a race.
  e. Is the `_inflight` key released in a `finally` that covers EVERY raise point after the set — including `get_user`, both `query` calls, `price_for`, and the transaction body? Trace each path by hand.
  f. On the 409 path, is the key left untouched (the in-flight request's key must not be deleted by the rejected request)?
  g. Could the 409 lock and db.py's `_lock` ever be acquired in opposite orders by two threads (deadlock)? Trace it.
  h. Missing product: does the code check the row list before indexing, and return a 4xx (not 500) that names the SKU?
  i. Missing user: 404 before any cart read or `_inflight` set?
  j. Are payment insert and cart delete inside the same transaction, so a failure between them cannot leave a payment row with an uncleared cart?
  k. Any behavior change to endpoints OTHER than /checkout beyond now going through the locked db helpers? (Diff should not touch orders/billing/admin/users/health or price_for's signature.)
  l. Does `sqlite3.connect` now pass a busy `timeout`? (Cross-process contention is out of scope for this fix but the timeout is cheap insurance; note if absent.)

STEP 4. Try to break it. Spend real effort here.
  - Mixed-user hammer: 10 user_ids x 20 requests each, all concurrent. Assert zero 5xx and `count(payments) == number of 200 responses`.
  - Hammer /checkout for user 1 while simultaneously hitting GET /orders and GET /billing/1 from other threads (exercises db.py lock across endpoints). Assert zero 5xx anywhere.
  - Inject a failure: monkeypatch `app.core.db.execute` (or the transaction) to raise on the cart delete, send one checkout, then send a second one for the same user. The second must NOT get 409 (the key must have been released by the finally) and `payments` must not contain a row from the failed first attempt (transaction rolled back).
  - Anything else you think of. If you find a way to get a 5xx or a duplicate payment, that is the most valuable thing you can report.

REPORT FORMAT (return as your final message, nothing else):
  - Step 1: harness used; status-code histogram; verbatim exception text(s); payments row count. State plainly whether the pre-fix code failed as claimed.
  - Step 2: pytest results; 20-run loop result (N/20 passed); post-fix hammer histogram and payments count.
  - Step 3: the a–l checklist with PASS/FAIL/CONCERN and file:line for each.
  - Step 4: what you tried, what happened.
  - VERDICT: one of SHIP / DO NOT SHIP / SHIP WITH NOTES, followed by at most five bullets of the concerns that matter, each anchored to a line in the diff and a concrete input that triggers it. Do not pad; if it is clean, say so in one line.

CONSTRAINTS: work only inside the repo and /tmp; leave the working tree exactly as you found it (re-stash-pop, remove any worktree you added, delete any shop.db you created that was not there before); do not commit; do not edit the fix — if you think it is wrong, say so in the report.
```

Dispatch mechanics: `Agent(model="opus", prompt=<brief above>)`, run in the background while I write up the change summary; block on its result before telling the user the fix is ready.

### Not dispatched

- **No fan-out.** The three defects share two files and one repro harness; parallel agents would collide on the same lines and each would need the full read I have already done.
- **No fixer subagent (sonnet or otherwise).** Handing a diagnosed, specified 40-line change to another agent costs a spawn, an environment setup, and a re-read for no independence gain; the independence that matters is at review, which is where the single subagent goes.
- **No pre-fix human gate.** The user asked for the fix; the only behavior decision (409 on overlap) is flagged and reversible. The blast radius of locking db.py is every endpoint, but it is a strict correctness change with negligible cost on sqlite, which serializes writes anyway. The gate is at merge, with evidence in hand.

---

## 3. Rationale

The codebase is small enough that the diagnosis was cheaper to do than to delegate, and the fix is coupled and short, so the only place a second context earns its cost is as an adversarial verifier of a concurrency change: a "fix" that passes its own flaky test once but leaves the race open is the specific failure mode here, and it is caught by a reviewer with a before/after protocol and a lock-scope checklist, not by more authors. Opus for the verifier because reviewing lock scope and exception paths is judgment work on a tiny diff, so the stronger model is nearly free; haiku would rubber-stamp and sonnet is adequate but the cost delta is trivial relative to shipping a broken race fix to a checkout endpoint.
