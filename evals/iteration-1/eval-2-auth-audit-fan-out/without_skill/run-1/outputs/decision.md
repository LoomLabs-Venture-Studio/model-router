# Routing decision: "audit every route handler for missing auth checks and give me a report"

Repository: `/home/claude/complexity-workspace/fixture/shopapi` (read-only)

---

## 1. Routing decision (as shown to the user)

I'll do this inline, myself, right now. No subagents, no fan-out.

Here's why, in one breath: the whole route surface is 11 handlers across 6 files (~70 lines of route code) plus a 12-line auth module, auth is enforced only per-handler (there's no middleware and no router-level `dependencies=`, so nothing is protected anywhere except where a handler explicitly says so), and I've already read all of it while scoping. Splitting that across agents would cost more in briefing and merging than the audit itself, and it would fragment the part that actually matters: several of the things worth reporting are only visible when one reader holds `core/auth.py` and every route file at the same time. Handing it to a single subagent would just mean re-reading 12 tiny files to reconstruct what's already in my context.

- **Execution:** inline, in this session (Fable).
- **Verifier:** no separate verifier agent. Before I show you the report I'll run a mechanical completeness check (count of `@router.*` decorators in `app/routes/` must equal the number of rows in the report, and every "protected" claim must map to a `Depends(require_auth)` on that exact line). A grep does that job as well as an agent would.
- **Human gate:** none needed before the work. It's read-only, no code changes, no external side effects. The report is the gate: it ends with the findings ranked and a recommended fix pattern, and I won't touch any code unless you ask.
- **Time:** a few minutes.

If you'd rather keep this out of the main session (for example, you want to keep working on something else here), I can hand the exact brief below to one Sonnet subagent instead. Same output, slightly slower, and I'd still do the completeness check on what comes back.

---

## 2. Dispatch plan

### Shape

| Piece | Who | Model | Why this model |
|---|---|---|---|
| Enumerate handlers, classify auth state, rank, write report | Main session (inline) | Fable (the session model) | The judgment calls (authentication vs. authorization, ownership/IDOR, admin-only vs. logged-in, "public by design" vs. "forgotten") are the whole job, the surface is tiny, and it's already loaded in context. Nothing to gain from a cheaper model; nothing to gain from a stronger one. |
| Completeness cross-check | Main session (inline, mechanical) | n/a (grep) | Decorator count vs. report rows; `Depends(require_auth)` line numbers vs. "protected" claims. |
| Verifier agent | **Not dispatched** | (would be Haiku) | Threshold at which I'd add one: the audit becomes a compliance/release artifact, or the surface grows past what fits in one careful pass (roughly >40 handlers or >8 route files). Brief for that case is included at the bottom for reference. |
| Human gate | **None before work** | n/a | Read-only. The report itself is the decision point for what gets fixed. |

Fan-out considered and rejected: partitioning by route file (6 partitions of 5-20 lines each) would produce six readers who each lack `core/auth.py` context and can't see cross-file patterns; merging six differently-calibrated severity scales is more work than the audit.

### The brief (verbatim)

This is the brief I'm executing against inline. It is written so that it can be handed unchanged to one Sonnet subagent if the user prefers that.

