"""Replay a session N times and report which turns are broken and which are noisy.

    python -m scripts.variance S2 --runs 5

A single replay cannot tell those apart, and reacting to one run is how you end up
tuning noise. A turn that fails every run is a defect; a turn that fails two runs in five
is a stability problem, and the two need opposite responses — the first wants a fix, the
second wants a more determinate mechanism rather than more prompt text.

This exists because it was needed: `S2` measured 38, 41, 38, 41 across consecutive runs
with different turns failing each time, and three prompt "improvements" in a row turned
out to be indistinguishable from chance.

Requires the tool server:  `python -m novaops.mcp_server`
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
from collections import defaultdict

from evals import golden

from novaops import config
from scripts.replay import replay


async def measure(session_id: str, runs: int) -> dict:
    session = golden.session(session_id)
    per_turn_pass: dict[int, list[bool]] = defaultdict(list)
    per_check_fail: dict[tuple[int, str, str], int] = defaultdict(int)
    totals: list[tuple[int, int]] = []

    for run in range(1, runs + 1):
        print(f"\n{'=' * 70}\nRUN {run} of {runs}\n{'=' * 70}")
        score = await replay(session_id, verbose=False)
        totals.append(score.totals)
        for turn in score.turns:
            per_turn_pass[turn.turn].append(turn.passed)
            for check in turn.failures:
                per_check_fail[(turn.turn, check.field, check.expectation)] += 1

    return {
        "session": session.id,
        "runs": runs,
        "totals": totals,
        "per_turn": per_turn_pass,
        "per_check": per_check_fail,
    }


def report(data: dict) -> None:
    runs = data["runs"]
    totals = data["totals"]
    scores = [ok for ok, _ in totals]
    out_of = totals[0][1] if totals else 0

    print(f"\n\n{'=' * 70}\nVARIANCE  {data['session']}  over {runs} runs\n{'=' * 70}")
    print(f"\nchecks passed: {scores}  of {out_of}")
    if len(scores) > 1:
        print(f"  best {max(scores)}   worst {min(scores)}   "
              f"mean {statistics.mean(scores):.1f}   spread {max(scores) - min(scores)}")

    print(f"\n{'turn':>5}  {'passed':>8}  verdict")
    broken, flaky, solid = [], [], []
    for turn in sorted(data["per_turn"]):
        results = data["per_turn"][turn]
        ok = sum(results)
        rate = ok / len(results)
        if rate == 1.0:
            verdict, bucket = "solid", solid
        elif rate == 0.0:
            verdict, bucket = "BROKEN — fails every run", broken
        else:
            verdict, bucket = f"FLAKY — {ok}/{len(results)}", flaky
        bucket.append(turn)
        print(f"{turn:>5}  {ok:>3}/{len(results):<4}  {verdict}")

    print(f"\n  solid  : {solid or '-'}")
    print(f"  flaky  : {flaky or '-'}   <- stability, not correctness; more prompt text "
          f"will not settle these")
    print(f"  broken : {broken or '-'}   <- real defects; fix these first")

    if data["per_check"]:
        print(f"\n{'fails':>6}  turn  check")
        for (turn, kind, expectation), count in sorted(
            data["per_check"].items(), key=lambda kv: (-kv[1], kv[0])
        ):
            print(f"{count:>3}/{runs:<3}  {turn:>4}  {kind}: {expectation[:60]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", help="session id or prefix, e.g. S2")
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args(argv)

    if not (config.INDEX_DIR / "corpus.faiss").is_file():
        print("No FAISS index. Run `python -m scripts.ingest` first.", file=sys.stderr)
        return 2

    report(asyncio.run(measure(args.session, args.runs)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
