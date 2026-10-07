"""Score the 27 submitted traces with deterministic checks, and put the result on each trace.

    SUBMISSION.md trace ids -> read each trace back from Langfuse -> check it against
    evals/submission_expectations.py -> post `binding_checks` + `turn_clean` on the trace
    -> write docs/SUBMISSION-SCORES.md

The specification: "Score runs with deterministic checks first ... then register LLM
judges for what code cannot check." The judges score the submission traces as they
arrive. This adds the deterministic half to the SAME traces, after the fact, without
running the agent again — `EVALUATION-INPUTS.yaml` is run once (ADR-026).

It reads what the trace recorded, not what the runner printed: the answer is the turn
span's output, the tools are its TOOL observations, and the sources are the document
paths inside those tool results — what actually reached the model, which is what
`forbidden_sources` and `expected_sources` are about.

Re-runnable: each score has a fixed id, so scoring again replaces rather than adds.

    python -m scripts.score_submission --dry-run   # print the results, post nothing
    python -m scripts.score_submission             # post scores, write the report
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.scoring import TurnScore, score_turn
from evals.submission_expectations import expectation_for

from novaops import config  # noqa: F401 — loads .env
from novaops.observability import tracing

REPORT = Path("docs/SUBMISSION-SCORES.md")
JUDGES = ("citation_faithfulness", "no_false_grant")

_ROW = re.compile(r"^\| `([A-Z]-[A-Z]-\d\d)` \| (—|\d+) \| ([0-9a-f]{32}) \|$", re.M)
_DOCUMENT = re.compile(r"[\w/-]+\.md")


@dataclass
class Row:
    item: str
    turn: int | None
    trace_id: str


@dataclass
class Result:
    row: Row
    score: TurnScore
    origin: str
    judges: dict[str, float]


def index_rows(text: str) -> list[Row]:
    """Every filled row of the SUBMISSION.md trace index — the committed source of truth."""

    return [
        Row(item, None if turn == "—" else int(turn), trace_id)
        for item, turn, trace_id in _ROW.findall(text)
    ]


def read_trace(trace_id: str) -> dict[str, Any]:
    """The answer, tool sequence, sources and judge scores one trace recorded."""

    from langfuse import get_client

    trace = get_client().api.trace.get(trace_id)
    observations = sorted(trace.observations, key=lambda o: o.start_time)
    root = next(o for o in observations if o.type == "AGENT" and o.parent_observation_id is None)
    tools = [o for o in observations if o.type == "TOOL"]

    sources: list[str] = []
    for tool in tools:
        for path in _DOCUMENT.findall(json.dumps(tool.output, ensure_ascii=False)):
            if path not in sources:
                sources.append(path)

    metadata = root.metadata or {}
    return {
        "answer": root.output if isinstance(root.output, str) else json.dumps(root.output),
        "tools": [t.name for t in tools],
        "sources": sources,
        "intent": str(metadata.get("intent") or ""),
        "judges": {s.name: s.value for s in (trace.scores or []) if s.name in JUDGES},
    }


def check(row: Row) -> Result:
    expectation = expectation_for(row.item, row.turn)
    recorded = read_trace(row.trace_id)
    score = score_turn(
        expectation.turn,
        answer=recorded["answer"],
        tools_used=recorded["tools"],
        sources=recorded["sources"],
        intent=recorded["intent"],
    )
    return Result(row, score, expectation.origin, recorded["judges"])


def publish(result: Result) -> None:
    """Put the deterministic result on the trace, beside the judges' scores."""

    score = result.score
    passed = len(score.checks) - len(score.failures)
    failures = "; ".join(f"{c.field}: {c.expectation} ({c.detail})" for c in score.failures)
    comment = f"{failures or 'all binding checks passed'} — expectations: {result.origin}"
    trace_id = result.row.trace_id
    tracing.score_trace(
        trace_id, "binding_checks", passed / (len(score.checks) or 1),
        comment=comment, data_type="NUMERIC", score_id=f"{trace_id}-binding_checks",
    )
    tracing.score_trace(
        trace_id, "turn_clean", "pass" if score.passed else "fail",
        comment=comment, data_type="CATEGORICAL", score_id=f"{trace_id}-turn_clean",
    )


def report(results: list[Result]) -> str:
    """The same results as a page, so they can be read without opening Langfuse."""

    checks = sum(len(r.score.checks) for r in results)
    passed = checks - sum(len(r.score.failures) for r in results)
    clean = sum(r.score.passed for r in results)

    def mean(name: str) -> str:
        values = [r.judges[name] for r in results if name in r.judges]
        return f"{sum(values) / len(values):.2f} over {len(values)}" if values else "—"

    lines = [
        "# Submission scores",
        "",
        "The 27 traces in `SUBMISSION.md`, read back from Langfuse and checked by "
        "`scripts/score_submission.py` against `evals/submission_expectations.py`. "
        "Generated — do not edit by hand. The same scores are on each trace as "
        "`binding_checks` and `turn_clean`, beside the LLM judges' scores.",
        "",
        f"- **Binding checks:** {passed} / {checks} passed",
        f"- **Clean turns:** {clean} / {len(results)}",
        f"- **Judge `no_false_grant`:** {mean('no_false_grant')}",
        f"- **Judge `citation_faithfulness`:** {mean('citation_faithfulness')}",
        "",
        "| Item | Turn | Checks | Judges (cite / grant) | Failed checks |",
        "| ---- | ---- | ------ | --------------------- | ------------- |",
    ]
    for r in results:
        # A literal "|" — the alternatives separator in an expectation — would split the
        # table cell, so it is escaped.
        failed = "<br>".join(f"`{c.field}`: {c.expectation} — {c.detail}"
                             for c in r.score.failures).replace("|", "\\|") or "—"
        cite = r.judges.get("citation_faithfulness")
        grant = r.judges.get("no_false_grant")
        judged = " / ".join("—" if v is None else f"{v:.1f}" for v in (cite, grant))
        lines.append(
            f"| `{r.row.item}` | {r.row.turn if r.row.turn is not None else '—'} | "
            f"{len(r.score.checks) - len(r.score.failures)}/{len(r.score.checks)} | "
            f"{judged} | {failed} |"
        )
    lines += ["", "Where each item's expectations come from:", ""]
    seen: set[str] = set()
    for r in results:
        if r.row.item not in seen:
            seen.add(r.row.item)
            origin = r.origin.split(" turn ")[0]
            lines.append(f"- `{r.row.item}` — {origin}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score the submitted traces.")
    parser.add_argument("--dry-run", action="store_true", help="print results, post nothing")
    args = parser.parse_args(argv)

    if not tracing.is_configured():
        raise SystemExit("Langfuse is not configured; there are no traces to read.")

    rows = index_rows(Path("SUBMISSION.md").read_text(encoding="utf-8"))
    results = [check(row) for row in rows]
    for r in results:
        mark = "ok  " if r.score.passed else "FAIL"
        turn = r.row.turn if r.row.turn is not None else "—"
        print(f"{mark} {r.row.item} {turn!s:>2}  "
              f"{len(r.score.checks) - len(r.score.failures)}/{len(r.score.checks)}  "
              + "; ".join(f"{c.field}: {c.expectation}" for c in r.score.failures))

    if args.dry_run:
        return 0
    for r in results:
        publish(r)
    REPORT.write_text(report(results), encoding="utf-8")
    print(f"\nposted binding_checks + turn_clean on {len(results)} traces; wrote {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
