# Evaluation checks

Two kinds of thing live here, and the distinction matters.

**Scored against the golden material.** `GOLDEN-DATASETS.json` carries per-turn
expectations for the nine annotated sessions and the single-turn cases. When scoring
against it, only these fields are binding — a mismatch is a real defect:

- `required_facts`, `forbidden_facts`
- `forbidden_tools`, `forbidden_sources`, `expected_sources`
- `should_use_tools`

These describe the course's reference implementation and **will** drift from any correct
system with its own tool decomposition. Map them once, here, to our names — never relax a
binding field to match our behavior:

- `expected_tools`, `optional_tools`, `sequence`, `arg_checks`
- `expected_intent`, `expected_route`

**Checks nothing ships.** The golden material deliberately omits the properties this
project is actually about, and writing them is part of the work. These are read as part of
the submission:

| Check | File | Proves |
| --- | --- | --- |
| Constraint carry | `test_constraint_carry.py` | a constraint stated on turn 1 still binds on turn 9 |
| Closed branch | `test_closed_branch.py` | a dead branch stops driving tool choice |
| Idempotency | `test_idempotency.py` | replaying a request creates no second row |
| Restart survival | `test_restart_survival.py` | a pending approval survives the process dying |

Run with `pytest evals/`.

## Development signal

`S6` and `S8` have identical expectations by design, so the delta between their scores is
a pure measure of robustness to how a request is written rather than to task difficulty.
`S6` is not in the measured set, so it cannot be overfitted. It is the most useful single
number available while developing.

`S1-quick-policy` is the smoke test — four turns, one tool call. If it fails, nothing
downstream is worth debugging yet.

## Running discipline — two kinds of state, and both bite

The specification warns that "a new conversation thread does not reset the database".
There is a second store with the same property, and it is easier to miss.

**The business database** (`var/novaops.db`). Reseed before each independent case or
session: `python -m scripts.reseed`. Several measured cases touch the same records, so
running them back to back on one database lets the first case's write suppress the second.

**The checkpointer** (`var/checkpoints.db`). This holds conversation history per
`thread_id`, and it survives process restarts — that is the point of it. Reuse a thread id
across runs and the planner replays against the previous run's history: it sees the
question already answered, sets `requires_tools=False`, and the turn legitimately uses no
tools. The trace then looks like a broken loadout and is actually stale state.

So, for every independent case:

- reseed the database, and
- use a thread id nobody has used, or drop the checkpointer with
  `python -m scripts.reseed --checkpoints`.

Within a session, do the opposite: one thread, one database, every turn in order. That is
what makes it a session rather than a sequence of one-shot calls.

Tests isolate both — the server fixtures copy the database, and `ask()` in
`tests/test_agent_loop.py` opens an in-memory checkpointer per turn.
