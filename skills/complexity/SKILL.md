---
name: complexity
description: Measure a task's complexity on a 7-dimension rubric, then route it. Decides whether to do the work inline, hand it to one subagent, or fan out to several, and picks the model tier (haiku, sonnet, opus, fable) plus effort, verifier, and human gate for each. Use this before spawning any Agent or subagent, before starting any coding, research, or analysis task with more than one step, whenever you are choosing which model should handle a piece of work, and whenever the user asks how hard something is, whether to fan out, or which model to use. Also use it when the user says /complexity, "triage this", "size this", "route this", or "what model for this". Skip only for conversational questions and single-step edits.
allowed-tools:
  - Bash(${CLAUDE_SKILL_DIR}/scripts/route.py *)
  - Bash("${CLAUDE_SKILL_DIR}/scripts/route.py" *)
license: MIT
---

# Complexity: measure, then route

Most routing mistakes come from deciding the *how* before measuring the *what*. Reading forty files in the main thread because the task "seemed small", sending a subtle concurrency bug to a cheap model because it was one file, or fanning out an audit to eight haiku shards and getting eight confident holes in the report. This skill puts a measurement step in front of every non-trivial dispatch.

The split is deliberate: **you make the judgments, the policy makes the decision.** You score seven small, separable questions about the task (a gut check each, the kind a strong engineer answers in seconds). `scripts/route.py` turns those scores into a decision with fixed thresholds, logs it, and gives you an id you can attach an outcome to later. Thresholds live in one config block in the script, so tuning the policy is a code change, not a re-prompt.

## Quiet by default

Routing is overhead, and overhead paid in the main thread is paid in the most expensive currency there is: output tokens on the session model, plus the user's attention. A paragraph explaining why you chose sonnet can cost more than the haiku probe that saved the reading. So:

- **Never narrate the routing.** No "I'm sizing this first", no rubric walkthrough, no restating the card. The user asked for the work, not a seminar on how you chose to do it.
- **One line in the thread, the rest in the log.** `route.py route` prints a one-liner like `[c-695b05a1c9e4 · S3R1A1K2I3P2V2 · fan-out 7xsonnet · verify:sonnet]`. Let it stand as the tool result; do not repeat it in prose. The full card is in the log; `route.py show last` prints it when the user asks, and `--explain` prints it when they invoked `/complexity` themselves.
- **Briefs by reference.** Write a shard brief once to a file (`Write` costs its tokens once), then each Agent prompt is two lines: the file path and the slice. Seven copies of a brief is seven times the output tokens for no information.
- **Probes and verifiers return conclusions, not transcripts.** Cap their return shape in the brief (ten lines, a table, pass/fail plus three findings). Their output lands in your context and stays there.
- **Report the work, not the routing.** When you finish, the summary is about the task. The decision id can ride along in brackets; nothing else about routing belongs in the reply.

## Workflow

1. **Fast path.** If every dimension below is obviously 0 or 1 and there is nothing to parallelize, do the work. Do not tax one-line edits with a card or a comment.
2. **Score.** Rate the task 0 to 3 on each dimension in the rubric. Mark any score you are guessing at with `?` (for example `S=2?`). Unsure is a legitimate answer and the router treats it conservatively. Score scope from cheap signals (`git ls-files | wc -l`, a grep count, a directory listing, `wc -l` on the files involved), not by reading the material: the reading is the thing you are deciding whether to delegate. Material you have already read counts as zero remaining scope.
3. **Route.** One Bash call; it prints the one-liner and logs the decision:
   ```bash
   "${CLAUDE_SKILL_DIR}/scripts/route.py" route --scores "S=2,R=3?,A=2,K=2,I=1,P=0,V=2" --kind implement --task "intermittent 500s on checkout under load"
   ```
   If Bash is unavailable, apply the rules in `references/routing-policy.md` by hand and say so in one clause.
4. **Dispatch.** If the line says `probe->reroute`, run the probe, re-score with what it found, then route again with `--after-probe` so the router treats anything still unsure conservatively instead of probing a second time:
   ```bash
   "${CLAUDE_SKILL_DIR}/scripts/route.py" route --scores "S=2,R=3,A=2,K=2,I=1,P=0,V=2" --kind implement --after-probe --task "intermittent 500s on checkout under load"
   ```
   If the user overrides ("just do it here", "use opus"), do what they asked — their override wins in what you do. Re-run with `--override "..."` too, but know what it does and does not do: it does not change the policy's answer (the route line is the same either way); it only annotates the logged decision, and the one-liner, with `· override`, so the log records where your judgment and theirs diverged. That divergence is the calibration signal, not a vote the flag wins in the tool. Do not argue the override in the thread.
5. **Close the loop.** When the delegated work comes back, one call records how it went:
   ```bash
   "${CLAUDE_SKILL_DIR}/scripts/route.py" outcome --id c-3f9a2bd41c7a --result ok      # ok | retry | escalated | failed
   ```
   A decision can take more than one outcome over time (a retry, then an ok); `route.py stats` later shows, per tier, both the final result and how many decisions ever needed a retry or escalation along the way. That is how you find out whether a threshold is too low, and it is the only place routing gets discussed: in a review the user chooses to open, not in every task.

