# A/B routing experiment

Answers the question the `complexity` skill exists to answer: does routing
delegations by Jev's complexity score (**Arm A**) cost less than sending
everything to the session model (**Arm B**), without losing quality? Design is
board-confirmed in `PLAYBOOK.md` ("Sprint: A/B routing harness", 2026-09-19);
this file is the runbook for actually running it.

**Status: Wave 1A + 1B built.** This directory has the full tooling and the
oracle: `make_run.py`, `route_arm_a.py`, `account.py`, `pricing.json`,
`make_env.py`, `briefs/`, `hidden_tests/`, `reference/`, `score.py`, and
`selftest/`. **Not built yet** (Wave 2): the runs themselves, the blind X/Y
review, and `REPORT.md`. Nothing in this directory has made a live TypeSafe
call; `route_arm_a.py` is verified only through its stubbed selftest.
`make_env.py` and `score.py` (against the pristine fixture and the reference
patches) HAVE been run for real -- see Setup and Selftests below.

## Setup

- Python 3.10+, stdlib only for every harness script (`make_run.py`,
  `route_arm_a.py`, `account.py`, `score.py`, `make_env.py`). No install step
  for those.
- `make_env.py` builds the ONE shared interpreter both arms' agents and
  `score.py` use, at `~/.cache/shopapi-dev/venv` by default (override with
  `--venv`; never an env var), pinned by `requirements.txt`
  (fastapi==0.141.1, httpx==0.28.1, pytest==9.1.1; the full resolved set is
  recorded in `requirements.lock`). It's idempotent -- rerun it any time.
  This lives outside the repo and outside the run directories so its path
  never hints at the harness; briefs name it directly
  (`~/.cache/shopapi-dev/venv/bin/python`).
- `pytest` is also required, separately, to run `experiments/ab-routing/selftest/`;
  that one's already on the system `python3` (verified: pytest 9.x). Don't
  confuse the two: selftests and the three skill gates run on system
  `python3`; hidden tests and agents' work run on the shared venv above.
- `TYPESAFE_API_KEY` must be resolvable from the *repo root* (env var or the
  repo's real `.env`) for Arm A to work. `route_arm_a.py` always runs `route.py`
  with `cwd` set to the repo root for exactly this reason -- it never reads
  `.env` itself.

## Order of operations for a full run

1. **`make_run.py --run <id>`** -- creates `<runs-root>/<id>/` (default
   `~/.cache/ab-routing/runs/<id>/`, override with `--runs-root`) with
   `A/task1..4/` and `B/task1..4/`, each a fresh one-commit git repo copied from
   the git-tracked files under `evals/fixture/shopapi/`. Also writes
   `manifest.json` with the 8 empty slots. Refuses to overwrite an existing run
   id. **Never run this with `--runs-root` pointing inside this repo.**
2. **Arm B dispatch.** For each task, take `briefs/task<n>.md`, substitute the
   literal `{PROJECT_DIR}` placeholder with that task's `B/task<n>/` absolute
   path, and give the resulting text to one engineer subagent on the session
   model, verbatim, with no additions. No routing involved.
3. **Arm A route + dispatch**, per task:
   - `route_arm_a.py --run <id> --task <n> --brief <path-to-brief-with-{PROJECT_DIR}-already-substituted>`
     builds `--context` from the task's own copy (tracked file count, total
     Python line count, the test command, and whether `pytest` currently
     passes -- computed against a throwaway copy of the copy, never the copy
     itself), calls `route.py score --jev --route --json` with the brief
     **verbatim**, and appends the full decision (scores, confidences, kind,
     Jev token usage, decision id, one-liner) to `<run>/decisions.json`. It
     also points `COMPLEXITY_LOG` at `<run>/decisions.jsonl` so this doesn't
     pollute the real decision log at `~/.claude/complexity-router/decisions.jsonl`.
   - The CTO dispatches **exactly** what the printed one-liner says: same
     model tier, same agent count, the SAME substituted brief text as Arm B
     got. No scoring, no nudging, no overriding -- there is no flag anywhere
     in this path that accepts a human-supplied score.
   - **Probe rule:** if the decision is `probe->reroute`, run exactly one
     read-only `Explore` agent on haiku, save its output to a file, then call
     `route_arm_a.py` again with `--after-probe --probe-output <file>`. Never
     probe a second time for the same task -- `route.py` itself refuses to
     treat a second probe conservatively past that point.
   - Both the probe and the Jev API call count toward Arm A's cost; `account.py`
     folds Jev usage in automatically, and the probe is just another transcript
     entry in the manifest (`role: "probe"`).
