# A/B Routing Experiment Report

## Headline

- **As scored:** A passed 51/51, B passed 49/51. Pass bar (of what B passed, how much did A also pass): 49/49 = 1.000 -> **MEETS** the bar.
- **Corrected:** A passed 51/51, B passed 51/51. Pass bar (of what B passed, how much did A also pass): 51/51 = 1.000 -> **MEETS** the bar.

Threshold: 90%.

**Cost:** Arm A $3.47, Arm B $9.09. Ratio A/B: 0.382.

## Per-Task Results

| Task | A passed/total | B passed/total | A target | B target | A guard | B guard | Cost A | Cost B | Ratio A/B |
|------|-----------------|-----------------|----------|----------|---------|---------|--------|--------|-----------|
| 1 | 10/10 | 10/10 | 8/8 | 8/8 | 2/2 | 2/2 | $0.69 | $2.26 | 0.305 |
| 2 | 17/17 | 17/17 | 10/10 | 10/10 | 7/7 | 7/7 | $0.80 | $3.14 | 0.254 |
| 3 | 11/11 | 11/11 | 8/8 | 8/8 | 3/3 | 3/3 | $1.35 | $2.37 | 0.571 |
| 4 | 13/13 | 13/13 | 10/10 | 10/10 | 3/3 | 3/3 | $0.63 | $1.32 | 0.478 |
| **Total** | 51/51 | 51/51 | 36/36 | 36/36 | 15/15 | 15/15 | $3.47 | $9.09 | 0.382 |

## Arm A Routing Decisions

### Task 1

Scores: scores (jev)  S=2,R=2,A=1,K=1?,I=1?,P=0?,V=1  kind=implement

- Decision: [c-85de41 · S2R2A1K1?I1?P0?V1 · agent sonnet]


### Task 2

Scores: scores (jev)  S=2,R=2,A=1,K=2,I=1?,P=2?,V=1  kind=implement

- Decision: [c-cfe318 · S2R2A1K2I1?P2?V1 · probe->reroute]

**Call type:** after_probe

Scores: scores (jev)  S=2,R=2,A=1,K=2,I=1,P=2,V=1  kind=implement

- Decision: [c-bfda87 · S2R2A1K2I1P2V1 · fan-out 6xsonnet]


### Task 3

Scores: scores (jev)  S=1?,R=2,A=1,K=3?,I=2?,P=0,V=1  kind=implement

- Decision: [c-060d9c · S1?R2A1K3?I2?P0V1 · probe->reroute · gate:confirm]

**Call type:** after_probe

Scores: scores (jev)  S=1,R=2,A=1,K=3,I=2,P=1?,V=1  kind=implement

- Decision: [c-313b57 · S1R2A1K3I2P1V1 · fan-out 3xopus · gate:confirm]


### Task 4

Scores: scores (jev)  S=2,R=2,A=1,K=2,I=1?,P=2,V=1  kind=implement

- Decision: [c-2f44d9 · S2R2A1K2I1?P2V1 · fan-out 6xsonnet]


## Cost Breakdown

### Arm A by model

| Model | Input | Output | Cache Read | Cache Write 5m | Cache Write 1h | Total |
|---|---|---|---|---|---|---|
| claude-haiku-4-5-20251001 | 196 ($0.0002) | 4,870 ($0.0244) | 880,676 ($0.0881) | 46,884 ($0.0586) | - | $0.1712 |
| claude-opus-5 | 32 ($0.0002) | 14,598 ($0.3649) | 918,458 ($0.4592) | 72,313 ($0.4520) | - | $1.2763 |
| claude-sonnet-5 | 160 ($0.0003) | 45,643 ($0.4564) | 5,175,795 ($1.0352) | 213,609 ($0.5340) | - | $2.0259 |
| jev-latest | 11,736 ($0.0005) | 864 ($0.0000) | - | - | - | $0.0005 |

**Total for Arm A:** $3.4739

