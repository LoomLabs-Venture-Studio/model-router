# Routing decision: "audit every route handler for missing auth checks and give me a report"

Repo: `/home/claude/complexity-workspace/fixture/shopapi`. Session model: fable. Decision log: `decisions.jsonl` next to this file (two lines: the as-posed route that asked for a probe, then the operative decision `c-79df6d`).

## 1. The decision, as shown to the user

Scored as posed, the router asked for a probe (`c-eaae4b`: S and P unsure until the handlers are counted). The probe was a direct read: the whole repo is 15 files / 139 lines, smaller than a probe brief. It found 6 route modules, 11 handlers (`@router.*` decorators), 92 lines of route code, one per-handler `Depends(require_auth)` pattern, `require_admin` applied as a plain call in handler bodies, no router-level or middleware auth, no route tests, and an import-time side effect in `app/core/db.py`. Re-scored with that:

```
complexity  S2 R2 A1 K2 I3 P0 V2  kind=review  difficulty 5/9
route       single agent · general-purpose (read-only brief) · sonnet · effort default
why         keep the reading out of the main context (S>=2, I3)
verifier    yes · sonnet, independent (give it the artifact and the task, not the reasoning)
gate        K2: changes ship through review, no direct deploy
id          c-79df6d   log .../with_skill/outputs/decisions.jsonl
```

Why: I3 and S2 drove the single agent; P0 (6 modules, 11 handlers, 92 lines) killed the fan-out that the generic scoring for this prompt (S3 P3, 6-8 shards) would give; K2 floored the tier at sonnet and, with V2, added an independent verifier. R2 rather than the worked example's R1 because "auth check" here is three checks, one of which (`require_admin`) is a body call invisible to a signature scan, and the token carries no identity, so the auditor has to reason about which handlers *need* an admin or ownership check, not just whether one exists. The number to disagree with, if any, is P0: fan-out would be right for a repo with more than a few dozen handlers.

No human gate: the task is read-only and produces a report; nothing irreversible happens. Any fixes are a separate K2 implement task, routed on their own.

## 2. Dispatch plan

