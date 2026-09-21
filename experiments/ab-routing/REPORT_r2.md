# A/B Routing Experiment Report

## Headline

- **Result:** A passed 51/51, B passed 51/51. Pass bar (of what B passed, how much did A also pass): 51/51 = 1.000 -> **MEETS** the bar.

Threshold: 90%.

**Cost:** Arm A $6.37, Arm B $9.09. Ratio A/B: 0.700.

## Per-Task Results

| Task | A passed/total | B passed/total | A target | B target | A guard | B guard | Cost A | Cost B | Ratio A/B |
|------|-----------------|-----------------|----------|----------|---------|---------|--------|--------|-----------|
| 1 | 10/10 | 10/10 | 8/8 | 8/8 | 2/2 | 2/2 | $1.26 | $2.26 | 0.559 |
| 2 | 17/17 | 17/17 | 10/10 | 10/10 | 7/7 | 7/7 | $3.32 | $3.14 | 1.059 |
| 3 | 11/11 | 11/11 | 8/8 | 8/8 | 3/3 | 3/3 | $0.87 | $2.37 | 0.365 |
| 4 | 13/13 | 13/13 | 10/10 | 10/10 | 3/3 | 3/3 | $0.92 | $1.32 | 0.693 |
| **Total** | 51/51 | 51/51 | 36/36 | 36/36 | 15/15 | 15/15 | $6.37 | $9.09 | 0.700 |

## Comparison

Jev-routed is Arm A of run `r1` (Phase 1). Claude-routed is Arm A of this run (Phase 2). The all-session-model baseline is this run's Arm B.

| Task | Row | Tests passed/total | Task-agent cost | Overhead (full) | Overhead (marginal) | Total cost | Cost / baseline |
|------|-----|---------------------|------------------|------------------|----------------------|------------|-----------------|
| 1 | Jev-routed | 10/10 | $0.6901 | $0.0001 | $0.0001 | $0.6902 | 0.305 |
| 1 | Claude-routed | 10/10 | $0.6751 | $0.5878 | $0.0226 | $1.2629 | 0.559 |
| 1 | Baseline | 10/10 | $2.2607 | $0.0000 | $0.0000 | $2.2607 | 1.000 |
| 2 | Jev-routed | 17/17 | $0.7026 | $0.0941 | $0.0941 | $0.7967 | 0.254 |
| 2 | Claude-routed | 17/17 | $2.5066 | $0.3914 | $0.0598 | $3.3227 | 1.059 |
| 2 | Baseline | 17/17 | $3.1368 | $0.0000 | $0.0000 | $3.1368 | 1.000 |
| 3 | Jev-routed | 11/11 | $1.2763 | $0.0774 | $0.0774 | $1.3537 | 0.571 |
| 3 | Claude-routed | 11/11 | $0.5125 | $0.3531 | $0.0247 | $0.8656 | 0.365 |
| 3 | Baseline | 11/11 | $2.3723 | $0.0000 | $0.0000 | $2.3723 | 1.000 |
| 4 | Jev-routed | 13/13 | $0.6332 | $0.0001 | $0.0001 | $0.6333 | 0.478 |
| 4 | Claude-routed | 13/13 | $0.5584 | $0.3594 | $0.0293 | $0.9178 | 0.693 |
| 4 | Baseline | 13/13 | $1.3235 | $0.0000 | $0.0000 | $1.3235 | 1.000 |
| **Total** | Jev-routed | 51/51 | $3.3022 | $0.1717 | $0.1717 | $3.4739 | 0.382 |
| **Total** | Claude-routed | 51/51 | $4.2526 | $1.6917 | $0.1363 | $6.3690 | 0.700 |
| **Total** | Baseline | 51/51 | $9.0933 | $0.0000 | $0.0000 | $9.0933 | 1.000 |

### What each router chose

| Task | Jev (compare run) | Claude (this run) |
|------|--------------------|--------------------|
| 1 | [c-85de41 · S2R2A1K1?I1?P0?V1 · agent sonnet] | model=sonnet mode=agent probe_first=False verifier=None confidence=0.86 |
| 2 | [c-bfda87 · S2R2A1K2I1P2V1 · fan-out 6xsonnet] | model=opus mode=agent probe_first=False verifier=sonnet confidence=0.84 |
| 3 | [c-313b57 · S1R2A1K3I2P1V1 · fan-out 3xopus · gate:confirm] | model=sonnet mode=agent probe_first=False verifier=None confidence=0.85 |
| 4 | [c-2f44d9 · S2R2A1K2I1?P2V1 · fan-out 6xsonnet] | model=sonnet mode=agent probe_first=False verifier=None confidence=0.85 |