### Arm B by model

| Model | Input | Output | Cache Read | Cache Write 5m | Cache Write 1h | Total |
|---|---|---|---|---|---|---|
| claude-fable-5-1 | 134 ($0.0013) | 86,412 ($4.3206) | 4,119,461 ($1.0299) | 299,318 ($3.7415) | - | $9.0933 |

**Total for Arm B:** $9.0933

### Per role

| Arm | Role | Total |
|-----|------|-------|
| A | engineer | $3.3022 |
| A | probe | $0.1712 |
| A | router | $0.0005 |
| B | engineer | $9.0933 |

### Pricing Source

- **anthropic:** https://platform.claude.com/docs/en/about-claude/pricing (retrieved 2026-09-19)
- **typesafe:** https://typesafe.ai (retrieved 2026-09-19)

## Blind Review

### De-anonymised

| Task | X is | Y is | Better | A correctness | A scope | A quality | B correctness | B scope | B quality |
|------|------|------|--------|----------------|---------|-----------|----------------|---------|-----------|
| 1 | A | B | A | 5 | 4 | 5 | 4 | 3 | 3 |
| 2 | B | A | B | 3 | 5 | 4 | 5 | 3 | 4 |
| 3 | B | A | B | 3 | 5 | 3 | 5 | 4 | 5 |
| 4 | B | A | tie | 5 | 4 | 4 | 5 | 5 | 4 |

### Reviewer output (verbatim)

| ticket | better (X/Y/tie) | X correctness | X scope | X quality | Y correctness | Y scope | Y quality |
|---|---|---|---|---|---|---|---|
| task1 | X | 5 | 4 | 5 | 4 | 3 | 3 |
| task2 | X | 5 | 3 | 4 | 3 | 5 | 4 |
| task3 | X | 5 | 4 | 5 | 3 | 5 | 3 |
| task4 | tie | 5 | 5 | 4 | 5 | 4 | 4 |

task1 X: none found. Makes app/core/db.py connect lazily so shop.db is never touched until a real request; the autouse fixture then swaps it in per-test.
task1 Y: the isolation trick assumes its own _import_db_outside_project() is the first import of app.core.db in the session; if any earlier import happens it silently loses that guarantee and could touch the real project shop.db.
task2 X: known_user() raises 404 for an unknown user in admin.set_tier and orders.create_order; that status-code fix is ticket4's job, not one of ticket2's 8 rules, so it overreaches scope.
task2 Y: _caller_user_id uses suffix.isdigit() then int(suffix) with no ASCII guard; a token like "Bearer user-²" passes .isdigit() but crashes int(), giving an unhandled 500 instead of the required 401 (rule 1).
task3 X: none found. An OverflowError guard and _refund_lock keep oversized ids and concurrent refunds from breaking rule 3/4.
task3 Y: refund's query binds payment_id/user_id straight into sqlite with no overflow guard, so an oversized id (e.g. 2**70) raises an unhandled 500 instead of the required 404; there is also no lock, so two concurrent refunds of the same payment can both read "not yet refunded" and both return 200.
task4 X: none found.
task4 Y: checkout.py drops the pre-existing u = get_user(user_id) binding for an inline call; likely harmless, but an edit the ticket did not need.

Verdicts: task1: X fixes the root cause with a 2-line lazy-connect change plus a minimal fixture; Y's import-order trick is fragile and adds unrequested files. task2: X orders 401/403 ahead of FastAPI's automatic 422 and validates ids with a strict regex; Y's isdigit() parser can crash instead of returning 401, though X does stray into ticket4's territory. task3: X protects against oversized ids and concurrent double-refunds; Y can 500 on an oversized id and has no race protection. task4: functionally identical 404/422/409 logic in the same order; Y just makes one small, unnecessary edit.

## Threats to Validity

### Pre-registered (from PLAYBOOK.md)

