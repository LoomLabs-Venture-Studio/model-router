# Where the routing runs

The rubric and `route.py` are the same in all three placements. What changes is who does the scoring, where the tokens are spent, and whether the policy is advice or a guard-rail.

| Placement | Who scores | Output tokens on the session model | Sees conversation | Policy is |
|---|---|---|---|---|
| 1. In-thread, quiet | the session model | ~30 per decision (one Bash call) | yes | advice |
| 2. Forked scorer | a haiku fork | ~0 (fork returns one line) | no (hint only) | advice |
| 3. Hooks | a script, or Jev | 0 | no | guard-rail |

Start with 1. Move to 3 when you want a guard-rail that catches the common miss instead of relying on the model to remember, or when you are running many sessions and the per-decision output tokens add up. The guard-rail is skippable by design — see "How to skip the gate" below — so it is not a substitute for the in-thread discipline in 1 and 2.

## 1. In-thread, quiet

This is the skill as shipped. Rules that keep it cheap: no narration, one-liner as the tool result, briefs by reference, capped return shapes for probes and verifiers, `show` only on request. Nothing to configure.

## 2. Forked scorer

Run the scoring and the probe inside a forked subagent so the rubric reasoning never touches the main context. Add a second skill file, `complexity-fork/SKILL.md`, whose body is the scoring steps only, and give it:

```yaml
---
name: complexity-fork
description: Internal. Scores a task on the complexity rubric and returns one routing line. Invoked by the complexity skill, not by users.
context: fork
agent: Explore
model: haiku
user-invocable: false
---
Score the task below on the seven dimensions in ${CLAUDE_SKILL_DIR}/../complexity/references/rubric.md,
using cheap signals only (grep counts, file lists, wc -l). If scope or shard count is unsure, do the probe
yourself and re-score. Then run
"${CLAUDE_SKILL_DIR}/../complexity/scripts/route.py" route --scores "..." --kind ... --task "..."
and return ONLY its one-line output. Independence hint from the caller: $ARGUMENTS
```

The main thread invokes it with the task and a one-word independence hint (`I0`, `I2`), reads the one-liner back, and dispatches. Cost: one haiku fork of roughly 10 to 20k input tokens and a few hundred output tokens, all on the cheap model.

What you lose: the fork cannot see the conversation, so it cannot judge whether the task depends on things said earlier. The hint covers the common cases. Skills with `context: fork` run in the background by default; set `background: false` here because the main thread needs the answer before it can continue.

## 3. Hooks

Two hooks in `.claude/settings.json` (project) or `~/.claude/settings.json` (all projects). Hook scripts read a JSON payload on stdin.

### UserPromptSubmit: score before the model sees the task

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          { "type": "command", "command": "~/.claude/skills/complexity/scripts/hooks/prompt-route.sh" }
        ]
      }
    ]
  }
}
```

`prompt-route.sh` reads `prompt` from the payload (the real UserPromptSubmit field; see the [hooks reference](https://docs.claude.com/en/docs/claude-code/hooks)), skips prompts under a length threshold (questions and one-liners), slash commands, and anything that isn't a real prompt (a missing, non-string, or empty value, or stdin that isn't a JSON object), and takes the Jev path (`route.py score --task "$prompt" --route --jev`, which brings calibrated confidence and sub-second latency) only when BOTH `COMPLEXITY_HOOK_JEV` is explicitly set to `on` (also accepts `1`, `true`, `yes`, case-insensitively; anything else, or unset, means off — board decision) AND `TYPESAFE_API_KEY` is available from either source: the environment, or a non-empty `TYPESAFE_API_KEY=` line in `.env` in the cwd (put one line `TYPESAFE_API_KEY=<your key>` in a file named `.env` in the directory you run from to create one; the environment always wins). With the switch off, the hook never even checks whether a key is available for this decision — no `get_api_key()` call, no `--jev` attempt, no network path at all — it goes straight to the heuristic scorer. Presence is checked in-process by loading `route.py` and calling its own `get_api_key()` — the same function that does the real read — rather than a second, separately-written check in bash that could drift out of sync with it; the key's value is never printed, logged, or forwarded anywhere. If the Jev call fails or times out for any reason (bad key, network, malformed response), the hook falls back silently to the heuristic (`route.py score --task "$prompt" --route`) rather than printing nothing, and only ever prints a line shaped like a decision line (`[c-... · ... ]`) — never a diagnostic. The whole hook (JSON parsing, the bounded prompt, both scoring attempts, and the timeouts on each) runs under one overall time budget, enforced in Python rather than with `timeout(1)`, which stock macOS does not ship. Anything the hook prints on exit 0 is added to the model's context, so the session model receives `[c-... · S3R1?A1K2I3P3?V2 · probe->reroute]` as an input line it did not have to write. The heuristic scorer marks most dimensions unsure, so in practice this placement wants Jev; with the heuristic alone it mostly says "probe first", which is still the right default for anything large. Direct use (`route.py score --jev`) is unaffected by this switch; it stays explicit and needs no environment variable.

**What leaves your machine.** When `COMPLEXITY_HOOK_JEV=on` AND a TypeSafe API key is available — from the environment, or from a `TYPESAFE_API_KEY=` line in a `.env` file in the working directory — this hook sends the first 4,000 characters of every prompt that is 80 or more characters long to TypeSafe's API (`https://api.typesafe.ai`), and nothing else. With the switch off, or with no key available, it scores the prompt locally (the keyword heuristic) and sends nothing anywhere. A `.env` holding that key for some other purpose enables the Jev path too, once the switch is also on — the hook has no way to tell why the key is there, only that it's there.

