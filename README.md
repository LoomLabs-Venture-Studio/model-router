# model-router

> **Status: a small experiment, not a production-ready skill.** It was tried on four small coding tickets against a 139-line app, one run per setup. All three setups passed the same 51 hidden tests, so the tests could not tell the models apart; a blind review still found a bug they missed in a cheaper model's code; costs for the same model moved about 15% between runs; and the fan-out path has never been executed end to end. Read `experiments/ab-routing/REPORT.md` and `experiments/ab-routing/REPORT_r2.md` for the numbers and their limits before relying on any of it. Expect rough edges. MIT licensed, no warranty.

A Claude Code skill (`skills/complexity/`) that measures a task's complexity before dispatching it, then routes it: inline, one subagent, or a fan-out, with the model tier (haiku, sonnet, opus, fable), verifier, and human gate for each. Judgment is the model's job; policy is code (`route.py`), logged, and tunable. The optional scorer uses TypeSafe's Jev so routing costs no Claude tokens at all.

Status: iteration 1 complete (2026-09-19). Claude Code first; Codex port pending.

## Layout

```
skills/complexity/            the skill (SKILL.md, references/, scripts/route.py, scripts/hooks/)
skills/complexity/evals/      eval prompts and assertions
tests/complexity/             unit tests for route.py and both hooks (pytest)
evals/fixture/shopapi/        small FastAPI fixture the evals run against
evals/iteration-1/            with-skill vs baseline runs, grading, benchmark, review.html
experiments/ab-routing/       A/B harness: Jev-routed and Claude-routed delegation vs all-session-model (REPORT.md has run r1, REPORT_r2.md has run r2)
dist/                         packaged .skill (gitignored build output)
.claude/skills/complexity     symlink to skills/complexity so the skill is active in this repo
```

## Use

```bash
# score + route (prints one line, logs the full card)
skills/complexity/scripts/route.py route --scores "S3 R1 A1 K2 I3 P2 V2" --kind review --shards 7 --task "audit handlers"
skills/complexity/scripts/route.py show last          # full card for a logged decision
skills/complexity/scripts/route.py outcome --id c-xxxxxxxxxxxx --result ok
skills/complexity/scripts/route.py stats
skills/complexity/scripts/route.py score --task "..." --route          # keyword heuristic
skills/complexity/scripts/route.py score --task "..." --route --jev    # calibrated (TypeSafe Jev)
```

`--jev` calls TypeSafe's HTTP API directly with the Python standard library; there is nothing to install. It needs `TYPESAFE_API_KEY`, either in the environment or in a `.env` file in the directory you run from (copy `.env.example`; `.env` is gitignored). The environment wins when both are set. The key is never printed or logged.

In Claude Code: `/complexity <task>`, or it triggers on its own before any Agent dispatch.

Install elsewhere: copy `skills/complexity` to `~/.claude/skills/complexity` (all projects) or `<repo>/.claude/skills/complexity` (one project), or save `dist/complexity.skill` from the Claude app.

## Placements

`skills/complexity/references/placements.md` describes three ways to run the routing: in-thread quiet (default), forked scorer, and hooks (`scripts/hooks/prompt-route.sh` on UserPromptSubmit, `scripts/hooks/gate-agent.py` on PreToolUse for Agent). Hooks make the policy a guard-rail the user can skip and take routing out of Claude's token budget.

## Tests

Lint and unit tests:

```bash
python3 -m py_compile skills/complexity/scripts/route.py skills/complexity/scripts/hooks/gate-agent.py
bash -n skills/complexity/scripts/hooks/prompt-route.sh
python3 -m pytest tests/complexity -q
```

The tests never touch the network or a real API key, and pytest is a development dependency only; the skill itself is standard library only.

## Evals

`evals/iteration-1/review.html` is the review page (open in a browser). Benchmark: with skill 100% of assertions, baseline 50%, at +83s and +24k tokens per decision (since reduced by the quiet mode). Known gap: the fixture is 146 lines, so the fan-out path was never exercised; iteration 2 needs a fixture with 40+ handlers.

## A/B routing experiment

`experiments/ab-routing/` compares two ways of delegating the same four coding tickets on the fixture: Arm A routes each ticket with `route.py score --jev --route` and dispatches what it decides; Arm B sends every ticket to the session model. Solutions are graded by 51 hidden acceptance tests, costed per model from subagent transcripts, and blind-reviewed as anonymous X/Y diffs. Preliminary run r1 (2026-09-19): both arms passed 51/51 on the corrected oracle; the routed arm cost $3.47 against $9.09 (ratio 0.382). One run, four small tasks: indicative, not conclusive. Findings, deviations, and limits are in `experiments/ab-routing/REPORT.md`; the runbook and pre-registered dispatch rules are in its `README.md`. The experiment needs `fastapi`, `httpx`, and `pytest` in a venv outside the repo (`make_env.py`); the skill itself stays stdlib-only.

Phase 2, run r2 (2026-09-20): same four tickets and 51 hidden tests, but routing by a fresh Claude Fable 5.1 instance per ticket, unaided (no skill, Jev, rubric, or tools). The baseline is Phase 1's Arm B, imported byte for byte and not re-run. Result: 51/51 hidden tests; $6.37 against $9.09 baseline (ratio 0.700; 0.529 with the routers priced at marginal cost) versus the Jev-routed arm's $3.47 (0.382) in r1. Claude chose Sonnet for three tickets and Opus plus a Sonnet verifier for one; no fan-outs or probes. Router calls: $1.69 full ($0.14 marginal); Jev: $0.0005. Blind review found a contract violation not in the hidden tests: the routed arm's Sonnet refund handler returns 422 instead of 401/403 when a bad token is combined with an invalid parameter. One run, baseline reused: indicative, not conclusive. Detail in `experiments/ab-routing/REPORT_r2.md` and `experiments/ab-routing/reports/r2-notes.md`; the Phase 2 dispatch rules and run-window procedure are pre-registered in `experiments/ab-routing/README.md`.

## Re-running the evals

The eval prompts are in `skills/complexity/evals/evals.json`; the runner is the skill-creator workflow (with-skill and baseline subagents per prompt, grader, `aggregate_benchmark`, `generate_review.py --static`). Fixture paths in `skills/complexity/evals/evals.json` (e.g. `fixture/shopapi`) are relative to this repository's `evals/` directory. The fixture is not part of a packaged skill, so the evals run from a checkout of this repository.
