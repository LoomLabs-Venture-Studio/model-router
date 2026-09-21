# Routing policy: the exact rules and why

`scripts/route.py` implements this. Read this when you need to apply the policy by hand (no Bash), when a decision looks wrong and you want to see which rule fired, or when tuning thresholds. All numbers live in the `CONFIG` block at the top of the script.

## Inputs

Seven scores 0 to 3 (S R A K I P V), each optionally marked `?` (unsure), plus `kind` in {explore, plan, implement, review, answer}, an optional shard count, and an optional user override.

## Step 1: resolve unsure scores

| Unsure on | Treatment | Why |
|---|---|---|
| R, A, K, V | Round up by one (cap 3) | Underestimating difficulty or risk costs a retry or an incident; overestimating costs a few cents |
| S | Probe before routing | Scope is the one thing a cheap read-only agent can measure exactly |
| P (2 or 3) | Probe before routing | Never fan out on a guess; the probe counts the shards. An unsure P of 0 or 1 is taken as given |
| I | Treat as 1 | Assume the brief needs care |

## Step 2: difficulty and tier

```
difficulty = R + A + K            # 0..9
if S == 3: difficulty += 1        # coherence across a large surface is its own difficulty
difficulty = min(difficulty, 9)

tier: 0-2 -> haiku | 3-5 -> sonnet | 6-7 -> opus | 8-9 -> fable (fallback opus)
```

Modifiers, applied in this order:

1. **Test-loop discount.** If V <= 1 and K <= 1 and R <= 1: tier down one. A cheap model iterating against a checker is the right economics when the work is mechanical and the checker already exists. The R guard matters: tests catch a wrong answer but do not help a model that cannot find a right one.
2. **Risk floor.** If K >= 2: tier at least sonnet. If K == 3: tier at least opus. Haiku on anything that touches auth, data, or money is a false economy.
3. **Gate.** If K == 3: `gate = confirm before irreversible step`. If also V == 3: `gate = human sign-off on the result`. Gates do not depend on tier; a stronger model does not make an irreversible action reversible.
4. **Verifier.** If (K >= 2 and V >= 2) or V == 3: add an independent verifier at max(tier, sonnet). If K == 3 and V >= 2: verifier at max(tier, opus). "Independent" means the verifier receives the artifact (diff, report) and the original task, not the reasoning that produced it.
5. **Effort (advisory).** difficulty 9 -> max; tier opus or fable -> high; otherwise default.

## Step 3: mode

Evaluated top to bottom; first match wins.

| Rule | Mode | Why |
|---|---|---|
| kind == answer | inline | Nothing to delegate |
| I == 0 | inline; probe first if S >= 2 | Cannot be briefed. Delegate the reading, keep the deciding |
| S is unsure, or P is unsure at 2+, or (A >= 2 and S >= 2) | probe first, then re-route | Scope, shard count, and the shape of the ambiguity are exactly what a probe measures |
| P >= 2 | fan-out | Independent shards; parallel wall-clock and isolated contexts |
| P == 1 | fan-out (2 or 3 agents at task tier) | Small fan-out, same tier, one message |
| S >= 2, or I == 3, or kind == explore | single agent | Keeps the reading out of the main context |
| otherwise | inline | Small, sequential, and the main thread already has the context |

"Inline" assumes the session model is at least the tier. If it is not (a sonnet session facing an opus-tier task), use a single agent at the tier instead; the card says so.

The single-agent rule exists to protect the main context. It only pays off when there is real reading left to do: score S by remaining volume (see the rubric), so a small repo or material you have already read routes inline even if it spans several files.

### Fan-out details

- `shards`: pass your count with `--shards N` (you usually know it after a probe); without it the router assumes 3 for P1, 6 for P2, 8 for P3. P1 is capped at 3 (it is a two-or-three-agent fan-out, not a wider one). Above `parallel_cap` (8), the router keeps the full count and reports concurrency separately rather than dropping shards, e.g. `fan-out 12xsonnet (8 at a time)`; run the rest as a second batch.
- `shard_model`: for P>=2, one tier below the task tier, because each shard is narrower than the whole; floored at sonnet when K >= 2 (a cheap model missing an auth gap in one shard is the same incident as missing it everywhere). P1's two-or-three-agent fan-out is not a narrower slice of the same task, so it stays at the task tier — no discount.
- `reduce`: inline when the merge is mechanical (concatenate tables, dedupe); one agent at the task tier when the merge needs judgment (synthesize findings, resolve conflicts).
- Launch all shards in one message. Use background mode for shards whose output is not needed to continue.

### Probe details

A probe is an `Explore` agent (read-only) on haiku, or sonnet if the repo is unfamiliar or large, with a brief that asks for facts only: which files or modules are involved and how many; whether an existing pattern or example exists; whether tests cover the area; what changed recently; anything that makes the task bigger than it sounds. It returns a short list, not a plan. Re-score S, P, and often R and V from what it found, then route again. Cost: one cheap agent, usually under a minute. It prevents both under-delegation (main thread reads forty files) and over-delegation (eight shards for a two-file change).

## Step 4: overrides

A user override always wins. Re-run the router with `--override "inline"` (or `"opus"`, `"no verifier"`, whatever they said) so the decision is logged with both the policy's answer and the human's. Divergence between the two is the most useful calibration data the log collects.

## Step 5: log and outcome

Every `route` call appends one JSON line to `$COMPLEXITY_LOG` (default `~/.claude/complexity-router/decisions.jsonl`) with the id, scores, unsure flags, difficulty, tier, mode, shards, probe, verifier, gate, override, and the project directory name. It also records `cwd`, the absolute, resolved working directory the decision was made in (used by the gate's no-id fallback to bind a decision to its own project); this is a full local path, so it includes the home directory path, and it stays in the local log like everything else here — it is never sent anywhere. `route.py outcome --id <id> --result ok|retry|escalated|failed --note "..."` appends the result. `route.py stats` aggregates: decisions per tier and mode, and retry or escalation rates per tier.

How to read stats:

- **Retries or escalations concentrated in one tier** mean that tier's difficulty band is too wide at the top. Move the boundary down by one (e.g. sonnet covers 3-4 instead of 3-5).
- **A tier with near-zero retries and many decisions** may be over-provisioned. Try widening the band below it.
- **Overrides that repeatedly pick a mode the policy did not** point at a mode rule, usually the single-agent threshold on S.
- Change one threshold at a time and give it a week of decisions.

## Worked calculations

| Task | Scores | difficulty | tier | mode | extras |
|---|---|---|---|---|---|
| Rename across a large repo | S2 R0 A0 K1 I2 P0 V1 | 1 | haiku | single | none (in a 150-line repo S is 1 and it routes inline) |
| Intermittent 500s on checkout | S2? R3 A2 K2 I1 P0 V2 | 7 | opus | probe, then inline or single | verifier opus, review gate |
| Audit all handlers for auth | S3 R1 A1 K2 I3 P3 V2 | 5 | sonnet | fan-out, shards at sonnet (K2 floor; count with `--shards`, default 8 for P3), reduce inline | verifier sonnet |
| Add pagination to one list endpoint | S1 R1 A1 K1 I2 P0 V1 | 3 | sonnet, test-loop discount -> haiku | inline | none |
| Rotate the prod DB credentials | S1 R1 A0 K3 I1 P0 V2 | 4 | sonnet, risk floor -> opus | inline | gate: confirm before irreversible step; verifier opus |
