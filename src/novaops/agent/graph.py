"""The agent loop: one graph, two scopes.

    START ─▶ needs distilling? ─▶ distil ─▶ classify ─▶ scope ─▶ model ─▶ tools
                  │ no                          ▲                  │        │
                  └─────────────────────────────┘                  └────────┘
                                                        (rearm on a missed lookup)

Lesson 10 stage 3's mechanics, with three changes this project requires:

**A durable checkpointer.** `AsyncSqliteSaver`, not `InMemorySaver`. Milestone 4 is done
only when a killed process resumes a pending approval, and an in-memory saver cannot do
that. It is wired from the first commit rather than swapped in later, because approval
state has to be *shaped* for durability, and discovering that late means reshaping it
after the workflows are written.

**A scope node.** Which tools are visible is decided by deterministic code from the
plan's intent and the scope's authority — not by the model, and not by a second router.
`classify` is the router.

**A write gate between scope and model.** It consults the database, never the plan. See
`write_gate.py`.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import (
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from novaops import config
from novaops.agent.policy import (
    BOUNDED_INSTRUCTION,
    REARM_INSTRUCTION,
    TRUTHFULNESS_CORRECTION,
    ConversationMemory,
    TurnPlan,
    answer_prompt,
    conversation_window,
    distiller_messages,
    memory_is_safe,
    narrates_untaken_action,
    needs_distillation,
    override_requires_tools,
    planner_messages,
    read_tools,
    record_subject,
    render_caller,
    render_gate,
    render_handoff,
    render_memory_for_answer,
    render_plan,
    render_subjects,
)
from novaops.agent.scopes import get_scope
from novaops.agent.state import AgentState
from novaops.agent.write_gate import (
    GateDecision,
    pending_request_for,
    release_writes,
)
from novaops.llm.client import get_model
from novaops.workflows.handoff import prepare_handoff


def _tools_this_turn(state: AgentState) -> list[str]:
    """Names of the tools that actually ran since the user's newest message."""

    messages = state.get("messages") or []
    boundaries = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
    current = messages[boundaries[-1]:] if boundaries else messages
    return [m.name for m in current if isinstance(m, ToolMessage) and m.name]


def used_tool_this_turn(messages: list) -> bool:
    """Did any tool run since the user's last message?"""

    boundaries = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
    current = messages[boundaries[-1]:] if boundaries else messages
    return any(isinstance(m, ToolMessage) for m in current)


