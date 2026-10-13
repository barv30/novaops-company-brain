"""The callable entry point.

The specification requires core logic to be "callable directly, not reachable only
through a UI", and requires a CLI or web chat — if one exists — to pass caller context
and a thread id into **the same core functions the tests call**, never a second path
into the workflow. `answer()` is that function. Tests call it, scripts call it, and any
interface we build later calls it too.

It owns the three things a turn needs that the graph itself should not know about:

  * **the MCP session**, opened with this caller's badge, so tools resolve identity from
    the transport rather than from anything the model produced;
  * **the trace**, so one turn is one span tree with the caller, scope, tool sequence
    and terminal status attached;
  * **the checkpointer**, so a thread's state survives the process.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from novaops import config
from novaops.agent.digest import trajectory_digest
from novaops.agent.graph import build_graph, sqlite_checkpointer
from novaops.agent.policy import message_text
from novaops.agent.scopes import MAYA, get_scope
from novaops.contracts.common import Status
from novaops.identity import Caller
from novaops.mcp_server.context import CALLER_HEADER, REQUEST_HEADER
from novaops.observability import tracing


def new_thread_id(case_id: str | None = None) -> str:
    """A thread id no previous run — and no previous call — has used.

    The case id is kept as a prefix so a Langfuse session is still recognisable by eye
    when matching traces to `SUBMISSION.md`; the random suffix is what makes it fresh.
    See ADR-018 — reusing a thread id is the failure that reads as a broken loadout and
    is actually stale conversation history.

    The suffix is drawn per CALL. It used to be one stamp per process, which kept ids
    fresh across runs but not within one: `scripts/variance.py` replays a session three
    times in one process, and its second run got the first run's thread. The freshness
    guard refused it (ThreadNotFreshError) rather than replaying — which is how it was
    found — but a per-process stamp was the wrong unit.
    """

    return f"{case_id}:{uuid.uuid4().hex[:8]}" if case_id else f"thread-{uuid.uuid4().hex[:12]}"


class ThreadNotFreshError(RuntimeError):
    """Raised when a turn declared as a new case lands on a thread that already has one."""


@dataclass
class TurnResult:
    """What one turn produced, in the form the evaluation harness reads."""

    answer: str
    status: str
    scope: str
    intent: str
    tools_used: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    gate_reason: str = ""
    gate_released: list[str] = field(default_factory=list)
    trace_id: str | None = None
    request_id: str = ""
    handoff: dict | None = None
    state: dict[str, Any] = field(default_factory=dict)


async def load_tools(
    caller: Caller, url: str | None = None, *, correlation_id: str | None = None
) -> list:
    """Open an MCP session carrying this caller's badge, and discover the tools.

    The session is per caller, which is the whole point: the header is set once, here,
    by application code, and every tool call made through these tool objects carries it.
    There is no argument anywhere that could carry a different identity.
    """

    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(
        {
            "novaops": {
                "url": url or config.MCP_SERVER_URL,
                "transport": "streamable_http",
                "headers": {
                    CALLER_HEADER: caller.employee_id,
                    **({REQUEST_HEADER: correlation_id} if correlation_id else {}),
                },
            }
        }
    )
    return await client.get_tools()


def _this_turn(messages: list) -> list:
    """Only the messages produced since the user's newest message.

    `messages` is the whole thread — the accumulated history, not this turn's slice.
    Reading it whole reports every tool ever called on the thread as if it had run now,
    which makes a turn that correctly used no tools look like one that used two. The
    per-turn expectations in `GOLDEN-DATASETS.json` are per turn, so this is where the
    turn boundary has to be drawn.
    """

    boundaries = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
    return messages[boundaries[-1]:] if boundaries else list(messages)


def _tools_used(messages: list) -> list[str]:
    """The tool names that ran this turn, in order."""

    return [m.name for m in _this_turn(messages) if isinstance(m, ToolMessage) and m.name]


def _sources(messages: list) -> list[str]:
    """Document paths that reached the model this turn.

    Used for the `expected_sources` and `forbidden_sources` checks, which are
    retrieval-layer expectations: what *arrived*, not what the answer chose to cite.
    """

    import re

    found: list[str] = []
    for message in _this_turn(messages):
        if isinstance(message, ToolMessage):
            for match in re.findall(r"[\w/-]+\.md", message_text(message)):
                if match not in found:
                    found.append(match)
    return found


#: Least to most severe. A turn adopts the worst status any workflow it consulted
#: reported: a run that reached a blocker and also answered a trivial question is
#: blocked, not completed.
_SEVERITY = [
    Status.COMPLETED,
    Status.PENDING,
    Status.NEEDS_HUMAN,
    Status.BLOCKED,
    Status.FAILED,
]


def _terminal_status(messages: list, answer: str) -> str:
    """The run's terminal state, taken from the workflow rather than guessed.

    `OnboardingChecklist` and `AccessDecision` each carry a `status`, and a tool that
    returns one is reporting where the work actually got to. Deriving the turn's status
    from the answer text instead would mean the trace says `completed` while the typed
    object says `blocked` — and the specification is explicit that the status is what a
    marker reads first, and that the prose must agree with it.

    Rachel Stein's case is the one this exists for: eligible, at 42 of 40 seats, nothing
    filed. `blocked` is the correct and creditable answer; `completed` would be a
    confident wrong one.
    """

    if not answer.strip():
        return Status.FAILED.value

    reported = [
        status
        for message in _this_turn(messages)
        if isinstance(message, ToolMessage)
        for status in (_top_level_status(message_text(message)),)
        if status is not None
    ]

    if not reported:
        return Status.COMPLETED.value
    return max(reported, key=_SEVERITY.index).value


def _top_level_status(payload: str) -> Status | None:
    """The `status` a tool result declares for *itself*, or None.

    Parsed, not matched. An `OnboardingChecklist` is `completed` while containing items
    whose own status is `blocked` — that is the contract's design, because the blocked
    items are the answer rather than a failure to produce one. A scan for the string
    `"status": "blocked"` finds those nested item statuses and reports the whole turn as
    blocked, which is how the first version of this function turned every Maya
    onboarding turn into a false blocker.
    """

    import json

    try:
        parsed = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None

    value = parsed.get("status")
    try:
        return Status(value)
    except ValueError:
        return None


def approve(
    *,
    approval_id: str,
    decided_by: str,
    approved: bool,
    reason: str,
    open_db=None,
) -> dict:
    """Record a human's decision on an approval. Application code only.

    This is the other half of the write gate, and the reason it is a plain function on
    the runner rather than a tool on the server: it is the one thing that can open the
    gate, so it must sit somewhere a model cannot reach. The caller supplies `approved`
    from a person's actual response — an `interrupt()` resume, a CLI prompt, a test —
    never from anything a model produced. See ADR-014.

    Idempotent: recording the same decision twice is a no-op, because a resume after a
    restart legitimately replays it.
    """

    from novaops.db import writes
    from novaops.db.connection import connect as default_connect

    opener = open_db or default_connect
    connection = opener()
    try:
        result = writes.record_decision(
            connection, approval_id=approval_id, decided_by=decided_by,
            approved=approved, reason=reason,
        )
        connection.commit()
    finally:
        connection.close()

    return {
        "approval_id": result.entity_id,
        "status": result.row.get("status"),
        "disposition": result.disposition,
    }


async def answer(
    message: str,
    *,
    caller: Caller,
    thread_id: str,
    scope: str = MAYA,
    tools: list | None = None,
    checkpointer=None,
    open_db=None,
    request_id: str | None = None,
    turn: int | None = None,
    case_id: str | None = None,
    expect_fresh: bool = False,
    pending_request_id: str | None = None,
) -> TurnResult:
    """Run one turn and return what it produced.

    `tools` and `checkpointer` are optional so a caller replaying a multi-turn session
    can open them once and reuse them across turns — which is what preserving state
    *within* a session means in practice.

    `pending_request_id` names the business request this turn concerns — the thing the
    write gate checks approvals against. It is an application input for the same reason
    the caller is: if the model could name the request, it could name one that happens
    to be approved. Absent, the gate has nothing to check and releases nothing.

    `expect_fresh=True` asserts that this thread has no history yet, and raises if it
    does. Pass it on the first turn of every independent case: a case id repeats across
    runs, so a thread id built from one silently replays the previous run unless it also
    carries a run stamp. Use `new_thread_id(case_id)` to get one that does. ADR-018.
    """

    get_scope(scope)  # refuse an unknown scope before anything else happens
    request_id = request_id or tracing.new_request_id()

    if tools is None:
        tools = await load_tools(caller, correlation_id=request_id)

    async def _run(saver) -> TurnResult:
        app = build_graph(tools, saver, open_db=open_db)
        graph_config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}

        if expect_fresh:
            existing = await app.aget_state(graph_config)
            if (existing.values or {}).get("messages"):
                raise ThreadNotFreshError(
                    f"Thread {thread_id!r} already holds "
                    f"{len(existing.values['messages'])} messages, but this turn was "
                    "declared as the start of an independent case. It would replay the "
                    "previous run's history: the planner sees the question already "
                    "answered, sets requires_tools=False, and the turn uses no tools. "
                    "Use new_thread_id(case_id), or reset with "
                    "`python -m scripts.reseed --checkpoints`."
                )

        # A distinctive name per trace. Every trace was previously called "maya.turn",
        # so a marker matching twenty-seven rows of SUBMISSION.md against the project saw
        # twenty identically-named runs and had to open each one. "M-S-01 t9 · maya"
        # identifies itself in the list.
        label = " · ".join(
            part for part in (case_id, f"t{turn}" if turn is not None else None, scope)
            if part
        )
        with tracing.turn_span(
            name=label or f"{scope}.turn",
            caller_id=caller.employee_id,
            user_group=caller.user_group,
            is_manager=caller.is_manager,
            scope=scope,
            thread_id=thread_id,
            request_id=request_id,
            turn=turn,
            case_id=case_id,
            user_input=message,
        ) as span:
            result = await app.ainvoke(
                {
                    "messages": [HumanMessage(message)],
                    "caller_id": caller.employee_id,
                    "user_group": caller.user_group,
                    "is_manager": caller.is_manager,
                    "scope": scope,
                    "thread_id": thread_id,
                    "request_id": request_id,
                    **({"pending_request_id": pending_request_id}
                       if pending_request_id else {}),
                },
                config=tracing.bind_callbacks(graph_config),
            )

            messages = result.get("messages", [])
            final = next(
                (
                    message_text(m)
                    for m in reversed(messages)
                    if isinstance(m, AIMessage) and message_text(m).strip()
                ),
                "",
            )
            used = _tools_used(messages)
            released = list(result.get("gate_released") or [])
            status = _terminal_status(messages, final)
            intent = (result.get("plan") or {}).get("current_intent", "other")

            span.update(
                output=final,
                metadata={
                    "status": status,
                    "intent": intent,
                    "tool_sequence": ", ".join(used) or "(none)",
                    "loadout_size": len(result.get("loadout") or []),
                    "gate_released": ", ".join(released) or "(nothing)",
                    "gate_reason": result.get("gate_reason", ""),
                    "handoff": json.dumps(result.get("handoff")) if result.get("handoff")
                    else "(none)",
                    "plan_override": result.get("plan_override") or "(none)",
                    # For the LLM judges, which can read only this span. See digest.py.
                    "trajectory": trajectory_digest(
                        _this_turn(messages), result, status=status, released=released
                    ),
                },
            )

            return TurnResult(
                answer=final,
                status=status,
                scope=scope,
                intent=intent,
                tools_used=used,
                sources=_sources(messages),
                gate_reason=result.get("gate_reason", ""),
                gate_released=released,
                trace_id=span.trace_id,
                handoff=result.get("handoff"),
                request_id=request_id,
                state=result,
            )

    if checkpointer is not None:
        return await _run(checkpointer)

    async with sqlite_checkpointer() as saver:
        return await _run(saver)