4. **Fill in `manifest.json`** -- for each of the 8 slots, add one entry per
   subagent transcript involved (`engineer`, `probe`, or later `reviewer`):
   `{"arm": "A", "task": 1, "role": "engineer", "transcript": "/abs/path/to/transcript.jsonl"}`.
   Transcript paths should be absolute (Claude Code subagent transcripts don't
   live inside the run directory).
5. **`score.py --run <id>`** -- for each of the 8 `(arm, task)` copies, clones
   its CURRENT working tree (a plain directory copy, uncommitted changes
   included -- never `git clone`, since agents aren't expected to commit) to a
   throwaway temp directory, runs that task's `hidden_tests/task<n>/` suite
   there with the shared interpreter (120s timeout), and writes
   `<run>/results.json`: per arm/task/test (name, mark, outcome), counts, and
   the pass bar (`tests_passed_by_both / tests_passed_by_B`, overall and per
   task, each with a `>= 0.90` boolean). Nothing ever runs inside the original
   task copy; afterwards each one is asserted byte-for-byte untouched.
   Run `score.py --validate` any time (no `--run` needed) to re-confirm the
   oracle itself: every target test fails and every guard test passes on the
   pristine fixture, and every `reference/task<n>.patch` makes that task's
   hidden tests pass 100%.
6. **`account.py --run <id>`** -- reads `manifest.json` and `decisions.json`,
   sums tokens per `(arm, task, role, model)` (de-duplicated by message id,
   last occurrence wins for streamed chunks), splits cache-write tokens by TTL
   (5-minute vs 1-hour) and prices everything from `pricing.json`, adds Arm
   A's Jev usage as its own line item, and writes `<run>/costs.json`
   (per-task, per-arm, and per-model totals in tokens and USD, plus a grand
   total per arm). Billing modifiers this accounting doesn't model
   (`inference_geo=us`, `speed=fast`, a batch `service_tier`) are hard errors;
   anything else unrecognised is a warning, recorded per `(arm, task)`.
7. **Blind X/Y diffs** -- `blind_pack.py --run <id>` creates anonymized diffs
   for each task, swapping arm labels randomly per task, and writes `blind_key.json`
   (mapping persisted for later de-anonymization).
8. **`REPORT.md`** -- `report.py --run <id>` generates the full report: pass-bar
   result, per-task test outcomes, Arm A routing decisions, cost breakdown, blind
   review section (pending), threats to validity, and reproduction steps.

## Blind review and report

```bash
python3 experiments/ab-routing/blind_pack.py --run <id> [--seed N]
python3 experiments/ab-routing/report.py --run <id> [--review <path>] [--out <path>]
```

`blind_pack.py` packages each task's two solutions as anonymous X/Y diffs (arm
labels randomized per task), writes `blind/task<n>_{X,Y}.diff`, `blind_key.json`
(mapping), and `blind/REVIEW_PROMPT.md`. `report.py` reads results/costs/decisions
JSON and generates `REPORT.md` with pass bar, per-task table, routing decisions,
cost breakdown, blind-review section (pending de-anonymization), and threats.

## What the operator may and may not do, per arm

- **Both arms:** nothing may be run inside a task copy (`A/task<n>` or
  `B/task<n>`) -- by the harness or by the operator -- before the agent
  starts. Arm and task copies must start from identical state; running
  anything first (even read-only-seeming things like `pytest`) can leave
  import-time side effects behind (this fixture's `app/core/db.py` opens
  `shop.db` on import), giving one arm a head start the other never gets.
  `route_arm_a.py`'s own context build runs `pytest` against a throwaway
  copy of the copy for exactly this reason, then verifies the task copy
  itself is still pristine (`git status --porcelain --ignored` empty) before
  going any further -- a mutated copy is a hard error, not a warning.
  `score.py` follows the same rule: it clones each copy's working tree before
  running anything, and re-verifies each one is untouched afterwards.