## Arm A Routing Decisions

### Task 1

- Model: sonnet
- Mode: agent
- Probe first: False
- Verifier: None
- Confidence: 0.86
- Reason: Small, well-specified ticket in a 139-line repo: an autouse conftest fixture builds a temp DB from the migration and redirects the app's DB path. The never-touch-real-shop.db, order-independent isolation, and unchanged-default rules are beyond haiku but within sonnet. The test suite self-verifies, so no probe, fan-out, or verifier.


### Task 2

- Model: opus
- Mode: agent
- Probe first: False
- Verifier: sonnet
- Confidence: 0.84
- Reason: Tiny repo (139 lines) and a fully specified ticket, so no probe or fan-out. The risk is the strict 401>403>404>422>409 ordering: FastAPI validates bodies and path params before handler code runs, so auth and ownership must pre-empt 422. Token parsing edge cases add risk. Opus handles these; a cheap sonnet verifier checks the rules.


### Task 3

- Model: sonnet
- Mode: agent
- Probe first: False
- Verifier: None
- Confidence: 0.85
- Reason: Single endpoint in a 139-line repo with exact rules and check order, so no probe and no fan-out. The existing tests fail because there is no test database, so the agent must check its work against a scratch DB and leave GET /billing unchanged. Sonnet handles that reliably; haiku risks the ordering and ownership-404 rules.


### Task 4

- Model: sonnet
- Mode: agent
- Probe first: False
- Verifier: None
- Confidence: 0.85
- Reason: Small, well-specified ticket on a 139-line app: one agent, no fan-out or probe. Haiku is likely to miss the subtle parts: 404 before 422 before 409 ordering, checkout leaving the cart and payments untouched on failure, and sweeping every endpoint for 200-with-error bodies without usable tests. Sonnet handles these; a verifier isn't worth the cost.


## Cost Breakdown

### Arm A by model

| Model | Input | Output | Cache Read | Cache Write 5m | Cache Write 1h | Total |
|---|---|---|---|---|---|---|
| claude-fable-5-1 | 8 ($0.0001) | 2,725 ($0.1362) | 58,185 ($0.0145) | 123,263 ($1.5408) | - | $1.6917 |
| claude-opus-5 | 46 ($0.0002) | 27,387 ($0.6847) | 1,513,345 ($0.7567) | 170,404 ($1.0650) | - | $2.5066 |
| claude-sonnet-5 | 154 ($0.0003) | 70,484 ($0.7048) | 4,845,764 ($0.9692) | 198,586 ($0.4965) | - | $2.1708 |

**Total for Arm A:** $6.3690

### Arm B by model

| Model | Input | Output | Cache Read | Cache Write 5m | Cache Write 1h | Total |
|---|---|---|---|---|---|---|
| claude-fable-5-1 | 134 ($0.0013) | 86,412 ($4.3206) | 4,119,461 ($1.0299) | 299,318 ($3.7415) | - | $9.0933 |

**Total for Arm B:** $9.0933

### Per role

| Arm | Role | Total |
|-----|------|-------|
| A | engineer | $4.2526 |
| A | router | $1.6917 |
| A | verifier | $0.4248 |
| B | engineer | $9.0933 |

### Pricing Source

- **anthropic:** https://platform.claude.com/docs/en/about-claude/pricing (retrieved 2026-09-19)
- **typesafe:** https://typesafe.ai (retrieved 2026-09-19)

## Blind Review

### De-anonymised

| Task | X is | Y is | Better | A correctness | A scope | A quality | B correctness | B scope | B quality |
|------|------|------|--------|----------------|---------|-----------|----------------|---------|-----------|
| 1 | B | A | B | 5 | 3 | 4 | 5 | 5 | 5 |
| 2 | A | B | tie | 5 | 4 | 4 | 5 | 4 | 4 |
| 3 | A | B | B | 3 | 5 | 3 | 5 | 4 | 5 |
| 4 | B | A | tie | 5 | 5 | 5 | 5 | 5 | 5 |

### Reviewer output (verbatim)

# Ticket-by-ticket review