- Four tasks, one run per arm per task: differences are indicative, not statistically significant.
- The fixture is 139 lines, so scope (S) barely varies across tasks.
- Agents have filesystem access and could in principle find the hidden tests even though run copies live outside the repo and briefs never mention them.
- Arm B's model is whatever the CTO session happens to run on, not a fixed reference model.

### Observed in this run

See Operator Notes below.

## Operator Notes

### What was dispatched (Arm A)

| Task | First decision | After probe | Dispatched | Deviation |
|---|---|---|---|---|
| 1 Test DB fixture | `agent sonnet` | n/a | 1 agent, Sonnet 5 | none |
| 2 Auth and ownership | `probe->reroute` | `fan-out 6xsonnet` | Haiku probe, then 1 agent, Sonnet 5 | fan-out collapsed to one agent at the task tier |
| 3 Refund logic | `probe->reroute · gate:confirm` | `fan-out 3xopus · gate:confirm` | Haiku probe, then 1 agent, Opus 5 | fan-out collapsed; K=3 gate auto-approved |
| 4 Error status codes | `fan-out 6xsonnet` | n/a | 1 agent, Sonnet 5 | fan-out collapsed |

Arm B: four agents on Claude Fable 5.1, one per task. Every task agent in both arms was a
`general-purpose` subagent given a one-line pointer to a ticket file holding the brief verbatim.

### Deviations from the pre-registered runbook

1. **Fan-out never executed (3 of 4 tasks).** Pre-registered: a fan-out decision runs as one
   agent at the task tier, because slicing needs operator judgment. Consequence: this run
   tests Jev's choice of model tier, not its fan-out judgment.
2. **Run location.** Copies lived under `~/.cache/shopapi-dev/work` rather than the default
   `~/.cache/ab-routing/runs`, so the path did not hint at an experiment. Decided before any
   agent started.
3. **Prompt form.** The prompt was a pointer to the ticket file, not the pasted brief, to rule
   out transcription differences between arms. Jev scored the same ticket text. Decided
   before any agent started.
4. **Probe prompt.** The skill's probe template plus one clarifying sentence (do not run the
   test suite), to keep Arm A copies pristine. Both copies were verified pristine after the probes.
5. **Hidden-test correction after the run (the only post-hoc change).** Two task 1 tests
   asserted that exactly four tests run. Arm B's solution added four tests of its own; all
   eight passed, and the ticket does not forbid adding tests. That assertion broke the
   harness's own rule that a hidden test checks only what the brief states. Fixed in
   `793d65f` with a regression check. Both result sets are reported. The pass-bar verdict is
   identical under either.
6. **Review prompt replaced by hand.** The generated prompt told the reviewer not to mention
   "arms, models, routing, cost, or the experiment", which discloses all of them. The operator
   wrote a neutral prompt and gave the reviewer a folder outside the run directory.
7. **Reviewer cost** ($0.81, Sonnet 5) is experiment overhead and is not charged to either arm.

### Observed in this run

- **Leak audit: clean.** Every tool call in all ten task and probe transcripts was scanned for
  the hidden tests, reference patches, the operator's spec file, the repository, the run's
  JSON artefacts, and the other arm's copies. No hits. The task agents could see the operator
  session's scratchpad folder, which held the hidden-test spec; none listed or read it.
- **The repository's own complexity skill is active for every subagent.** At least one Arm B
  agent invoked it on its own task. Its tokens are inside that agent's bill. Applies to both arms.
- **Subagents inherit the repository's CLAUDE.md context**, which does not mention the
  experiment (the harness lives on an unmerged branch).
- **Sonnet agents read more cached tokens than Fable agents** (1.6M to 2.0M versus 0.6M to
  1.3M per task) because they took more turns, and were still 2x to 4x cheaper per task.
