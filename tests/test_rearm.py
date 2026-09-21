"""The rearm path, and the loop it must not create.

`rearm` fires when the planner said the turn needs evidence and the model answered
without any. Measured on `S2` turn 9, a polite retry does not work: the loadout the model
already had was correct, and deleting the answer leaves identical context, so it produces
the same answer again. The retry therefore binds with `tool_choice="any"`, which takes
the choice away.

That force must be consumed by exactly one call. `rearmed` stays true for the rest of the
turn, so a force keyed on it obliges another tool call after the results come back —
model to tools to model until the recursion limit. This module exists because that is
exactly what happened, and it drives the real graph rather than asserting the contract in
prose.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from novaops.agent import graph as graph_module
from novaops.agent.policy import ConversationMemory, TurnPlan


@tool
def get_policy(name: str) -> str:
    """Return a policy by name."""

    return f"[source: policies/{name}.md] the rule says five days"


class _FakeBound:
    """A model with a fixed tool subset bound, recording how it was bound."""

    def __init__(self, parent: _FakeModel, tool_choice) -> None:
        self.parent = parent
        self.tool_choice = tool_choice

    def invoke(self, messages):
        self.parent.calls.append(self.tool_choice)
        # Answer without a tool the first time; comply once obliged.
        if self.tool_choice == "any":
            return AIMessage(
                content="",
                tool_calls=[{"name": "get_policy", "args": {"name": "equipment_policy"},
                             "id": f"call-{len(self.parent.calls)}"}],
            )
        return AIMessage(content="I already know this, no lookup needed.")


class _Structured:
    def __init__(self, value) -> None:
        self.value = value

    async def ainvoke(self, messages):
        return {"parsed": self.value, "raw": AIMessage(content="")}


class _FakeModel:
    """Stands in for Bedrock. Records every `tool_choice` it was bound with."""

    def __init__(self) -> None:
        self.calls: list = []

    def bind_tools(self, tools, tool_choice=None):
        return _FakeBound(self, tool_choice)

    def invoke(self, messages):
        self.calls.append(None)
        return AIMessage(content="I already know this, no lookup needed.")

    def with_structured_output(self, model, include_raw=False):
        if model is TurnPlan:
            # The planner insists a lookup is needed. That is what arms rearm.
            return _Structured(
                TurnPlan(current_intent="policy_question", requires_tools=True)
            )
        return _Structured(
            ConversationMemory(current_intent="policy", important_facts=["x"])
        )


@pytest.fixture
def shared_model(monkeypatch) -> _FakeModel:
    model = _FakeModel()
    monkeypatch.setattr(graph_module, "get_model", lambda **kw: model)
    return model


async def _run(model, tmp_path):
    app = graph_module.build_graph(
        [get_policy], InMemorySaver(), open_db=lambda: _NullDB()
    )
    return await app.ainvoke(
        {
            "messages": [HumanMessage("does adding a seat need Finance?")],
            "caller_id": "E004",
            "user_group": "UG_HR",
            "is_manager": True,
            "scope": "maya",
            "thread_id": "rearm-test",
            "request_id": "req-test",
        },
        config={"configurable": {"thread_id": "rearm-test"}},
    )


class _NullDB:
    """The write gate consults a database; Maya's scope refuses before it is read."""

    def execute(self, *args, **kwargs):
        raise AssertionError("the gate must refuse on scope before touching the database")

    def close(self):
        return None


async def test_rearm_forces_a_tool_call_after_the_model_declines(shared_model, tmp_path):
    """The whole point: the second attempt is not a request, it is an obligation."""

    result = await _run(shared_model, tmp_path)

    assert shared_model.calls, "the model was never called"
    assert shared_model.calls[0] is None, "the first attempt should be a free choice"
    assert "any" in shared_model.calls, "rearm never forced a tool call"
    assert result["rearmed"] is True


async def test_the_force_is_spent_and_the_turn_terminates(shared_model, tmp_path):
    """The loop this module is named after.

    With the force keyed on `rearmed` rather than consumed, the model is obliged to call
    a tool again once the results come back, and the turn never ends. Terminating at all
    is the assertion; the flag being false afterwards is why it terminated.
    """

    result = await _run(shared_model, tmp_path)

    assert result.get("force_tool") is False, "the force outlived the call that used it"
    assert shared_model.calls.count("any") == 1, (
        f"the model was obliged {shared_model.calls.count('any')} times, not once: "
        f"{shared_model.calls}"
    )


async def test_the_tool_actually_ran(shared_model, tmp_path):
    from langchain_core.messages import ToolMessage

    result = await _run(shared_model, tmp_path)
    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert tool_messages, "the forced call produced no tool result"
    assert "policies/equipment_policy.md" in tool_messages[0].content


async def test_rearm_does_not_fire_when_the_planner_wants_no_tools(monkeypatch, tmp_path):
    """Turns 7, 10 and 12 of S2 answer from history. Forcing a tool there is the
    opposite failure — a needless call on a turn the golden data says needs none."""

    model = _FakeModel()
    monkeypatch.setattr(graph_module, "get_model", lambda **kw: model)
    monkeypatch.setattr(
        _FakeModel,
        "with_structured_output",
        lambda self, m, include_raw=False: _Structured(
            TurnPlan(current_intent="onboarding_status", requires_tools=False)
            if m is TurnPlan
            else ConversationMemory(current_intent="x", important_facts=["y"])
        ),
    )

    result = await _run(model, tmp_path)

    assert not result.get("rearmed")
    assert "any" not in model.calls, "a no-tool turn was forced into a lookup"
