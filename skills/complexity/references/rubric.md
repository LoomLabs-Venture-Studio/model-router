# The rubric, all four levels

Score each dimension on its own. The most common failure is halo: a task that feels hard gets 3s across the board, and the router over-spends. Ask each question as if the others did not exist.

Mark a score with `?` when you are guessing. The router rounds unsure risk and reasoning up and turns unsure scope into a probe, so `?` is cheap to admit and expensive to hide.

## S · Scope: how much remains to be read or touched?

| | Anchor | Examples |
|---|---|---|
| 0 | One known location; no search needed | Fix a typo in a named file; change one config value |
| 1 | A handful of files in one module, or under roughly 300 lines to read in total | Add a field to a model and its serializer; a rename in a small repo |
| 2 | One subsystem, or a search is needed to find the extent (roughly 5 to 20 files, or 300 to 2,000 lines) | Rename a function used across a package; add logging to every handler in a service |
| 3 | Unknown extent, or 20+ files or 2,000+ lines across several subsystems | Change an ID type used everywhere; audit every route in a large service; "make the app support multi-tenancy" |

Scope is the main driver of *delegation*, and it measures context cost, so volume matters more than file count. Score it from cheap signals (a grep count, `wc -l`, a directory listing) rather than by reading the material: the reading is what you are deciding whether to delegate. Material already in the main context counts as zero remaining scope; delegation cannot protect context that has already been spent.

## R · Reasoning: how deep is the inference?

| | Anchor | Examples |
|---|---|---|
| 0 | Mechanical; a pattern to copy exists | Rename, format, lint fixes, boilerplate from an adjacent example, version bump |
| 1 | Known pattern with local adaptation; a bug with an obvious cause | Add a CRUD endpoint like the others; fix a null check; write tests for a pure function |
| 2 | Several interacting constraints; design choices; debugging with hypotheses; unfamiliar API | Add caching with invalidation; integrate a third-party SDK; find why a job runs twice |
| 3 | Novel design; subtle concurrency, security, performance, or data-consistency reasoning; multi-hop causal debugging | Intermittent failures under load; a race in a payment flow; schema migration with live traffic; a new permissions model |

Reasoning is the main driver of *model tier*. Cheap models are fine at 0 and 1 and unreliable at 3 even with tests.

## A · Ambiguity: how underspecified is it?

| | Anchor | Examples |
|---|---|---|
| 0 | Fully specified; one right answer | "Change the timeout from 30s to 60s in `client.py`" |
| 1 | Minor gaps with obvious defaults | "Add pagination to the users list" (page size, cursor vs offset are defaultable) |
| 2 | Several valid readings; needs judgment or a clarifying question | "Make search faster" (index? cache? query rewrite? which queries?) |
| 3 | The goal itself is unclear; needs discovery or product judgment | "Improve onboarding"; "clean up the data layer" |

A=2 or 3 usually means one question to the user is cheaper than any amount of model. Ask it before routing when the user is present. When they are not, take the most reasonable reading, say which one you took, and score A as it stands.

## K · Risk: what is the blast radius if wrong?

| | Anchor | Examples |
|---|---|---|
| 0 | Local, reversible, no side effects | Docs, comments, scratch scripts, tests, a personal notebook |
| 1 | Reversible through normal review | Ordinary application code that ships in a PR |
| 2 | Touches data, auth, payments, migrations, public API contracts, CI or infra config, shared libraries | Change a permission check; alter a schema; edit the deploy pipeline; bump a shared dependency |
| 3 | Irreversible or production-affecting | Run against prod data; deploy; rotate secrets; move money; delete; send email or post publicly; force-push |

Risk is about consequences, not effort. A one-line change can be K3. K sets the floor on model tier, decides whether a verifier is mandatory, and at 3 inserts a human gate before the irreversible step regardless of everything else.

## I · Independence: can it run without this conversation?

| | Anchor | Examples |
|---|---|---|
| 0 | Needs live back-and-forth, or depends on preferences stated earlier in this chat that you cannot write down in a paragraph | Iterating on a design with the user; "make it feel like the other one we did" |
| 1 | Briefable, but the brief needs care: several constraints to transfer | A fix that must respect three decisions made earlier in the session |
| 2 | Cleanly briefable; the result is a diff or a short conclusion | Implement a spec'd endpoint; write a migration for a given change |
| 3 | Cleanly briefable and mostly reading; the main thread only needs the conclusion | "Which modules import the legacy client?"; "Does any handler skip auth?" |

Independence is a gate. I=0 means inline, full stop; if scope is also large, delegate the *reading* with a probe and keep the *deciding* here. Everything a subagent knows comes from the brief, so if you cannot write the brief, you cannot delegate.

## P · Parallelism: how many independent shards?

| | Anchor | Examples |
|---|---|---|
| 0 | One sequential thread | A single bug; a single feature with one code path |
| 1 | 2 or 3 independent pieces | Backend change plus a frontend change plus docs |
| 2 | 4 to 8 independent pieces | Audit six services; port five modules |
| 3 | 9+, or a map over files or records | Classify every ticket; check every handler; summarize every doc |

Independent means a shard neither needs nor changes what another shard produces. Shards that share a file are not independent (they will conflict on write). When the pieces are independent but tiny, do not fan out: the fixed cost of a brief and a reduce step is real.

## V · Verifiability: how will you know it is right?

| | Anchor | Examples |
|---|---|---|
| 0 | Self-evident on inspection | A rename; a typo; a config value |
| 1 | Tests or a checker exist or are cheap to write; generate-test-iterate works | Refactor under an existing suite; a function with a clear spec |
| 2 | Needs judgment review; correctness is not mechanically checkable | Report quality; API design; a performance fix with no benchmark |
| 3 | No oracle; needs an independent reviewer, adversarial check, or human sign-off | Security audit conclusions; a migration plan; anything where a confident wrong answer is the expensive failure |

V interacts with K. Low V and low K justify a cheaper model (the checker is doing the hard part). High V with high K demands a verifier that has not seen the reasoning, because a reviewer who reads the argument first tends to agree with it.

## Kind

Not a difficulty measure; it chooses the subagent type and tools.

- `explore`: find, locate, understand, "where is", "which files", "how does X work" (read-only)
- `plan`: design an approach, compare options, produce a step list
- `implement`: change code, data, or config
- `review`: assess and report; no edits
- `answer`: respond from knowledge; almost always inline

## Calibration notes

- If you find yourself explaining what you "really meant" by a score, that explanation is the score you should have written.
- Score the task as posed, then adjust for what you can see in the repo. A rename in a repo with no tests is V0 not V1; a "small change" to `auth/` is K2 not K1.
- When two people would disagree about a number, mark it `?`. The router's rounding is the tie-break, and the log will tell you later whether the rounding was right.