| ticket | better | X correctness | X scope | X quality | Y correctness | Y scope | Y quality |
|---|---|---|---|---|---|---|---|
| task1 | X | 5 | 5 | 5 | 5 | 3 | 4 |
| task2 | tie | 5 | 4 | 4 | 5 | 4 | 4 |
| task3 | Y | 3 | 5 | 3 | 5 | 4 | 5 |
| task4 | tie | 5 | 5 | 5 | 5 | 5 | 5 |

**task1 X:** none found — closing the import-time connection inside a scratch dir (task1_X.diff:43‑54) means real `shop.db` is never even opened; only soft risk is relying on this conftest being the first thing to import `app.core.db`.
**task1 Y:** rewrites `app/core/db.py` into a lazy, env-var-driven connection cache purely to make tests overridable (task1_Y.diff:8‑38) — this is production-code restructuring the ticket says to avoid, and it changes when/how the real connection is established outside tests too.

**task2 X:** `_known_user` (403+404 wrapper) is duplicated near-verbatim in `admin.py` and `orders.py` instead of shared (task2_X.diff:71‑76, 142‑147); no behavioral bug found.
**task2 Y:** `auth.py` now imports from `app.core.users` (task2_Y.diff:10) to support `known_user()`, a new coupling the ticket didn't ask for; no behavioral bug found.

**task3 X:** the malformed-token check `_caller(token)` runs as body code (task3_X.diff:33‑34), after FastAPI has already parsed `payment_id`/`user_id`; a request like `payment_id=abc` with `Authorization: Bearer garbage` gets 422 instead of the required 401.
**task3 Y:** none found — `require_identity`/`require_self_or_admin` are real dependencies so 401/403 win over 422 in that same case, the existence+ownership check is one query (task3_Y.diff:53‑55) so it can't leak which user owns a payment, and a lock (task3_Y.diff:42,53) plus a large test suite (`tests/test_refund_endpoint.py`) cover the concurrent-double-refund case.

**task4 X:** none found.
**task4 Y:** none found.

**Verdicts:** task1 — X gets isolation entirely from the test side without touching `app/core/db.py`, matching "don't change app behavior outside tests" more literally than Y's production rewrite. task2 — both fully implement all eight rules with the same 401→403→404 ordering and comparable scope; differences are purely stylistic. task3 — Y's dependency-based auth check preserves the required 401-before-422 ordering and adds race-safety that X's inline check lacks. task4 — the two patches are essentially line-for-line equivalent, differing only in error-message wording.

## Threats to Validity

### Pre-registered (from PLAYBOOK.md)

- Four tasks, one run per arm per task: differences are indicative, not statistically significant.
- The fixture is 139 lines, so scope (S) barely varies across tasks.
- Agents have filesystem access and could in principle find the hidden tests even though run copies live outside the repo and briefs never mention them.
- Arm B's model is whatever the CTO session happens to run on, not a fixed reference model.

### Observed in this run

See Operator Notes below.

## Operator Notes

### What Phase 2 changed

Same four tickets, fixture, hidden tests, scorer, and task-agent prompt as Phase 1 (run r1).
The router was swapped: instead of Jev plus the `complexity` skill's policy, a fresh Claude
Fable 5.1 instance per ticket chose the delegation unaided. It saw the ticket, the same
one-line repository facts Jev saw, a menu (model, one agent or fan-out, optional probe,
optional verifier), and one objective: "Choose the cheapest option you are confident will
complete the ticket correctly." No rubric, no thresholds, no prices, no tools.

The baseline arm is Phase 1's all-Fable Arm B, imported byte for byte ($9.09, 51/51 on the
corrected oracle). It was not re-run.

### What the routers chose, and what was dispatched

| Task | Claude router (r2) | Confidence | Dispatched | Jev in r1 (as dispatched) |
|---|---|---|---|---|
| 1 Test DB fixture | sonnet, one agent | 0.86 | 1 agent, Sonnet 5 | Sonnet |
| 2 Auth and ownership | opus, one agent, verifier sonnet | 0.84 | 1 agent, Opus 5, then a Sonnet verifier and one fix round | probe, then Sonnet (fan-out collapsed) |
| 3 Refund logic | sonnet, one agent | 0.85 | 1 agent, Sonnet 5 | probe, then Opus (fan-out collapsed, K=3 gate) |
| 4 Error status codes | sonnet, one agent | 0.85 | 1 agent, Sonnet 5 | Sonnet (fan-out collapsed) |

