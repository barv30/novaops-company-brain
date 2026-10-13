"""The guard against the bug that would have corrupted the submission run.

ADR-018. `var/checkpoints.db` keeps conversation history per `thread_id` and survives
process restarts — that is the point of a durable checkpointer. But case ids repeat
across runs by design (`M-I-01` is `M-I-01` every time), so a thread id built from one
replays the previous run's history unless something makes it unique. The planner then
sees the question already answered, sets `requires_tools=False`, and the turn uses no
tools. The trace reads as a broken loadout and is stale state.

No model or tool server is needed to prove the guard works, so none is used.
"""

from __future__ import annotations

from typing import Annotated

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from novaops.agent import ThreadNotFreshError, new_thread_id, runner
from novaops.identity import Caller

CALLER = Caller("E010", "UG_REGULAR", is_manager=False)


class _State(TypedDict, total=False):
    messages: Annotated[list, add_messages]


def _stub_graph(_tools, checkpointer, **_kwargs):
    """A graph with the same shape as the real one and no model calls."""

    builder = StateGraph(_State)
    builder.add_node("reply", lambda s: {"messages": [AIMessage("ok")]})
    builder.add_edge(START, "reply")
    builder.add_edge("reply", END)
    return builder.compile(checkpointer=checkpointer)


@pytest.fixture
def stub(monkeypatch):
    monkeypatch.setattr(runner, "build_graph", _stub_graph)


# 1. UNIQUE THREAD IDS -------------------------------------------------------

def test_a_case_id_yields_a_different_thread_id_each_run():
    """The same case id must not resolve to the same thread across runs."""

    first = new_thread_id("M-I-01")
    assert first.startswith("M-I-01:"), "the case id stays readable for the trace index"
    assert new_thread_id("M-I-01") != first


def test_the_same_case_replayed_in_one_process_gets_a_fresh_thread():
    """scripts/variance.py replays a session N times in one process. With one stamp per
    process, run 2 got run 1's thread and the freshness guard stopped it."""

    threads = {new_thread_id("S2-onboarding-maya") for _ in range(3)}
    assert len(threads) == 3


def test_two_cases_in_one_run_get_different_threads():
    assert new_thread_id("M-I-01") != new_thread_id("M-I-02")


def test_a_thread_id_with_no_case_id_is_still_unique():
    assert new_thread_id() != new_thread_id()


# 2. THE GUARD ---------------------------------------------------------------

async def test_a_fresh_thread_is_accepted(stub):
    saver = InMemorySaver()
    result = await runner.answer(
        "first question",
        caller=CALLER,
        thread_id=new_thread_id("M-I-01"),
        tools=[],
        checkpointer=saver,
        expect_fresh=True,
    )
    assert result.answer == "ok"


async def test_reusing_a_thread_for_a_new_case_is_refused(stub):
    """The exact failure from ADR-018, now loud instead of silent."""

    saver = InMemorySaver()
    thread = "M-I-01"  # a bare case id — what a careless runner would use

    await runner.answer("first run", caller=CALLER, thread_id=thread,
                        tools=[], checkpointer=saver, expect_fresh=True)

    with pytest.raises(ThreadNotFreshError, match="already holds"):
        await runner.answer("second run, same case id", caller=CALLER, thread_id=thread,
                            tools=[], checkpointer=saver, expect_fresh=True)


async def test_the_error_says_how_to_fix_it(stub):
    saver = InMemorySaver()
    await runner.answer("t1", caller=CALLER, thread_id="dup", tools=[],
                        checkpointer=saver, expect_fresh=True)
    with pytest.raises(ThreadNotFreshError) as caught:
        await runner.answer("t2", caller=CALLER, thread_id="dup", tools=[],
                            checkpointer=saver, expect_fresh=True)
    message = str(caught.value)
    assert "new_thread_id" in message
    assert "--checkpoints" in message


async def test_continuing_a_session_is_not_refused(stub):
    """Within a session the opposite rule holds: one thread, every turn in order.

    `expect_fresh` is for the FIRST turn of an independent case only. Turn 2 of a
    twelve-turn session must land on the same thread and see turn 1.
    """

    saver = InMemorySaver()
    thread = new_thread_id("M-S-01")

    await runner.answer("turn 1", caller=CALLER, thread_id=thread, tools=[],
                        checkpointer=saver, expect_fresh=True)
    second = await runner.answer("turn 2", caller=CALLER, thread_id=thread, tools=[],
                                 checkpointer=saver)  # no expect_fresh

    human = [m for m in second.state["messages"] if isinstance(m, HumanMessage)]
    assert len(human) == 2, "turn 2 did not see turn 1"