Separately, `route.py` itself looks for a `TYPESAFE_API_KEY=` line in a `.env` file in the working directory on every `route` call, whether or not Jev is ever used — it needs the value so it can redact it from anything it prints or logs. Reading the file means parsing every line in it into memory, but only the `TYPESAFE_API_KEY` value is ever kept, used, or exposed; every other variable's value is parsed and then discarded, never printed, logged, or forwarded, and the key itself is sent to TypeSafe only when `--jev` is used or the prompt hook takes the Jev path. `route.py` never writes to `.env`.

### PreToolUse on Agent: a guard-rail on the floor

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Agent",
        "hooks": [
          { "type": "command", "command": "~/.claude/skills/complexity/scripts/hooks/gate-agent.py" }
        ]
      }
    ]
  }
}
```

`gate-agent.py` reads the tool input and finds the decision behind this Agent call: an id in the prompt wins, from any project, and never expires (that is the intended way to reuse a decision later); otherwise the most recent decision logged from the *same* project — the payload's `cwd`, or an ancestor or descendant of it, resolved and compared to the hook's own `cwd` if the payload has none — within N minutes, ignoring anything timestamped more than two minutes in the future (clock skew). A decision logged without a `cwd` (an older log) is never used by this fallback. It then returns one of two `hookSpecificOutput.permissionDecision` values with a reason (otherwise it says nothing, and the call proceeds):

- `deny` when no decision applies (an Agent call with no routing behind it, or an id the prompt names that isn't in the log), or when the applicable decision has K >= 2 and the call's model is haiku;
- `ask` when the applicable decision has K == 3 and the call would run a non-read-only agent: this is not a denial, it is a human permission prompt (approve to proceed). K == 3 is never silently denied — a human confirming it is the gate the policy asks for at that risk level.

The reason text goes back to the model on a `deny`, which then fixes the call; on an `ask`, it goes to the human deciding whether to approve. Current builds also accept `hookSpecificOutput.updatedInput`, which would let the hook rewrite `model` instead of denying (this is what `COMPLEXITY_GATE_MODE=rewrite` does for the K>=2/haiku case); that is documented for hooks in general but not confirmed for the Agent tool's `model` field specifically, so treat deny-with-reason as the primary mechanism and test `updatedInput` before relying on it.

Malformed input fails open rather than denying wherever the gate genuinely cannot verify anything at all: bad stdin, a payload or `tool_input` that isn't the expected shape, or an unreadable log all make the gate say nothing (the call proceeds). A bad timestamp on a candidate decision is different, and not silent in the same way: that one record is simply skipped as a candidate (same for a bad `COMPLEXITY_GATE_WINDOW_MIN`, which falls back to its 30-minute default rather than being used as-is) — if that leaves no decision that applies at all, the gate explicitly *denies* for lack of one, the same as if nothing had been logged in the window, rather than staying silent about it. The two blocks that stay in every case: an explicit id not found in the log, and an Agent call with no applicable decision at all.

This is a guard-rail, not a security boundary: it runs inside the same trust boundary as the session it is guarding, and it is meant to be skippable. How to skip the gate:

- `COMPLEXITY_GATE=off` turns the whole gate off.
- A prompt containing a read-only marker ("read-only", "read only", "do not edit", "size this task", "independent review"), or `subagent_type: Explore`, skips the K3 approval prompt.
- "size this task" in the prompt, or `subagent_type: Explore`, is *additionally* treated as a sizing probe and skips the K>=2 haiku floor too — a sizing probe cannot cause the incident that floor guards against. The other read-only markers ("read-only", "do not edit", "independent review") are not exempt from the floor: a read-only *review* shard on a cheap model can still miss the gap it was sent to find, which is the incident the floor exists for.
- An Agent call with no `model` field passes the K>=2 floor: the gate cannot know the model the call will actually inherit.

### SubagentStop: log outcomes automatically

A `SubagentStop` hook receives the finished agent's type and last message. Pattern-match the message for the return shape your briefs demand (a table, `pass`/`fail`) and append an `outcome` record with the decision id found in the brief. This closes the loop without the session model spending a call on it.

### What hooks change

In placements 1 and 2 the policy is something the model agrees to follow. In placement 3 it is something the harness checks. That distinction is the whole point of a governance layer: a floor that a rushed model can skip is not a floor. The per-decision cost also drops to a script run, or a Jev call at a fraction of a cent, and nothing about routing ever appears in the thread unless the user asks.

## Log growth and rotation

The decision log is append-only: `route.py` and `gate-agent.py` only ever add a line, never rewrite or delete one, so nothing here needs a new environment variable or ever deletes a user's history on its own. A line is typically well under 1KB (the task text alone is capped at 200 characters). `show`, `outcome`'s id check, and `stats --last N` read only the last 1MiB of the file rather than the whole thing, falling back to a full scan only when an id isn't found in that window, so none of them get slower as the log grows into the megabytes. To rotate it, just rename the file (e.g. to `decisions-2026-09.jsonl`) and let `route.py` create a fresh one on the next write; nothing reads old files back in automatically. If the log can't be written at all (an unwritable directory, `COMPLEXITY_LOG` pointing somewhere invalid), routing still works — `route.py` prints the decision normally and warns once on stderr — but that decision was never recorded, so the gate will not recognise it: an Agent call citing its id gets the same "not in the log" denial as an id that was simply mistyped.

## Cost sanity check

Rough ratios, not prices: output tokens cost several times input tokens on the same model, and a frontier session model costs an order of magnitude or two more per token than haiku. So one paragraph of routing narration on the session model (say 300 output tokens) is in the same range as the entire haiku probe (15k input tokens) that the routing was trying to justify. Narration is where the savings go to die; the one-liner exists for that reason.