- **Arm A:** dispatch exactly what `route_arm_a.py`'s one-liner says. Never
  substitute a different model or shard count, never hand-score, never skip
  the probe step when the decision calls for one, never probe more than once
  per task.
- **Arm B:** one engineer subagent per task, on the session model, no routing
  step at all. This is the baseline "just send it" arm.
- Neither arm's brief may mention the experiment, the arms, hidden tests, or
  this repository -- `briefs/task<n>.md` are written as ordinary engineering
  tickets for the `shopapi` service, with the shared contract embedded
  verbatim and a single `{PROJECT_DIR}` placeholder to substitute. Agents have
  filesystem access to their copy and could in principle go looking for the
  hidden tests regardless; run copies living outside the repo is the only
  mitigation for that (see Threats to validity).

## Rerunning

Pick a new `--run` id -- run ids are refused, not overwritten, so a rerun is
always `make_run.py --run <new-id>` followed by the same steps. There's no
in-place "reset" of a run; delete the run directory yourself if you want to
free the id.

## Where artefacts land

Everything for a run lives under `<runs-root>/<run-id>/` (default
`~/.cache/ab-routing/runs/<run-id>/`), **outside this repo**, so an agent
working inside a copy can't see the harness, the other copies, or the fixture
it came from:

```
<run>/
  A/task1../4/        8 independent one-commit git repos (fixture copies)
  B/task1../4/
  manifest.json        arm/task/role -> transcript path
  decisions.jsonl       route.py's own log (redirected here, not the real one)
  decisions.json        route_arm_a.py's full per-call records
  costs.json            written by account.py
  results.json          written by score.py: per-test outcomes + the pass bar
```

Nothing here ever writes into `evals/fixture/`; `make_run.py`'s selftest
asserts that directly, and `score.py --validate` builds its own throwaway
copies from the tracked fixture rather than touching it.

## Hidden tests, the oracle, and grading

- `hidden_tests/task<n>/` are self-contained pytest suites (`pytest.ini` at
  `hidden_tests/` registers the `target`/`guard` markers and anchors rootdir
  there regardless of invocation cwd). Every test takes `--target=<clone>` (a
  throwaway clone the *runner* provides -- **use the `=` form**: a
  space-separated `--target <path>` gets misparsed by pytest's early,
  pre-plugin argv scan for conftest.py files to preload once the target has
  its own `tests/conftest.py`, which every real solution will). Tasks 2-4
  build `shop.db` (migration + the shared seed data, `hidden_tests/_shopdb.py`)
  into the clone and `chdir`/`sys.path` into it from `pytest_configure` (which
  runs before collection -- a fixture would be too late, since
  `app.core.db` connects to `./shop.db` at import time); task 1's checks are
  black-box subprocess checks per its own throwaway copy-of-the-copy. Every
  test is marked `target` (fails on the pristine fixture) or `guard` (passes
  on pristine; a solution must not break it); each task has at least 8
  targets. `hidden_tests/inventory.json` is the pristine-collected list of
  every test and its mark, used by `score.py` so a suite that errors at
  collection still counts every one of its tests as failed rather than
  silently shrinking the pass-bar denominator.
- `reference/task<n>.patch` is a minimal git patch, against the pristine
  fixture, that makes that task's hidden tests pass 100% (and keeps the
  fixture's own two tests passing) -- proof the tests are satisfiable and not
  over-fit to one implementation. Apply with `git apply reference/task1.patch`
  from a pristine copy's root.
- `score.py --validate` re-confirms both of the above against the live
  fixture and patches any time (no run needed); it's the gate CI-equivalent
  for the oracle itself.

## Threats to validity (from PLAYBOOK, not hidden)

- Four tasks, one run per arm per task: differences are indicative, not
  statistically significant.
- The fixture is 139 lines, so scope (S) barely varies across tasks.
- Agents have filesystem access and could in principle find the hidden tests
  even though run copies live outside the repo and briefs never mention them.
