"""What a turn DID, copied onto the turn's span so a Langfuse judge can read it.

A Langfuse evaluation rule judges ONE observation and reads only that observation's own
fields. A turn's tool calls are recorded as observations *beside* the turn span, not
inside it, so a judge pointed at the turn cannot see them. Anything a judge must reason
about — what the tools returned, what earlier turns established, what the write gate
released — has to be put on the span it judges. Lesson 11 `02-instrument-agent/run.py`
`trajectory_digest`, adapted.

**Trade-off:** this duplicates evidence the trace already holds in full, and truncates
it. The deterministic scorer in `evals/` reads the untruncated originals and needs none
of this; the digest exists only for the judges, which run on Langfuse's servers.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from novaops.agent.policy import message_text

#: A tool result is often a policy or a record set, and a faithfulness judge has to
#: check claims against its substance. Lesson 10 and 11 truncate at the same 1500.
TOOL_RESULT_CHARS = 1500


def trajectory_digest(
    turn_messages: list,
    state: dict,
    *,
    status: str,
    released: list[str],
) -> dict[str, Any]:
    """The calls, the carried context, and the write outcome of one turn.

    `turn_messages` must be THIS turn's slice only; the whole thread would hand the judge
    tool results from earlier turns as if they were fresh evidence.
    """

    calls: dict[str, dict] = {}
    order: list[str] = []
    for message in turn_messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls or []:
                calls[call["id"]] = {"tool": call["name"], "args": call["args"], "result": None}
                order.append(call["id"])
        elif isinstance(message, ToolMessage):
            entry = calls.get(message.tool_call_id)
            if entry is not None:
                text = message_text(message)
                entry["result"] = (
                    text[:TOOL_RESULT_CHARS] + " …[truncated]"
                    if len(text) > TOOL_RESULT_CHARS
                    else text
                )

    plan = state.get("plan") or {}
    memory = state.get("memory") or {}
    return {
        "intent": plan.get("current_intent"),
        "requires_tools": plan.get("requires_tools"),
        "calls": [calls[key] for key in order],
        # What earlier turns established. A rule the USER stated has no tool result
        # behind it; without this, a correct answer that repeats it reads as invented.
        "state": {
            "facts": plan.get("relevant_facts") or [],
            "constraints": plan.get("relevant_constraints") or [],
            "earlier_decisions": memory.get("decisions") or [],
        },
        # What APPLICATION CODE recorded about writes — the only ground truth for
        # "was access granted?". The no-false-grant judge compares the prose to this.
        "writes": {
            "scope": state.get("scope"),
            "status": status,
            "write_tools_released": released,
            "gate_reason": state.get("gate_reason") or "",
            "handoff_prepared_not_filed": bool(state.get("handoff")),
        },
    }