```
TASK: Audit every HTTP route handler in the FastAPI service at
/home/claude/complexity-workspace/fixture/shopapi for missing or
insufficient auth checks, and write a report. The repository is
READ-ONLY: do not modify, create, or delete any file in it. Do not
propose diffs; describe fix patterns in prose only.

SCOPE (already established, verify rather than rediscover):
- App wiring: /home/claude/complexity-workspace/fixture/shopapi/app/main.py
  includes six routers: users, orders, checkout, billing, admin, health.
  There is no middleware and no router-level or app-level
  `dependencies=[...]`. Auth exists ONLY where a handler declares it.
- Auth primitives: /home/claude/complexity-workspace/fixture/shopapi/app/core/auth.py
  defines `require_auth` (a FastAPI dependency: checks for a
  "Bearer ..." Authorization header and returns the raw token string)
  and `require_admin(token)` (a plain function, NOT a dependency: must be
  called explicitly inside a handler).
- Route files: /home/claude/complexity-workspace/fixture/shopapi/app/routes/{users,orders,checkout,billing,admin,health}.py
- Expected handler count: 11 (users 2, orders 3, checkout 1, billing 2,
  admin 2, health 1). If you find a different number, say so and explain.

METHOD:
1. Enumerate. Grep `^@router\.(get|post|put|patch|delete)` across
   app/routes/. Every match is one row in the report. No handler may be
   omitted, including ones you judge to be fine.
2. For EACH handler, record:
   a. HTTP method, path, file, and line number of the decorator.
   b. What it does (read / write / delete / refund / privilege change)
      and which tables it touches.
   c. Authentication: does the signature contain `Depends(require_auth)`?
      Yes/No, with line number.
   d. Authorization: if the path is under /admin, is `require_admin(token)`
      actually called in the body? If the handler takes a `user_id`
      (path or query) and reads or mutates that user's data, is there
      ANY check that the caller is that user or an admin? Note that
      `require_auth` returns the raw token and never resolves it to a
      user identity, so "no ownership check" is the expected answer for
      every user_id-scoped handler; state it per handler anyway.
   e. Classification, exactly one of:
      - PUBLIC-BY-DESIGN: no auth, and that is clearly intentional
        (liveness probe, explicitly "public" endpoint). Say why you
        believe it is intentional; flag it as "confirm" if unsure.
      - MISSING-AUTHN: no `Depends(require_auth)` on a handler that
        reads or mutates non-public data.
      - MISSING-AUTHZ: `require_auth` is present, but the handler needs
        a stronger check it does not perform (admin-only path without
        `require_admin`; user-scoped data without an ownership check).
      - OK: authenticated AND the authorization level matches what the
        handler does.
   f. Severity: Critical / High / Medium / Low / Info. Rank by impact of
      the unauthenticated or over-privileged action (money movement,
      deletion, and privilege escalation outrank reads; reads of other
      users' PII outrank reads of the caller's own data).
3. Systemic findings. After the per-handler table, list issues that live
   in core/auth.py or in the wiring rather than in any one handler, e.g.
   token is never bound to a user identity; admin check is a hardcoded
   string compare; `require_admin` is easy to forget because it is not
   a dependency; no defense-in-depth at router/app level. Keep each to
   one or two sentences and say which handlers each one affects.
4. Recommended fix pattern, in prose, short: what the minimal change to
   auth.py and the routers would be to close the whole class of issue
   (e.g. resolve token to a user, expose a `require_admin` dependency,
   add router-level dependencies as a safety net, compare caller to
   user_id). No diffs, no code edits.

REPORT FORMAT (Markdown):
- Title, one-paragraph summary with counts: N handlers audited,
  N MISSING-AUTHN, N MISSING-AUTHZ, N PUBLIC-BY-DESIGN, N OK.
- "Method and coverage" section: how handlers were enumerated, the
  decorator count, and the explicit statement that no middleware or
  router-level auth exists (with file references), so the reader can
  trust the audit is complete.
- Per-handler table sorted by severity (Critical first), columns:
  Severity | Method+Path | File:line | Authn | Authz | Classification |
  What an attacker can do.
- "Systemic findings" section.
- "Recommended fix pattern" section.
- End with one line asking the user whether they want fixes drafted,
  and if so whether as a single PR or per-router.

CONSTRAINTS:
- Cite file:line for every claim about the presence or absence of a
  check. Do not say "appears to" when you can point at a line.
- Do not run the app, do not create shop.db, do not run pytest. Static
  reading only; everything needed is in the files above.
- Do not write any files inside the repository. Return the report as
  your final message.
```

### Completeness cross-check (run inline before presenting the report)

```
1. grep -c '^@router\.\(get\|post\|put\|patch\|delete\)' app/routes/*.py
   -> sum must equal the number of rows in the per-handler table (11).
2. grep -n 'Depends(require_auth)' app/routes/*.py
   -> every line here must appear as "Authn: yes @ file:line" in exactly
      one row; every row marked "Authn: yes" must appear here.
3. grep -n 'require_admin(' app/routes/*.py
   -> every /admin row marked "Authz: admin" must correspond to a line
      here; any /admin row without a match must be MISSING-AUTHZ.
4. Confirm 'dependencies=' and 'middleware' return zero hits under app/.
```

### Verifier brief (NOT dispatched; kept for the case where the threshold above is crossed)

Model: Haiku. Read-only.

```
You are verifying an auth audit report, not redoing it. Inputs: the
report (pasted below) and the route files under
/home/claude/complexity-workspace/fixture/shopapi/app/routes/ plus
/home/claude/complexity-workspace/fixture/shopapi/app/core/auth.py.
Check ONLY these mechanical facts and answer PASS or FAIL with a list
of discrepancies:
1. Every `@router.<method>(...)` decorator in app/routes/*.py appears as
   exactly one row in the report, with matching method, path, and
   file:line.
2. Every row that claims `Depends(require_auth)` is present points at a
   line that actually contains it; every row that claims it is absent
   points at a handler signature that does not contain it.
3. Every row under /admin that claims `require_admin` is called points
   at a line in that handler's body containing `require_admin(`.
4. The report's summary counts equal the row counts in its table.
Do not comment on severity, wording, or recommendations.
```

### Human gate

None before execution. The audit is static, read-only, and produces a document. The report closes with an explicit question ("draft fixes? one PR or per-router?") so any code change is a separate, user-initiated step.

---

## 3. Rationale

Eleven handlers and a twelve-line auth module fit in one context with room to spare, and the findings that matter most are cross-cutting (they depend on holding `auth.py` and every router together), so a single reader who already has everything loaded beats both a re-briefed subagent and a file-partitioned fan-out on accuracy, latency, and cost. Fan-out earns its overhead when the surface exceeds one context or partitions are truly independent; neither is true here, and a grep-based completeness check gives the same assurance a verifier agent would at a 70-line scale.