- **Verified reviewer claims.** Task 3: confirmed. Arm A's (Opus) refund handler returns 500
  for `payment_id=2**70`; Arm B's (Fable) returns 404. Task 2: not reproduced. The reviewer
  said Arm A's (Sonnet) token parser would crash with a 500 on `user-²`; it returns 401. The
  real difference found is that Arm A accepts `user-01` as user 1 and Arm B rejects it; the
  ticket does not settle which is right. One of the reviewer's two checkable defect claims
  was wrong, so treat its scores as opinion, not measurement.

### Interpretation (operator's, preliminary)

1. **On these tasks, routing matched the baseline on the measured outcome at 38% of the
   cost.** Both arms passed all 51 hidden tests (corrected). Arm A cost $3.47 against $9.09.
   The saving came from tier choice: three tasks to Sonnet, one to Opus, none to Fable.
2. **The baseline bought robustness the tests do not measure.** The blind reviewer preferred
   Arm B on two tasks, Arm A on one, with one tie. The confirmed instance is the refund task:
   Fable guarded against oversized ids and double refunds; Opus did not, and returns a 500 on
   an oversized id. Whether that is worth 1.8x on that task ($2.37 against $1.35) is a product
   call. On task 1 the cheaper arm produced the design the reviewer preferred.
3. **Jev's tier choices were sensible and differentiated by risk.** It gave the refund task
   K=3 (money) and the most expensive tier it used, the test-fixture task K=1. It never chose
   Haiku: reasoning scored R=2 on every task with 0.70 to 0.94 confidence.
4. **Jev over-scores parallelism on small repositories.** It asked for six shards twice and
   three once, on a 139-line app. It reads P from the ticket's structure (a list of endpoints
   or rules), not from the amount of work. The P rubric wording should require that each
   shard be worth a separate agent. This is the clearest calibration finding.
5. **Independence (I) is unanswerable from a brief.** Jev's I confidence was 0.52 to 0.58 on
   every first call. The rubric asks whether the task depends on the surrounding conversation,
   which a scorer that sees only the brief cannot know. Either pass that as context or drop I
   from the Jev path and let the caller set it.
6. **Probes did their job cheaply.** Two Haiku probes (about $0.08 each) moved scope
   confidence from 0.49 to 0.94 on task 3 and parallelism confidence from 0.47 to 0.97 on
   task 2. Jev's own six calls cost $0.0005 in total.
7. **What this does not show.** Four tasks, one run per arm, one small app, implementation
   work only. No variance estimate: rerunning the same arm could move test counts and cost by
   more than some of the differences seen. Nothing here tests explore, review, plan, or a real
   fan-out. Known Issue 8 (fan-out path untested) stays open.

### Suggested next steps

- Rerun both arms two more times for a variance estimate (about $13 per full run).
- Reword the P rubric level descriptions; re-score these four tickets and check the fan-out calls disappear.
- Add a larger fixture (40+ handlers) so scope and parallelism genuinely vary, then let fan-outs execute.
- Add hidden robustness tests (oversized ids, concurrent refunds) if that behaviour is wanted; today neither the ticket nor the tests ask for it.

## Reproduce

```bash
# Create the run
python3 experiments/ab-routing/make_run.py --run r1 --runs-root ~/.cache/shopapi-dev/work

# Dispatch Arm B then Arm A per README.md's order of operations, fill manifest.json

# Score the run
python3 experiments/ab-routing/score.py --run r1 --runs-root ~/.cache/shopapi-dev/work

# Account for costs
python3 experiments/ab-routing/account.py --run r1 --runs-root ~/.cache/shopapi-dev/work

# Generate blind diffs
python3 experiments/ab-routing/blind_pack.py --run r1 --runs-root ~/.cache/shopapi-dev/work

# Generate this report
python3 experiments/ab-routing/report.py --run r1 --runs-root ~/.cache/shopapi-dev/work --review ~/.cache/shopapi-dev/work/r1/review.md --as-scored ~/.cache/shopapi-dev/work/r1/results.as_scored.json --notes experiments/ab-routing/reports/r1-notes.md
```
