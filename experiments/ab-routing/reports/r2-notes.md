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