Invoked as `/complexity <task>`: the user asked for the analysis, so this is the one case where the full card belongs in the thread. Score and route `$ARGUMENTS` with `--explain`, then ask whether to proceed.

## Where the routing runs

Three placements, from least to most machinery. `references/placements.md` has the details and the config snippets.

1. **In-thread, quiet** (this skill as shipped): you score, one Bash call routes, one line lands in the thread. Cost per decision: a few dozen output tokens plus the scores you had to think about anyway.
2. **Forked scorer**: the scoring and probe run in a forked haiku subagent (`context: fork`) and return only the one-liner. The main thread spends nothing on rubric reasoning. Trade: the fork cannot see the conversation, so it scores independence (I) worse; the main thread passes a one-word hint.
3. **Hooks, out of band**: a `UserPromptSubmit` hook scores the prompt (heuristic, or Jev in under a second) and injects the one-liner as context before you see the task; a `PreToolUse` hook on the Agent tool is a guard-rail that catches an unrouted or under-tiered Agent call by default (deny haiku when risk is 2 or more, deny an Agent call with no routing id). Routing then costs zero output tokens without you having to remember to run it. It is a guard-rail the user can skip, not a security boundary — see `references/placements.md` for the skip paths. This is the placement for a governance harness.

## The rubric

Each dimension is one question. Score them independently; do not let a hard task pull every number up. Anchors for 0 and 3 are below; `references/rubric.md` has all four levels with examples. Read it the first time you use this skill in a session, not every time. Read `references/brief-template.md` only when you are about to delegate.

| Dim | Question | 0 | 3 |
|---|---|---|---|
| **S** Scope | How much remains to be read or touched? | One known location, no search | Unknown extent, or 20+ files or roughly 2,000+ lines across several subsystems |
| **R** Reasoning | How deep is the inference? | Mechanical; a pattern to copy exists | Novel design; subtle concurrency, security, perf, or data consistency; multi-hop causal debugging |
| **A** Ambiguity | How underspecified is it? | Fully specified, one right answer | The goal itself is unclear; needs discovery or product judgment |
| **K** Risk | What is the blast radius if wrong? | Local, reversible, no side effects | Irreversible or production-affecting: prod data, deploys, secrets, money, deletion, sending or publishing |
| **I** Independence | Can it run without this conversation? | Needs live back-and-forth or unstated preferences from this chat | Cleanly briefable and mostly reading; the main thread only needs the conclusion |
| **P** Parallelism | How many independent shards? | One sequential thread | Nine or more, or a map over files or records |
| **V** Verifiability | How will you know it is right? | Self-evident on inspection | No oracle; needs an independent reviewer, adversarial check, or human sign-off |

Plus one categorical, **kind**: `explore` (find or understand), `plan` (design an approach), `implement` (change things), `review` (assess and report), `answer` (respond from knowledge). Kind picks the subagent type; it is not a difficulty measure.

Three dimensions do most of the work and are the ones people misjudge:

- **S (scope) is volume, not file count.** Fifteen occurrences across seven files in a 150-line repo is S1: it fits in one screen and a subagent would cost more than the read. Scope is about how much context the main thread would burn.
- **K (risk) is about consequences, not effort.** A one-line change to a migration is K2 or K3. Score it by what happens if the change is wrong and nobody catches it.
- **I (independence) is a gate, not a preference.** A subagent starts with nothing but the brief. If the task depends on things said earlier in this conversation that you cannot write down in a paragraph, it is I=0 and it stays inline no matter how big it is (delegate the *reading* with a probe if scope is large; keep the *deciding* here).

## How the policy decides (summary)

`difficulty = R + A + K`, plus 1 when S=3 (keeping a large surface coherent is itself hard). Tier by difficulty: 0-2 haiku, 3-5 sonnet, 6-7 opus, 8-9 fable (falls back to opus if fable is not available on the account). Then:

- Tests exist (V<=1), low risk (K<=1), mechanical work (R<=1): drop one tier. A cheap model in a generate-test-iterate loop is the right tool when the checker is the hard part already solved.
- K>=2: never haiku, for shards either. K=3: at least opus, and a **gate**: confirm with the user before any irreversible step, whatever the tier.
- Verifier: added when K>=2 and V>=2, or whenever V=3. Independent means it gets the diff or the artifact, not your reasoning, so it is not anchored on the draft. V=3 with K=3 means human sign-off, not just another model.
- `?` on R, A, K, or V rounds up. `?` on S, or on P at 2 or above, triggers a **probe** (a read-only Explore agent on a cheap model that sizes the task and counts the shards) before routing. `?` on I is treated as 1.

Mode:

- I=0: inline (with a probe first if S>=2).
- P>=2: fan out. Shards get one tier below the task tier (they are narrower than the whole), floored at sonnet when K>=2; the reduce step stays inline or goes to one agent at the full tier. Cap at 8 concurrent shards; batch beyond that.
- P=1: two or three agents at the task tier, launched in one message.
- S>=2, or I=3, or kind=explore: one agent at the task tier, to keep the reading out of the main context.
- Otherwise inline. "Inline" assumes this session's model is at least the tier; if it is not, use one agent at the tier instead.

