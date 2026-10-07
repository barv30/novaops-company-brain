"""Run the measured evaluation set and record a trace id for every turn.

    python -m scripts.submit --dry-run          # print the 27 planned traces, call nothing
    python -m scripts.submit                    # run all required cases
    python -m scripts.submit --only W-I-02      # one case, for checking a fix
    python -m scripts.submit --only M-S-01 --only W-I-03

This is Milestone 10's run, and `SUBMISSION.md`'s trace index comes from its output.
`scripts/replay.py` does the same job for `GOLDEN-DATASETS.json`, which is practice
material with the answers attached; this reads `EVALUATION-INPUTS.yaml`, which is the
graded set with none.

The specification is specific about how the run must be done, because getting it wrong
produces traces that show the harness's failures rather than the system's:

* **reseed before each independent case or session** — several cases touch the same
  records, and one case's write must not shape the next case's answer;
* **preserve state within a session** — a session is one thread on one database, every
  turn in order, which is the whole point of it;
* **pin the clock to 2026-07-01** — `config.AS_OF`, never the wall clock;
* **one trace per turn** — so the index lines up with the rows of `SUBMISSION.md`.

And one rule of ours (ADR-018): every case gets a thread id nobody has used, because
case ids repeat across runs and a reused thread replays the previous run's history.

Only the required workflows run. Vendor and Renewal are out of scope (ADR-001), and are
listed in the plan as skipped rather than silently dropped.

Requires the tool server:  `python -m novaops.mcp_server`
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from novaops import config
from novaops.agent.scopes import MAYA, WEBEX

#: Which authority each workflow runs under. Maya and Webex are two scopes on one agent;
#: the workflow a case belongs to decides the scope, never the wording of its message.
WORKFLOW_SCOPE = {"maya": MAYA, "webex": WEBEX}

DEFAULT_OUT = config.VAR_DIR / "submission"


@dataclass(frozen=True)
class Turn:
    n: int | None          # None for a one-shot input; SUBMISSION.md shows it as "—"
    message: str


@dataclass(frozen=True)
class Case:
    """One independent unit of the run: a one-shot input, or a whole session."""

    case_id: str
    workflow: str
    scope: str
    caller_id: str
    kind: str              # "input" or "session"
    turns: tuple[Turn, ...]


@dataclass
class TraceRecord:
    """One row of the trace index."""

    case_id: str
    turn: int | None
    trace_id: str | None
    status: str
    scope: str
    caller_id: str
    intent: str = ""
    tools: list[str] = field(default_factory=list)
    gate_reason: str = ""
    answer: str = ""
    error: str = ""

    @property
    def turn_label(self) -> str:
        return "—" if self.turn is None else str(self.turn)


# 1. THE PLAN — no model, no database, testable on its own ---------------------

def load_plan(
    path: Path | None = None, *, only: list[str] | None = None
) -> tuple[list[Case], list[str]]:
    """Turn `EVALUATION-INPUTS.yaml` into an ordered list of cases.

    Returns `(cases, skipped)`. Order follows the file: inputs before sessions within a
    workflow, workflows in file order. `skipped` names what was left out and why, so an
    out-of-scope workflow is visible in the output rather than missing from it.
    """

    raw = yaml.safe_load((path or config.EVALUATION_INPUTS).read_text(encoding="utf-8"))
    wanted = {case_id.upper() for case_id in (only or [])}
    cases: list[Case] = []
    skipped: list[str] = []

    for workflow in raw["workflows"]:
        name = workflow["id"]
        if not workflow.get("required") or name not in WORKFLOW_SCOPE:
            skipped.append(f"{name}: optional workflow, out of scope (ADR-001)")
            continue
        scope = WORKFLOW_SCOPE[name]

        for item in workflow.get("inputs") or []:
            cases.append(
                Case(
                    case_id=item["id"],
                    workflow=name,
                    scope=scope,
                    caller_id=item["caller"]["employee_id"],
                    kind="input",
                    turns=(Turn(None, str(item["message"])),),
                )
            )
        for session in workflow.get("sessions") or []:
            turns = sorted(session["turns"], key=lambda t: int(t["n"]))
            cases.append(
                Case(
                    case_id=session["id"],
                    workflow=name,
                    scope=scope,
                    caller_id=session["caller"]["employee_id"],
                    kind="session",
                    turns=tuple(Turn(int(t["n"]), str(t["message"])) for t in turns),
                )
            )

    if wanted:
        unknown = wanted - {case.case_id for case in cases}
        if unknown:
            raise SystemExit(f"Unknown or out-of-scope case id(s): {sorted(unknown)}")
        cases = [case for case in cases if case.case_id in wanted]

    return cases, skipped


def planned_traces(cases: list[Case]) -> int:
    """One trace per turn. The required set must come to 27."""

    return sum(len(case.turns) for case in cases)


# 2. THE RUN --------------------------------------------------------------------

async def run_case(case: Case, *, url: str | None = None) -> list[TraceRecord]:
    """Run one case on a reseeded database and a thread nobody has used.

    A failure on one turn is recorded and the run continues: a case that errors is a
    `failed` row in the index, which is a truthful result, whereas stopping the whole run
    would lose the other twenty-six.
    """

    from novaops.agent import runner
    from novaops.agent.graph import sqlite_checkpointer
    from novaops.db import repositories
    from novaops.db.connection import connect
    from scripts.reseed import reseed

    reseed()  # before each independent case or session, per the specification

    connection = connect()
    try:
        caller = runner_caller(case.caller_id, repositories.all_employees(connection))
    finally:
        connection.close()

    thread = runner.new_thread_id(case.case_id)
    tools = await runner.load_tools(caller, url=url, correlation_id=thread)
    records: list[TraceRecord] = []

    async with sqlite_checkpointer() as saver:
        for index, turn in enumerate(case.turns):
            try:
                result = await runner.answer(
                    turn.message,
                    caller=caller,
                    thread_id=thread,
                    scope=case.scope,
                    tools=tools,
                    checkpointer=saver,
                    turn=turn.n,
                    case_id=case.case_id,
                    expect_fresh=(index == 0),
                )
                records.append(
                    TraceRecord(
                        case_id=case.case_id, turn=turn.n, trace_id=result.trace_id,
                        status=result.status, scope=case.scope, caller_id=case.caller_id,
                        intent=result.intent, tools=result.tools_used,
                        gate_reason=result.gate_reason, answer=result.answer,
                    )
                )
            except Exception as exc:  # noqa: BLE001 — record it, keep going
                records.append(
                    TraceRecord(
                        case_id=case.case_id, turn=turn.n, trace_id=None, status="failed",
                        scope=case.scope, caller_id=case.caller_id,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
    return records


def runner_caller(employee_id: str, employees: list[dict[str, Any]]):
    """The caller comes from the yaml, never from the message. Invariant 1."""

    from novaops.identity import build_caller

    return build_caller(employee_id, employees)


# 3. OUTPUT ---------------------------------------------------------------------

def write_outputs(records: list[TraceRecord], out_dir: Path) -> tuple[Path, Path]:
    """Write the full index as JSON, and the SUBMISSION.md rows as Markdown."""

    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "trace_index.json"
    md_path = out_dir / "trace_index.md"

    json_path.write_text(
        json.dumps(
            [{**asdict(r), "turn_label": r.turn_label} for r in records],
            indent=2, ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    lines = ["| Item | Turn | Trace ID |", "| ---- | ---- | -------- |"]
    lines += [f"| `{r.case_id}` | {r.turn_label} | {r.trace_id or ''} |" for r in records]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def print_plan(cases: list[Case], skipped: list[str]) -> None:
    print(f"\n{planned_traces(cases)} traces planned across {len(cases)} cases\n")
    for case in cases:
        label = f"{len(case.turns)}-turn session" if case.kind == "session" else "one-shot"
        print(f"  {case.case_id:<8} {case.scope:<6} caller {case.caller_id}  {label}")
    for line in skipped:
        print(f"  skipped  {line}")


def print_summary(records: list[TraceRecord]) -> None:
    print(f"\n{'case':<8} {'turn':>4}  {'status':<10} {'trace':<34} tools")
    for r in records:
        tools = ", ".join(r.tools) or "-"
        print(f"{r.case_id:<8} {r.turn_label:>4}  {r.status:<10} {(r.trace_id or '-'):<34} "
              f"{tools[:60]}")
        if r.error:
            print(f"{'':<14}ERROR {r.error[:120]}")

    missing = [r for r in records if not r.trace_id]
    by_status: dict[str, int] = {}
    for r in records:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    tally = " · ".join(f"{k} {v}" for k, v in sorted(by_status.items()))
    print(f"\n{len(records)} traces · {tally}")
    if missing:
        print(f"WARNING: {len(missing)} turn(s) have no trace id — is Langfuse configured?")


async def run(cases: list[Case], *, url: str | None = None) -> list[TraceRecord]:
    records: list[TraceRecord] = []
    for case in cases:
        print(f"\n--- {case.case_id} ({case.scope}, caller {case.caller_id}, "
              f"{len(case.turns)} turn(s))")
        case_records = await run_case(case, url=url)
        for r in case_records:
            print(f"  turn {r.turn_label:>2}: {r.status:<10} {r.trace_id or r.error[:60]}")
        records.extend(case_records)

    from novaops.observability import tracing

    if tracing.is_configured():
        tracing._client().flush()  # noqa: SLF001 — make sure every trace is sent before exit
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", action="append", help="run one case id (repeatable)")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, call nothing")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="where to write the index")
    parser.add_argument("--url", help="tool server URL, if not the configured one")
    args = parser.parse_args(argv)

    cases, skipped = load_plan(only=args.only)
    print_plan(cases, skipped)
    if args.dry_run:
        return 0

    if not (config.INDEX_DIR / "corpus.faiss").is_file():
        print("No FAISS index. Run `python -m scripts.ingest` first.", file=sys.stderr)
        return 2

    records = asyncio.run(run(cases, url=args.url))
    json_path, md_path = write_outputs(records, args.out)
    print_summary(records)
    print(f"\nindex written to {json_path}\nrows for SUBMISSION.md in {md_path}")
    return 0 if all(r.trace_id for r in records) else 1


if __name__ == "__main__":
    sys.exit(main())