- Arm B's model is whatever the CTO session happens to run on, not a fixed
  reference model.

## Selftests

```
python3 -m pytest experiments/ab-routing/selftest -q
python3 experiments/ab-routing/score.py --validate
```

`selftest/` covers: `make_run.py` (all 8 copies are clean, one-commit repos;
the tracked fixture is provably untouched; a repeated run id is refused);
`account.py` (token de-duplication by message id, exact USD against a
synthetic pricing file, the cache-write TTL split and its fallback/mismatch
paths, the billing-modifier guard rails, and the unknown-model/null-price
hard errors); `route_arm_a.py` (context building against a real throwaway
fixture copy -- including the F1 regression, that it never mutates the task
copy itself -- and stdout parsing / decision recording with route.py's own
subprocess call stubbed); and `score.py` (JUnit parsing including the
collection-error placeholder, the pass-bar arithmetic against synthetic
per-test data -- a normal split, B passing zero tests, a collection error --
and the untouched-copy snapshot comparison). No live call to
`api.typesafe.ai` happens anywhere in the suite.

`score.py --validate` is the integration test for the hidden tests and
reference patches themselves (real pytest runs against the real fixture and
real patches) and is intentionally not duplicated with synthetic data in
`selftest/`.

## Dispatch rules (pre-registered 2026-09-19, before any run; do not change after a run starts)

The operator (the CTO session) makes no judgment calls during a run. Every routing outcome
maps to exactly one action:

**Both arms**
- Task agents are `general-purpose` subagents, not the project's `engineer` agent: the
  engineer role's standing instructions point at this repository's PLAYBOOK, which describes
  the experiment. The prompt is the brief with `{PROJECT_DIR}` substituted and nothing else.
- Agents within an arm run concurrently, in the background, one per task copy. Agent names
  are `ab<arm>-t<n>` (plus `-probe`, `-verify`) so transcripts map to manifest slots
  mechanically.
- No follow-up messages to a task agent. If it stops early or asks a question, its copy is
  scored as it stands. The single exception is the Arm A verifier fix round below.

**Arm B:** model `fable` (the session model, Claude Fable 5.1), one agent per task. No
routing, no probe, no verifier.

**Arm A:** run `route_arm_a.py`, then act on the one-liner it records:
- `agent <tier>`: one agent at that tier.
- `inline`: one agent at the decision's task tier (from `route.py show <id>`). The operator
  never implements, and inline work would hide its cost in the operator's own session.
- `fan-out Nx<tier>`: one agent at the task tier, recorded as a deviation. Slicing a task
  into shards needs operator judgment, which this experiment forbids; the fixture is also
  too small to shard. Report the deviation and what Jev asked for.