`references/routing-policy.md` has the exact rules, the reasoning behind each, and how to tune them.

## Dispatching in Claude Code

- The Agent tool takes a `model` parameter (`haiku`, `sonnet`, `opus`, `fable`); it overrides any subagent's own `model` frontmatter. Set it from the card every time. Do not rely on the default, which inherits the session model.
- Kind to subagent type: `explore` and probes use `Explore` (read-only, fast; say "medium" or "very thorough"); `plan` uses `Plan`; `implement` and `review` use `general-purpose` (tell a reviewer not to edit).
- Independent shards go in **one message with multiple Agent calls** so they run concurrently. Use background mode for shards whose results are not needed to continue. Write the shared brief to a file first and point each shard at it with its slice; do not paste the brief seven times.
- Effort (`low`, `medium`, `high`, `xhigh`, `max`) is set in a custom subagent's frontmatter, not per call. The card's effort is advisory unless you have a custom agent definition; opus and fable work defaults to high, difficulty 9 to max.
- Write the brief with `references/brief-template.md`. The brief is the only context the agent will have, and most delegation failures are brief failures, not model failures.

## What the user sees

By default, one line, as the tool result of the route call:

```
[c-695b05a1c9e4 · S3R1A1K2I3P2V2 · fan-out 7xsonnet · verify:sonnet]
```

That is enough to disagree with ("why sonnet?", "that's not K2"), and the answer to any of those questions is `route.py show c-695b05a1c9e4`, which prints the full card:

```
complexity  S3 R1 A1 K2 I3 P2 V2  kind=review  difficulty 5/9
route       fan-out · 7 shards · general-purpose (read-only brief) · shard model sonnet · reduce: inline if mechanical (concatenate, dedupe); one agent at task tier if it needs judgment · task tier sonnet
why         P2: independent shards
verifier    yes · sonnet, independent (give it the artifact and the task, not the reasoning)
gate        K2: changes ship through review, no direct deploy
id          c-695b05a1c9e4   log ~/.claude/complexity-router/decisions.jsonl
```

Show the full card only when asked, or when the user invoked `/complexity` themselves.

## Worked examples

**"Rename `getUser` to `fetchUser` everywhere."**
In a large repo: S2 R0 A0 K1 I2 P0 V1, implement. difficulty 1, haiku. Single agent (S2: the matches would otherwise fill the main context), run the tests, no verifier. Twenty seconds of haiku beats reading thirty files here. In a 150-line repo the same task is S1 and the router says inline, which is right: the brief would be longer than the job.

**"We're seeing intermittent 500s on checkout under load. Find the cause and fix it."**
S2? R3 A2 K2 I1 P0 V2, implement. difficulty 7, opus, verifier (K2 and V2), review gate. S is unsure, so: probe first (Explore, haiku: which services are on the checkout path, where the logs are, what changed recently, whether load tests exist), then re-score and route again with `--after-probe`. If S comes back 2 (one subsystem, not a single file), it stays a single opus agent even though the diagnosis is close to the user (I1) — S>=2 keeps the reading out of the main context regardless of I. Only a probe that brings S down to 1 sends it inline (or one opus agent, if this session is below that tier).

**"Audit all route handlers for missing auth checks and give me a report."**
S3 R1 A1 K2 I3 P3 V2, review. difficulty 4+1 = 5, sonnet. Fan out by route module (`--shards 6` once you have counted them; the default for P3 is 8), shards at sonnet (one tier down would be haiku, but K2 floors shards at sonnet), each returning a table of handler, auth check found, evidence line. Reduce inline. Verifier at sonnet on the merged report, sampling a few "no gap" claims, because the expensive failure here is a confident false negative.

## Scoring with Jev (optional)

`route.py score --task "..." --jev` asks TypeSafe's Jev the same seven questions and uses its calibrated confidence to set the `?` flags instead of your gut. It calls TypeSafe's HTTP API directly over HTTPS (stdlib `urllib`, no SDK) and needs `TYPESAFE_API_KEY`: set it in the environment, or put one line `TYPESAFE_API_KEY=<your key>` in a file named `.env` in the directory you run from (the environment always wins when set and non-empty; never commit that file). This direct, explicit use needs no other switch; the `UserPromptSubmit` hook (placement 3) additionally requires `COMPLEXITY_HOOK_JEV=on` before it will take the Jev path on its own, so a key configured for something else never sends prompts to TypeSafe without being asked. Without `--jev`, `score` runs a keyword heuristic that is honest about being crude (most dimensions come back `?`); it exists for hooks and scripts with no model in the loop. Your own scoring, with the repo and the conversation in view, is the default and usually the better one.

## Porting to Codex

The rubric, the card, and `route.py` are agent-agnostic. Only the "Dispatching in Claude Code" section is specific to this harness. A Codex port swaps that section for Codex's subagent and model-selection mechanics and keeps everything else.
