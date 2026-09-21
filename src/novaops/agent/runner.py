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

import uuid
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from novaops import config
from novaops.agent.graph import build_graph, sqlite_checkpointer
from novaops.agent.policy import message_text
from novaops.agent.scopes import MAYA, get_scope
from novaops.contracts.common import Status
from novaops.identity import Caller
from novaops.mcp_server.context import CALLER_HEADER
from novaops.observability import tracing

#: Distinguishes one process run from the next, so a thread id built from a case id is
#: unique per run. Case ids repeat across runs by design — `M-I-01` is `M-I-01` every
#: time — and a repeated thread id replays the previous run's history.
_RUN_STAMP = uuid.uuid4().hex[:8]


def new_thread_id(case_id: str | None = None) -> str:
    """A thread id no previous run has used.

    The case id is kept as a prefix so a Langfuse session is still recognisable by eye
    when matching traces to `SUBMISSION.md`; the stamp is what makes it fresh. See
    ADR-018 — reusing a thread id across runs is the failure that reads as a broken
    loadout and is actually stale conversation history.
    """

    return f"{case_id}:{_RUN_STAMP}" if case_id else f"thread-{uuid.uuid4().hex[:12]}"


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
    state: dict[str, Any] = field(default_factory=dict)


async def load_tools(caller: Caller, url: str | None = None) -> list:
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
                "headers": {CALLER_HEADER: caller.employee_id},
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


def _terminal_status(answer: str, gate_released: list[str]) -> str:
    """The run's terminal state.

    Deliberately conservative and deliberately dumb for now: Phase 2 only has to end
    every run in exactly one `Status`. Maya and Webex each refine this in their own
    phase, where there is a business outcome to distinguish `blocked` from `completed`.
    """

    if not answer.strip():
        return Status.FAILED.value
    return Status.COMPLETED.value


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
) -> TurnResult:
    """Run one turn and return what it produced.

    `tools` and `checkpointer` are optional so a caller replaying a multi-turn session
    can open them once and reuse them across turns — which is what preserving state
    *within* a session means in practice.

    `expect_fresh=True` asserts that this thread has no history yet, and raises if it
    does. Pass it on the first turn of every independent case: a case id repeats across
    runs, so a thread id built from one silently replays the previous run unless it also
    carries a run stamp. Use `new_thread_id(case_id)` to get one that does. ADR-018.
    """

    get_scope(scope)  # refuse an unknown scope before anything else happens
    request_id = request_id or tracing.new_request_id()

    if tools is None:
        tools = await load_tools(caller)

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
            status = _terminal_status(final, released)
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
                request_id=request_id,
                state=result,
            )

    if checkpointer is not None:
        return await _run(checkpointer)

    async with sqlite_checkpointer() as saver:
        return await _run(saver)