Two sequential Agent calls (the verifier needs the auditor's output, so they cannot share a message), then an inline reduce.

| Step | Agent tool call | Model | Why this model |
|---|---|---|---|
| 1. Audit | `subagent_type: general-purpose`, `model: "sonnet"`, foreground | sonnet | difficulty 5 (R2+A1+K2); K2 forbids haiku; nothing in a 92-line audit needs opus |
| 2. Verify | `subagent_type: general-purpose`, `model: "sonnet"`, foreground, brief includes the step-1 report verbatim | sonnet | policy: max(tier, sonnet) for K2+V2; independent (gets the report and the task, not the reasoning); at 11 handlers it re-derives the whole table rather than sampling |
| 3. Reduce | inline (this session) | fable (session) | mechanical: present the table, resolve any verifier disagreements by reading the cited lines, then `route.py outcome --id c-79df6d --result ok\|retry\|escalated` |

### Brief 1: auditor (verbatim)

```
Goal
  Report every HTTP route handler in /home/claude/complexity-workspace/fixture/shopapi that lacks an auth
  check it should have, with file:line evidence, so the user can decide what to fix.

Done means
  One row per route handler; 11 rows expected (count the `@router.<method>(` decorators in
  app/routes/*.py; if you get a different number, say so and explain). Every row has a verdict and
  evidence. A short "Systemic" section covers anything that is not per-handler. No edits, no new files.

Context you need
  - Small FastAPI service. Routers: app/routes/{users,orders,checkout,billing,admin,health}.py,
    mounted in app/main.py (5 lines). The whole app is about 140 lines; read all of app/ in full
    rather than grepping.
  - Auth lives in app/core/auth.py (11 lines). `require_auth` is a FastAPI dependency that checks for
    a `Bearer` header and returns the raw token string; it does not resolve the token to a user.
    `require_admin(token)` is a plain function, not a dependency: it only applies if a handler body
    calls it explicitly.
  - Handlers opt in per route via `Depends(...)` in the signature. Confirm for yourself whether
    app/main.py or any `APIRouter(...)` adds router-level `dependencies=` or middleware that would
    cover handlers implicitly, and state what you found.
  - migrations/001_init.sql lists the tables (users, products, cart, orders, payments); use it to
    judge how sensitive each handler's data is.
  - "Auth check" means three things. Assess every handler on all three:
      1. Authentication: is a token required at all (`Depends(require_auth)` or an equivalent)?
      2. Admin authorization: for handlers under /admin, or that change privileged state such as
         another user's tier, is `require_admin` applied to the authenticated token before the
         privileged action?
      3. Object-level authorization: for handlers that read or mutate a specific user's or record's
         data identified by a caller-supplied user_id / order_id / payment_id, is there any check
         that the caller may act on that object? If the current token design makes such a check
         impossible, say so once under Systemic and mark the affected rows accordingly rather than
         repeating it per row.

Already ruled out
  - Only `@router.*` handlers are in scope. app/core/* is context, not a target.
  - Do not propose or write fixes and do not redesign the auth layer: findings only, at most one
    sentence of suggested direction per GAP row.
  - Do not assess anything other than auth. SQL, concurrency and error handling are out of scope
    even if you notice problems; you may list at most three such observations at the end under
    "Out of scope, noticed".

Constraints
  - Read-only. Do not edit, create, rename or delete any file.
  - Static reading only: do not run the app, do not run pytest, do not import app.* (app/core/db.py
    opens a SQLite file at import time).
  - Stay inside /home/claude/complexity-workspace/fixture/shopapi.
  - Do not guess. If a verdict depends on a product decision (for example whether an endpoint is
    meant to be public), mark it NEEDS-DECISION and say what the decision is.

Return
  A markdown table, one row per handler, ordered by severity (privileged writes and money first,
  then deletes, then reads of personal data, then public info):
    handler | method+path | file:line | authn (yes/no) | authz needed (admin / ownership / none,
    3-6 word reason) | authz present (yes/no/n.a.) | verdict (OK / GAP / NEEDS-DECISION) |
    evidence (file:line of the check, or of the site where it is missing)
  Then "Systemic": at most 5 bullets for things that are not per-handler.
  Then one line: "N handlers, X GAP, Y NEEDS-DECISION, Z OK".
  Then, optionally, "Out of scope, noticed": at most 3 bullets.
  Under 60 lines total. No preamble, do not restate the code.
```

### Brief 2: verifier (verbatim; `<REPORT>` is replaced with the auditor's output, pasted in full and unedited)

```
Independent review. Read-only: do not edit or create files; do not run the app or pytest; do not
import app.* (app/core/db.py opens a SQLite file at import time).

Original task: "audit every route handler for missing auth checks and give me a report", against
/home/claude/complexity-workspace/fixture/shopapi. "Auth check" covers (1) authentication via
`Depends(require_auth)` or an equivalent, (2) admin authorization via `require_admin` on privileged
handlers, (3) object-level authorization for handlers acting on caller-supplied ids.

The report is attached at the bottom. You have not seen how it was produced; judge it on its own.

Do this in order, and do not read the report's verdict column until step 2:
  1. Build your own table first. Read app/main.py, app/core/auth.py and all six files in app/routes/
     in full (about 120 lines). List every `@router.*` handler with your own authn / authz-needed /
     authz-present / verdict.
  2. Compare row by row against the report. The expensive failure is a confident false negative, so
     check every OK row hardest. Also check that all 11 handlers are present, that every GAP cites a
     real file:line, and that the three check classes were applied consistently across modules
     (same situation, same verdict).
  3. Check the Systemic section for claims the code does not support.

Return: pass / pass with notes / fail; then the three most important findings with file:line; then
a table of rows where you disagree with the report (handler | report verdict | your verdict | why).
Under 30 lines.

--- REPORT ---
<REPORT>
```

### Reduce (inline)

Present the auditor's table with the verifier's verdict attached. For any row the two disagree on, read the cited lines (the repo is 139 lines, so this is cheaper than a third agent) and state which verdict stands and why. If the verifier returns `fail`, or the auditor's row count is not 11, re-run step 1 with the verifier's findings appended to "Context you need" and log the outcome as `retry`; if a second pass still disagrees, escalate the auditor to opus and log `escalated`. Otherwise log `ok`.

## 3. Rationale

The skill's generic scoring for this prompt (S3 P3) fans out by route module, but sizing the repo shows 6 modules, 11 handlers and 92 lines of route code, so P0: six shard briefs would each be longer than the code they audit, and the judgments that matter here (admin gating done as a body call, no token-to-user binding, no router-level coverage) are cross-module patterns that one reader who sees every handler catches once, where six shards each rediscover or miss them. K2 (a wrong auth report leaves holes in billing and admin paths unfixed) floors the tier at sonnet and, with V2, requires an independent verifier, which at 11 handlers can re-derive the whole table before reading the report's verdicts instead of sampling a few "no gap" claims.