No router asked for a fan-out or a probe. All four ruled out Haiku, each naming the specific
subtlety it expected Haiku to miss. No deviations from the pre-registered dispatch rules were
needed: every decision was directly executable.

### The verifier on task 2

The router asked for a Sonnet verifier on task 2 only. The verifier saw the ticket and the
diff, nothing else, and returned `fail`. Per the pre-registered rule its findings went to the
task agent verbatim, once, without operator comment.

- Findings 1 and 2 (headline): **false.** It claimed the untouched refund endpoint "almost
  certainly" still depended on `require_auth`, so swapping that import would raise a
  `NameError` and take the app down. The pristine refund endpoint has no auth dependency at
  all (that is the bug task 3 exists to fix). The verifier guessed at code it could not see.
- Finding 3: **true, and fixed.** `create_order` and `set_tier` raised 404 from the function
  body, so FastAPI's own 422 could pre-empt it. The task agent moved the unknown-user check
  into a dependency. The hidden tests do not exercise that combination, so this did not
  change the score.
- Cost of that insurance: verifier $0.42, plus the fix round inside the Opus agent's $2.51.

### Cost, three ways

| | Jev-routed (r1) | Claude-routed (r2) | All-Fable baseline |
|---|---|---|---|
| Hidden tests (corrected oracle) | 51/51 | 51/51 | 51/51 |
| Task agents | $3.30 | $4.25 | $9.09 |
| Routing overhead | $0.17 (two Haiku probes; Jev itself $0.0005) | $2.12 (routers $1.69, verifier $0.42) | none |
| Total | $3.47 | $6.37 | $9.09 |
| Fraction of baseline | 0.382 | 0.700 | 1.000 |
| Total with routers at marginal cost | n/a | $4.81 (0.529) | n/a |

The router's full cost is almost all start-up: each fresh Fable instance wrote 26K to 45K
tokens of system context to cache to produce roughly 500 tokens of answer. Counting only the
router's own uncached input and output, the four decisions cost $0.14. A session that already
has its context loaded and decides for itself would pay something near the marginal figure;
a system that spawns a fresh model per decision pays the full one. Both are reported because
which one applies depends on the deployment, not on the router's judgment.

### Blind review (de-anonymised) and what was verified

Same reviewer prompt and model (Sonnet 5) as Phase 1; the reviewer saw only tickets and
anonymous X/Y diffs in a folder outside the run directory.

| Task | Better | Routed arm (A) | Baseline (B) |
|---|---|---|---|
| 1 | B | Sonnet: lazy connection plus a `SHOP_DB_PATH` override in `app/core/db.py` | Fable: test-side only, `app/` untouched |
| 2 | tie | Opus plus verifier | Fable |
| 3 | B | Sonnet | Fable |
| 4 | tie | Sonnet | Fable |

Verified by the operator on throwaway clones:
- **Task 3, confirmed.** The routed arm's Sonnet refund handler checks the token's shape in the
  function body, after FastAPI has validated the query parameters. With a non-integer
  `payment_id`: a malformed token returns 422 (contract: 401) and another user's token returns
  422 (contract: 403). An oversized `payment_id` returns 500 (contract: 404). The baseline
  returns 401, 403, and 404. The hidden tests do not combine a bad token with an invalid
  parameter, so both arms scored 11/11. For comparison, Phase 1's Opus refund solution got the
  401-before-422 ordering right and failed only the oversized-id case.
- **Reviewer preferences are noisy.** On task 1 this reviewer preferred the baseline's
  test-side-only design; Phase 1's reviewer, judging the identical baseline solution against
  a similar lazy-connection design, preferred the lazy-connection one. Scores are opinion.

### Deviations from the pre-registered runbook

1. **Baseline reused, not re-run** (board decision, preliminary numbers). Phase 1's baseline
   agents ran with the `complexity` skill available and the real CLAUDE.md; Phase 2's task
   agents ran without the skill and with a stub CLAUDE.md. One Phase 1 baseline agent invoked
   the skill on itself.
2. **Router replies were taken from the router transcripts by script**, not retyped, then
   validated by `route_arm_c.py --record`. All four validated first time; no re-ask was needed.
3. **Reviewer cost** is experiment overhead and is not charged to either arm.

