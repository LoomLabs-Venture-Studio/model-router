# Brief template

A subagent starts with nothing but this text. It cannot see the conversation, your reasoning, or what you already ruled out. Most delegation failures are brief failures: the agent solved a neighboring problem, redid work you had done, or returned a wall of text when you needed one number. Write the brief so a strong engineer who just walked in could act on it without asking a question.

Keep it under a screen. Fill every section; "none" is a valid answer and better than silence.

```
Goal
  One sentence. What is true when this is done.

Done means
  The concrete acceptance criteria. Tests pass; a table with these columns; a yes/no with evidence.

Context you need
  Repo facts, file paths, the pattern to copy, the decision already made. Paste small things; point at large ones.

Already ruled out
  What was tried, what not to redo, what is deliberately out of scope.

Constraints
  Do not edit outside <paths>. Do not run <commands>. Do not change the public API. Read-only if review or explore.

Return
  The exact shape of the answer: "a list of file:line with a one-line reason", "the diff plus the test output",
  "yes/no plus the three strongest pieces of evidence". Short. The main thread only needs the conclusion.
```

## Shard briefs

For fan-out, write one brief and parameterize it. Every shard gets the same Goal, Done, Constraints, and Return; only the "your slice" line changes:

```
Your slice: routes/billing/*.py (7 handlers). Other agents cover the other modules; do not look outside your slice.
```

Give shards a fixed return schema so the reduce step is mechanical:

```
Return one row per handler: handler | file:line | auth check present (yes/no/unclear) | evidence line
```

## Probe briefs

A probe asks for facts, not a plan. Ask for the things that change the routing:

```
Read-only. Size this task, do not start it: "<task>".
Return:
  - files or modules involved, with a count
  - an existing pattern or example to copy, if any (file:line)
  - test coverage of the area (suite name, or "none")
  - anything that makes this bigger than it sounds (shared code, migrations, external calls, feature flags)
  - how many independent pieces it splits into
Ten lines or fewer.
```

## Verifier briefs

The verifier must not be anchored on the draft. Give it the task and the artifact; withhold the reasoning.

```
Independent review. Original task: "<task>".
Attached: the diff (or the report). You have not seen how it was produced; judge it on its own.
Check: does it do what the task asked; what would break; what did it miss; is any claim unsupported.
Return: pass / pass with notes / fail, then the three most important findings with file:line.
```