def build_graph(
    tools: list,
    checkpointer,
    *,
    open_db=None,
):
    """Compile the agent.

    `tools` are the MCP tool objects the agent may ever use; `open_db` returns a
    connection for the write gate to consult. Both are injected rather than imported, so
    the graph has no opinion about where either comes from — and so a test can hand it a
    temporary database without monkeypatching.
    """

    if open_db is None:
        from novaops.db.connection import connect as open_db  # noqa: N813

    base_model = get_model()
    by_name = {tool.name: tool for tool in tools}
    catalogue = sorted(by_name)

    planner = get_model().with_structured_output(TurnPlan, include_raw=True)
    distiller = get_model().with_structured_output(ConversationMemory, include_raw=True)

    bound_cache: dict[tuple, Any] = {}

    def model_for(names: list[str], *, force: bool = False):
        """Bind a tool subset once per distinct subset. Binding is not free.

        `force` binds with `tool_choice="any"`, which obliges the model to call one of
        the offered tools rather than answering. It is used on a rearm only — see the
        `rearm` node for why asking a second time politely does not work.
        """

        key = (tuple(sorted(names)), force)
        if key not in bound_cache:
            selected = [by_name[n] for n in key[0]]
            if not selected:
                bound_cache[key] = base_model
            elif force:
                bound_cache[key] = base_model.bind_tools(selected, tool_choice="any")
            else:
                bound_cache[key] = base_model.bind_tools(selected)
        return bound_cache[key]

    # 1. DISTIL -------------------------------------------------------------
    async def distil(state: AgentState) -> dict:
        """Compress history into structured memory once it grows past the threshold.

        Never deletes a message. Distillation changes what gets *sent*, and the raw
        history stays in state — so a summary that loses something costs tokens on the
        next turn rather than costing the evidence.
        """

        result = await distiller.ainvoke(distiller_messages(state))
        updated = result["parsed"]
        if updated is None or not memory_is_safe(state.get("memory") or {}, updated):
            return {}  # keep the old memory AND the raw history
        return {
            "memory": updated.model_dump(),
            "distilled_upto": len(state["messages"]),
        }

    # 2. CLASSIFY — this is the router --------------------------------------
    async def classify(state: AgentState) -> dict:
        result = await planner.ainvoke(planner_messages(state))
        plan = result["parsed"] or TurnPlan(
            current_intent="other", requires_tools=True, asks_record_state=False
        )
        override = override_requires_tools(plan, state["messages"])
        if override:
            plan = plan.model_copy(update={"requires_tools": True})
        return {
            "plan": plan.model_dump(),
            "plan_override": override,
            "subjects": record_subject(state.get("subjects") or [], plan, state["messages"]),
        }

    # 3. SCOPE — deterministic; the only node that spends no model call ------
    def scope(state: AgentState) -> dict:
        """Decide which tools this turn may see, and whether a write is released.

        Two separate decisions, deliberately adjacent so the difference is visible:
        `visible_reads` may reflect what the model inferred, `release_writes` may not.
        """

        plan = TurnPlan(**state["plan"])
        active = get_scope(state.get("scope", "maya"))

        # `requires_tools` alone decides whether ANY tool is visible. `action_confirmed`
        # is deliberately not consulted here, though Lesson 10 consulted it: there it
        # gated write visibility, and in this design writes come only from the gate
        # below, so it has no remaining job in the read loadout.
        #
        # Leaving it in had a measurable cost. On S2 turn 10 — "go ahead and file it" —
        # the planner correctly set requires_tools=False, and `action_confirmed` opened
        # the loadout anyway: nine read tools ran on a turn that needed none. An
        # instruction to act is not a request to go looking.
        loadout = (
            active.visible_reads(plan.current_intent, catalogue)
            if plan.requires_tools
            else []
        )

        connection = open_db()
        try:
            # An application-supplied request id wins. Absent one, resolve it from the
            # subject — a turn about Rachel's access is about AR001 whether or not
            # anybody said so. Without this the gate reports "nothing to approve", which
            # is true, useless, and leaves the model with nothing to tell the user.
            # Only for a scope that could write. Maya is refused on authority alone, so
            # resolving a request for her is work whose result is discarded — and it
            # would mean a read-only scope touching the database to decide it may not
            # write, which is the wrong order to check things in.
            request_id = state.get("pending_request_id")
            if active.writes_allowed and not request_id:
                request_id = pending_request_for(
                    connection, plan.subject_employee or state.get("caller_id")
                )
            decision: GateDecision = release_writes(
                scope=active,
                intent=plan.current_intent,
                request_id=request_id,
                connection=connection,
                catalogue=catalogue,
            )

            # Maya asked to act: record the typed handoff instead of filing anything.
            # Built here, by application code, because the golden data marks S2 turn 10
            # as needing no tool call — the handoff records what was asked, it does not
            # look anything up. The model's plan supplies who and what (proposing); the
            # conversation id and the requester are the application's. ADR-023.
            handoff = None
            if (
                not active.writes_allowed
                and plan.action_confirmed
                and plan.subject_employee
                and plan.requested_system
            ):
                try:
                    built = prepare_handoff(
                        connection,
                        subject=plan.subject_employee,
                        system=plan.requested_system,
                        justification=plan.action_justification,
                        conversation_id=state.get("thread_id") or "unknown",
                        requested_by=state.get("caller_id") or "anonymous",
                    )
                    handoff = built.model_dump(mode="json") if built else None
                except Exception:  # noqa: BLE001 — a handoff must never fail the turn
                    handoff = None
        finally:
            connection.close()

        return {
            "loadout": sorted(set(loadout) | set(decision.released)),
            "rearmed": False,
            "pending_request_id": request_id,
            "handoff": handoff,
            "gate_reason": decision.reason,
            "gate_released": decision.released,
            "force_tool": False,
            "tool_rounds": 0,
            "corrected": False,
        }

    # 4. MODEL --------------------------------------------------------------
    def call_model(state: AgentState) -> dict:
        plan = TurnPlan(**state["plan"])
        active = get_scope(state.get("scope", "maya"))
        exhausted = state.get("tool_rounds", 0) >= config.MAX_TOOL_ROUNDS

        # The answering model sees the distilled memory too, not only the planner.
        # Without this it gets `conversation_window` — two turns verbatim — plus an
        # unordered list of facts, and nothing that says which came first. Measured on
        # `S3` turn 8, "who was the person in my very first question?": the memory held
        # "Rachel Stein's Webex licence — resolved" and the answer said Alex Kim, the
        # current subject, because that was the only history it could see. Distillation
        # is supposed to replace the raw messages for the model that answers, not just
        # for the one that plans.
        prompt = (
            answer_prompt(active)
            + render_caller(state)
            + render_memory_for_answer(state)
            + render_plan(plan)
            + render_gate(state)
            + render_handoff(state)
            + render_subjects(state)
        )
        if exhausted:
            # Offer nothing and say why. Leaving tools bound and asking nicely does not
            # stop a model that has decided to keep looking: unbounded, one turn cycled
            # the same six tools six times over for 38 calls.
            prompt += BOUNDED_INSTRUCTION
        if state.get("rearmed"):
            # The retry must differ in what the model is told, not only in what it is
            # offered — see REARM_INSTRUCTION.
            prompt += REARM_INSTRUCTION
        messages = [
            SystemMessage(prompt),
            *conversation_window(state["messages"]),
        ]
        # `force_tool` is CONSUMED here, not merely read. It is set by `rearm` and
        # cleared by this call, because `rearmed` stays true for the rest of the turn:
        # leaving the force on would oblige a tool call after the tool results came
        # back, which loops model -> tools -> model until the recursion limit.
        loadout = [] if exhausted else state["loadout"]
        forced = bool(state.get("force_tool")) and bool(loadout)
        response = model_for(loadout, force=forced).invoke(messages)

        # Truthfulness is checked, not requested. On S8 turn 4 the gate correctly
        # releases nothing and the model writes "[Called create_access_request with
        # parameters: ...]" as ordinary prose — no tool ran, and to a reader that is
        # indistinguishable from having filed it. Two rounds of prompt did not stop it,
        # so the answer is inspected and rewritten once. Deterministic code deciding
        # what the model may claim, which is the same principle as the write gate.
        if not getattr(response, "tool_calls", None):
            from novaops.agent.policy import message_text as _text

            claimed = narrates_untaken_action(_text(response), _tools_this_turn(state))
            if claimed and not state.get("corrected"):
                corrected = model_for(loadout, force=False).invoke(
                    [SystemMessage(prompt + TRUTHFULNESS_CORRECTION),
                     *conversation_window(state["messages"])]
                )
                return {"messages": [corrected], "force_tool": False, "corrected": True}

        return {"messages": [response], "force_tool": False}

    # 5. REARM — the model answered without the lookup it said it needed ----
    def rearm(state: AgentState) -> dict:
        """The planner said this turn needs evidence and the model answered without any.

        Three moves, and the third is the one that matters. Reopen the read catalogue,
        delete the answer produced without the lookup, and mark the turn `rearmed` — which
        makes `call_model` bind with `tool_choice="any"` and append `REARM_INSTRUCTION`.

        Widening the loadout alone does nothing: the loadout the model already had was
        correct, and deleting the answer leaves identical context, so a polite retry
        reproduces the same answer. Measured on `S2` turn 9, where the model declined a
        lookup twice in a row before the choice was taken away from it. Deciding this in
        code rather than asking the model again is the point.

        Never reopens a write: a write released by the gate stays released, and one that
        was not is not granted here as a side effect of a retry.
        """

        allowed = sorted(set(read_tools(catalogue)) | set(state.get("gate_released") or []))
        return {
            "loadout": allowed,
            "rearmed": True,
            "force_tool": True,
            "messages": [RemoveMessage(id=state["messages"][-1].id)],
        }

    def route(state: AgentState) -> str:
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None):
            return "tools"
        plan = TurnPlan(**state["plan"])
        missed_lookup = (
            plan.requires_tools
            and not state.get("rearmed")
            and not used_tool_this_turn(state["messages"])
        )
        return "rearm" if missed_lookup else END

    # 6. WIRING -------------------------------------------------------------
    graph = StateGraph(AgentState)
    graph.add_node("distil", distil)
    graph.add_node("classify", classify)
    graph.add_node("scope", scope)
    graph.add_node("model", call_model)
    graph.add_node("rearm", rearm)
    tool_node = ToolNode(tools)

    async def run_tools(state: AgentState) -> dict:
        """Run the requested tools and count the round against this turn's allowance."""

        result = await tool_node.ainvoke(state)
        rounds = state.get("tool_rounds", 0) + 1
        if isinstance(result, dict):
            return {**result, "tool_rounds": rounds}
        return {"messages": result, "tool_rounds": rounds}

    graph.add_node("tools", run_tools)

    graph.add_conditional_edges(
        START,
        lambda s: "distil" if needs_distillation(s) else "classify",
        {"distil": "distil", "classify": "classify"},
    )
    graph.add_edge("distil", "classify")
    graph.add_edge("classify", "scope")
    graph.add_edge("scope", "model")
    graph.add_conditional_edges("model", route, {"tools": "tools", "rearm": "rearm", END: END})
    graph.add_edge("rearm", "model")
    graph.add_edge("tools", "model")

    return graph.compile(checkpointer=checkpointer)


def sqlite_checkpointer(path=None):
    """The durable checkpointer, as an async context manager.

    Kept here so callers do not each decide where checkpoints live, and so the choice of
    a file-backed saver is visible next to the graph it protects.
    """

    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from novaops import config

    target = path or config.CHECKPOINT_DB
    target.parent.mkdir(parents=True, exist_ok=True)
    return AsyncSqliteSaver.from_conn_string(str(target))


__all__ = ["build_graph", "sqlite_checkpointer"]
