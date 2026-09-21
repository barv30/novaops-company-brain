"""Replay a golden session against the live agent and score it.

    python -m scripts.replay S1              # the smoke test
    python -m scripts.replay S2 --verbose    # every check, not only failures
    python -m scripts.replay S2 --turns 1-4  # a slice, while iterating on one turn

A session is **one thread on one database**, replayed in order — that is what makes it a
session rather than a sequence of one-shot calls, and replaying it any other way is a
different test. The thread id is fresh per run (ADR-018), and the database is reseeded
before the first turn.

Requires the tool server:  `python -m novaops.mcp_server`
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from evals import golden, scoring

from novaops import config
from novaops.agent import runner
from novaops.agent.graph import sqlite_checkpointer
from novaops.agent.scopes import MAYA, WEBEX
from novaops.db import repositories
from novaops.db.connection import connect
from novaops.identity import build_caller
from novaops.observability import tracing
from scripts.reseed import reseed

#: Which scope a session runs under. Maya and Webex are two scopes on one agent, and the
#: caller's workflow — not the message — decides which authority a session carries.
SESSION_SCOPE = {
    "S6-boundary-clean": WEBEX,
    "S7-boundary-noisy": WEBEX,
    "S8-boundary-heavy-noise": WEBEX,
}


def _publish(trace_id: str | None, turn_score: scoring.TurnScore) -> None:
    """Put this turn's deterministic result on its own trace.

    Lesson 11 asks for runs scored with deterministic checks first. A trace carrying its
    score is readable without the repository open: a marker sees the number beside the
    run, and the comment names which expectation failed rather than leaving them to
    reconstruct it.
    """

    if not trace_id:
        return

    passed = len(turn_score.checks) - len(turn_score.failures)
    total = len(turn_score.checks) or 1
    failures = "; ".join(f"{c.field}: {c.expectation}" for c in turn_score.failures)

    tracing.score_trace(
        trace_id,
        "binding_checks",
        passed / total,
        comment=failures or "all binding checks passed",
        data_type="NUMERIC",
    )
    tracing.score_trace(
        trace_id,
        "turn_clean",
        "pass" if turn_score.passed else "fail",
        comment=failures or None,
        data_type="CATEGORICAL",
    )


def _parse_turns(spec: str | None, total: int) -> set[int]:
    if not spec:
        return set(range(1, total + 1))
    wanted: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            wanted.update(range(int(lo), int(hi) + 1))
        elif part:
            wanted.add(int(part))
    return wanted


async def replay(
    session_id: str,
    *,
    verbose: bool = False,
    turns: str | None = None,
    keep_db: bool = False,
) -> scoring.SessionScore:
    session = golden.session(session_id)
    wanted = _parse_turns(turns, len(session.turns))

    if not keep_db:
        reseed()

    connection = connect()
    try:
        caller = build_caller(session.caller_id, repositories.all_employees(connection))
    finally:
        connection.close()

    scope = SESSION_SCOPE.get(session.id, MAYA)
    thread = runner.new_thread_id(session.id)

    print(f"\n{session.id}")
    print(f"  caller {caller.employee_id} ({caller.user_group}, manager={caller.is_manager})")
    print(f"  scope  {scope}")
    print(f"  thread {thread}")
    if session.focus:
        print(f"  focus  {session.focus[:150]}")

    tools = await runner.load_tools(caller)
    result = scoring.SessionScore(session_id=session.id)

    async with sqlite_checkpointer() as saver:
        for turn in session.turns:
            if turn.n not in wanted:
                continue
            print(f"\n  --- turn {turn.n} ---")
            print(f"  > {' '.join(turn.user.split())[:180]}")

            outcome = await runner.answer(
                turn.user,
                caller=caller,
                thread_id=thread,
                scope=scope,
                tools=tools,
                checkpointer=saver,
                turn=turn.n,
                case_id=session.id,
                expect_fresh=(turn.n == min(wanted)),
            )
            print(f"  < {' '.join(outcome.answer.split())[:300]}")

            if turn.binds_anything:
                turn_score = scoring.score_turn(
                    turn,
                    answer=outcome.answer,
                    tools_used=outcome.tools_used,
                    sources=outcome.sources,
                    intent=outcome.intent,
                    requires_tools=(outcome.state.get("plan") or {}).get("requires_tools"),
                    rearmed=bool(outcome.state.get("rearmed")),
                )
                result.turns.append(turn_score)
                _publish(outcome.trace_id, turn_score)

    print(result.report(verbose=verbose))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", help="session id or prefix, e.g. S2")
    parser.add_argument("--verbose", "-v", action="store_true", help="show passing checks too")
    parser.add_argument("--turns", help="a slice, e.g. '1-4' or '7,9,12'")
    parser.add_argument(
        "--keep-db", action="store_true",
        help="do not reseed first (only for a deliberate idempotency check)",
    )
    args = parser.parse_args(argv)

    if not (config.INDEX_DIR / "corpus.faiss").is_file():
        print("No FAISS index. Run `python -m scripts.ingest` first.", file=sys.stderr)
        return 2

    score = asyncio.run(
        replay(args.session, verbose=args.verbose, turns=args.turns, keep_db=args.keep_db)
    )
    return 0 if score.passed else 1


if __name__ == "__main__":
    sys.exit(main())
