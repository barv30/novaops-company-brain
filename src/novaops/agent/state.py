"""What the agent carries between nodes, and what a turn produces.

`AgentState` is Lesson 10 stage 3's state plus the three fields this project needs:
the caller (identity is an application input, so it enters the state rather than being
inferred), the scope, and a request id that survives tool calls and resumes.

Reading the state top to bottom is the cheapest description of the system: every field
is there because some node needs it, and a field nobody reads is a field to delete.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages

from novaops.contracts.common import Status
from novaops.identity import Caller


class AgentState(TypedDict, total=False):
    """One conversation thread's state.

    `messages` has a reducer, so returning it from a node APPENDS. Every other field has
    no reducer, so returning it REPLACES — which is what makes `plan` and `loadout`
    per-turn decisions rather than accumulating history.
    """

    # --- the conversation -------------------------------------------------
    messages: Annotated[list, add_messages]

    # --- who is asking (application input, never a model inference) --------
    caller_id: str
    user_group: str
    is_manager: bool

    # --- this turn's decisions --------------------------------------------
    plan: dict           # the TurnPlan the classify node produced
    scope: str           # "maya" or "webex" — which authority this turn runs under
    loadout: list[str]   # the tool names visible this turn
    rearmed: bool        # has the missed-lookup fallback already fired?
    force_tool: bool     # oblige the NEXT model call to use a tool; consumed by it
    tool_rounds: int     # tool batches run this turn; bounds refinement
    corrected: bool      # a false claim of action was already rewritten once

    # --- durable memory across a long session ------------------------------
    memory: dict         # ConversationMemory — named fields, not a summary string
    distilled_upto: int  # boundary between memory and raw history

    # --- identity of the run ----------------------------------------------
    thread_id: str       # the conversation; part of every idempotency key
    request_id: str      # survives tool calls and resumes; appears in the trace

    # --- the write gate ---------------------------------------------------
    pending_request_id: str   # the access request a write would act on, when there is one
    gate_reason: str          # why the gate opened or stayed shut — goes into the trace
    gate_released: list[str]  # write tools actually released this turn
    handoff: dict             # Maya's typed AccessIntent, when she was asked to act
    plan_override: str        # why code overruled the planner this turn; "" when it did not

    # --- terminal state ---------------------------------------------------
    status: str          # exactly one Status value per run


def caller_of(state: AgentState) -> Caller:
    """Rebuild the `Caller` from state.

    The three identity fields are stored flat rather than as a `Caller` object because
    LangGraph checkpoints state as JSON, and a dataclass would need a custom serializer
    for no benefit. This function is the one place that reassembles them, so the rest of
    the system still works with a `Caller` rather than three loose strings.
    """

    return Caller(
        employee_id=state["caller_id"],
        user_group=state.get("user_group", "UG_REGULAR"),  # type: ignore[arg-type]
        is_manager=bool(state.get("is_manager", False)),
    )


def initial_state(
    *,
    caller: Caller,
    thread_id: str,
    request_id: str,
    scope: str = "maya",
    **extra: Any,
) -> AgentState:
    """The state a fresh thread starts from."""

    state: AgentState = {
        "messages": [],
        "caller_id": caller.employee_id,
        "user_group": caller.user_group,
        "is_manager": caller.is_manager,
        "plan": {},
        "scope": scope,
        "loadout": [],
        "rearmed": False,
        "memory": {},
        "distilled_upto": 0,
        "thread_id": thread_id,
        "request_id": request_id,
        "status": Status.PENDING.value,
    }
    state.update(extra)  # type: ignore[typeddict-item]
    return state
