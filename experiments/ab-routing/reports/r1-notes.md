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