### Observed in this run

- **Run window:** 00:56:47Z to 01:09:43Z. For that window only, the `.claude/skills/complexity`
  symlink was removed (verified beforehand to hide the skill from newly spawned agents) and
  CLAUDE.md was a two-line neutral stub. Both were restored byte for byte and never committed.
  Only run agents (routers, task agents, the verifier) were spawned inside it.
- **Audit: clean.** Nine transcripts. Routers: zero tool calls each. Zero skill calls by any
  agent. No access to the hidden tests, reference patches, the repository, the run's JSON
  artefacts, Phase 1's data, the memory directory, or another task's copy.
- Task agents still learn the repository's name from their starting directory (one mentioned
  it), and one wrote that a change would be "easy to add if the grader wants it": it suspected
  grading, without knowing how. Neither looked anywhere it should not.
- The Sonnet task agents cost about the same in both phases ($1.75 across tasks 1, 3, 4 here
  against $2.02 for the three Sonnet tasks in r1), which is a rough indication of run-to-run
  noise at this scale: around 15%.

### Interpretation (operator's, preliminary)

1. **Both routers matched the baseline on everything the tests measure.** The difference is
   cost: Jev-routed 38% of baseline, Claude-routed 70% (53% at marginal router cost).
2. **Claude's unaided judgment was good, and more expensive.** It made no spurious fan-out or
   probe requests, which is exactly where Jev's rubric misfired in Phase 1. But it bought
   insurance the tests did not require: Opus plus a verifier on the task it judged hardest.
   Jev's arm did that task on Sonnet for $0.80 and passed the same 17 hidden tests.
3. **The two routers disagree about what makes a task risky, and each was right once.** Jev
   escalated the refund task (consequence: money). Claude escalated the auth task
   (implementation difficulty: making 401 and 403 pre-empt FastAPI's automatic 422 across
   many endpoints) and left refund on Sonnet. Outcome: Claude's Opus auth solution drew a
   tie with Fable in blind review, where Phase 1's Sonnet auth solution lost. But Claude's
   Sonnet refund solution has verified contract violations (below) that Phase 1's Opus
   refund solution did not have. Neither router's notion of risk dominates; one run per arm,
   so this is suggestive only.
4. **A verifier that sees only a diff guesses about the rest of the file.** Its headline
   finding was wrong for that reason; its minor finding was right. Give a verifier the touched
   files, not just the diff, or expect false alarms that cost a fix round.
5. **Hidden tests under-measure the contract's ordering rule.** Both phases found real
   ordering and robustness defects that no hidden test exercises (bad token combined with an
   invalid parameter; oversized ids). Cheaper tiers produced them more often than Fable did.
   If that behaviour matters, the oracle needs those cases before the next comparison.
6. **Jev's structural advantages are price and policy.** The routing call is effectively free
   ($0.0005 for six calls against $1.69 for four fresh Fable routers), and the fixed policy
   does not add a verifier to a testable, medium-risk task.
7. **What this does not show.** Same limits as Phase 1: four small tasks, one run per arm, one
   139-line app, implementation work only, no variance estimate, and the baseline was reused.

### Suggested next steps

- Phase 3 (board direction): Jev routing across several model providers (GPT and Gemini as
  well as Claude). Needs a harness that can run non-Claude coding agents on the same tickets
  and cost them from their own usage records.
- Carry over from Phase 1: reword the P rubric, decide how I is set on the Jev path, add a
  larger fixture, and rerun for variance.

## Reproduce

```bash
# Create the run
python3 experiments/ab-routing/make_run.py --run r2 --runs-root ~/.cache/shopapi-dev/work

# Dispatch Arm B then Arm A per README.md's order of operations, fill manifest.json

# Score the run
python3 experiments/ab-routing/score.py --run r2 --runs-root ~/.cache/shopapi-dev/work

# Account for costs
python3 experiments/ab-routing/account.py --run r2 --runs-root ~/.cache/shopapi-dev/work

# Generate blind diffs
python3 experiments/ab-routing/blind_pack.py --run r2 --runs-root ~/.cache/shopapi-dev/work

# Generate this report
python3 experiments/ab-routing/report.py --run r2 --runs-root ~/.cache/shopapi-dev/work --review ~/.cache/shopapi-dev/work/r2/review.md --notes experiments/ab-routing/reports/r2-notes.md --compare-run r1
```