- `probe->reroute`: the probe rule above (one read-only `Explore` probe on `haiku`, prompt
  from the skill's probe brief template with the task brief as the task), then re-route
  with `--after-probe` and apply these same rules to the new decision.
- `verify:<model>`: after the task agent finishes, one read-only `general-purpose` verifier
  at that model, prompted with the skill's verifier brief template (the task brief and the
  `git diff` of the copy, never the agent's reasoning). If it returns `fail`, send its
  findings verbatim to the task agent once; no second verification. Verifier and fix-round
  tokens count toward Arm A.
- A human gate (K=3) is auto-approved and recorded; nothing here is irreversible.
- If the Jev call fails: wait 30 seconds, retry once. If it fails again, abort the run.
  Never substitute the keyword heuristic or a hand score.
- A tier the account cannot use falls back as `route.py` already specifies (fable to opus);
  record it.

**Order:** `make_env.py`, `score.py --validate`, `make_run.py`, all of Arm B, all of Arm A,
fill `manifest.json`, `score.py`, `account.py`, blind review, report.

## Phase 2: Claude as the router (pre-registered 2026-09-19, before any Phase 2 run)

Same test as Phase 1, with the router swapped: no Jev, no `complexity` skill, no rubric, no
`route.py`. In the harness files the routed arm still occupies the `A/` slots of the run; the
baseline `B/` slots are imported from Phase 1 with `import_baseline.py`.

**Environment for the run window**
- Remove the `.claude/skills/complexity` symlink before spawning any router, probe, verifier,
  or task agent, and restore it (`git checkout -- .claude/skills/complexity`) as soon as the
  last one has been spawned and finished. Never commit the removal. The operator tells the
  board when it is removed and when it is back.
- For the same window, replace the repository's `CLAUDE.md` with a neutral stub ("Internal tooling
  repository. No project-specific instructions.") and restore it afterwards
  (`git checkout -- CLAUDE.md`). Every subagent gets `CLAUDE.md` injected, and the real one
  describes this project as a complexity router and names its seven dimensions. Never commit
  the stub. No engineer, QA, or other non-run agent may be spawned inside the window (board
  decision 2026-09-19).
- The operator session must not act as the router: it knows the Phase 1 outcomes.

**Router**
- One fresh `general-purpose` agent per ticket on `fable` (the session model), named
  `abC-t<n>-router`. Its prompt is the full text of the file `route_arm_c.py` writes, pasted
  verbatim, because the router gets no tools and therefore cannot read a file. The prompt
  tells it to call no tool and no skill and to reply with the JSON object only.
- The operator saves the reply verbatim and runs `route_arm_c.py --record`. If it does not
  validate, the operator sends the router exactly one message: "Reply with only the JSON
  object in the required format." If the second reply does not validate, that task is aborted
  and reported. The operator never edits, completes, or substitutes a decision.
- A router that calls any tool or skill invalidates its decision: report it and re-run that
  ticket once with a fresh router.

**Dispatch (identical in spirit to Phase 1)**
- `mode: agent`: one `general-purpose` task agent at the chosen `model`, named `abC-t<n>`,
  prompted with the one-line pointer to its ticket file.
- `mode: fan-out`: one agent at the chosen `model`, recorded as a deviation.
- `probe_first: true`: one read-only `Explore` probe on `haiku` with the Phase 1 probe prompt,
  then a second, fresh router agent with the after-probe prompt. One probe per ticket; a
  second `probe_first` is ignored and recorded.
- `verifier: <model>`: the Phase 1 verifier rule (one read-only verifier at that model on the
  ticket and the diff; on `fail`, its findings go to the task agent once).
- Any model on the menu may be chosen, including `fable`.
- No follow-up messages to task agents. Router, probe, and verifier tokens are charged to the
  routed arm; the router is reported both in full and as a marginal cost.

**Order:** `score.py --validate`, `make_run.py`, `import_baseline.py`, remove the symlink,
route and dispatch all four tickets, restore the symlink, fill `manifest.json`, `score.py`,
`account.py`, audit, `blind_pack.py`, blind review, `report.py --compare-run r1`.

**Command lines for the three new steps:**

```bash
# import_baseline.py -- reuse Phase 1's Arm B into the new run's B/ slots
python3 experiments/ab-routing/import_baseline.py --run r2 --from-run r1 --arm B

# route_arm_c.py -- per ticket, per call: build the prompt, paste it into a fresh
# router agent verbatim, save its reply, then record it. Two separate invocations;
# --record only ever accepts what the router actually replied.
python3 experiments/ab-routing/route_arm_c.py --run r2 --task 3 --brief experiments/ab-routing/briefs/task3.md
#   -> prints the prompt's path (default: outside both the repo and the run, at
#      <runs-root>/../router/r2/task3_router_prompt.md)
python3 experiments/ab-routing/route_arm_c.py --run r2 --task 3 --record /path/to/saved_reply.txt

# after a probe (one per ticket; a second is ignored and recorded as such):
python3 experiments/ab-routing/route_arm_c.py --run r2 --task 3 \
    --brief experiments/ab-routing/briefs/task3.md --after-probe --probe-output /path/to/probe_output.txt
python3 experiments/ab-routing/route_arm_c.py --run r2 --task 3 --record /path/to/saved_reply.txt

# report.py -- the three-way comparison against Phase 1
python3 experiments/ab-routing/report.py --run r2 --compare-run r1 --out experiments/ab-routing/REPORT_r2.md
```
