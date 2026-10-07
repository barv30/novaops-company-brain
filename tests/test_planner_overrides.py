"""Where application code overrules or steers the planner. Pure functions, no model.

Two corrections, each found by a rehearsal of the evaluation set:

- `override_requires_tools` — S2 turn 9 asked a threshold question and the planner chose
  not to look the threshold up, three runs out of three.
- `render_caller` — S2 turn 4, "Rachel Stein pinged me about her ticket", was looked up
  under the caller's id, because the prompt said "me" means the caller and nothing said
  "her" does not.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from novaops.agent.policy import TurnPlan, override_requires_tools, planner_messages, render_caller


def plan(intent: str, requires_tools: bool) -> TurnPlan:
    return TurnPlan(current_intent=intent, requires_tools=requires_tools)


def tool_result(name: str) -> ToolMessage:
    return ToolMessage(content="...", name=name, tool_call_id=f"call-{name}")


# --- override_requires_tools -------------------------------------------------------

def test_a_rule_question_with_no_rule_retrieved_is_forced_to_look_it_up():
    """S2 turn 9: eight turns of records, not one policy. 'Freeze' was the user's word."""

    history = [HumanMessage("We're under the Q3 SaaS freeze."), tool_result("get_employee"),
               tool_result("check_software_subscription")]
    reason = override_requires_tools(plan("policy_question", False), history)
    assert "forced" in reason


def test_a_follow_up_on_a_policy_already_fetched_keeps_its_plan():
    """S1 turns 2-4 — 'which team owns that policy?' — must stay lookup-free."""

    history = [HumanMessage("Personal laptop policy?"), tool_result("get_policy"),
               AIMessage("..."), HumanMessage("Which team owns that policy?")]
    assert override_requires_tools(plan("policy_question", False), history) == ""


def test_an_older_unrelated_policy_does_not_excuse_the_lookup():
    """S2: turn 6 fetched the equipment policy; turn 9 asks a spending threshold."""

    history = [HumanMessage("monitor and headset?"), tool_result("get_policy"),
               AIMessage("..."), HumanMessage("add a seat?"),
               tool_result("check_software_subscription"), AIMessage("..."),
               HumanMessage("Given the freeze, does adding a seat need Finance?")]
    assert "forced" in override_requires_tools(plan("policy_question", False), history)


def test_the_last_lookup_turn_counts_every_tool_it_called():
    history = [HumanMessage("q"), tool_result("get_employee"), tool_result("search_policies"),
               AIMessage("..."), HumanMessage("and who owns it?")]
    assert override_requires_tools(plan("policy_question", False), history) == ""


def test_a_kb_article_counts_as_a_rule_already_here():
    """S5 turn 4 follows two KB lookups and expects none of its own."""

    history = [tool_result("search_knowledge_base")]
    assert override_requires_tools(plan("policy_question", False), history) == ""


def test_other_intents_are_left_to_the_planner():
    """'Remind me what I said' and 'go ahead' are not rule questions."""

    assert override_requires_tools(plan("onboarding_status", False), []) == ""
    assert override_requires_tools(plan("access_request", False), []) == ""


def test_a_plan_that_already_looks_things_up_is_untouched():
    assert override_requires_tools(plan("policy_question", True), []) == ""


# --- render_caller -----------------------------------------------------------------

def state(**overrides) -> dict:
    base = {"caller_id": "E004", "user_group": "UG_HR", "is_manager": True, "plan": {},
            "messages": [HumanMessage("hi")], "memory": {}}
    base.update(overrides)
    return base


def test_a_named_subject_is_kept_apart_from_the_caller():
    text = render_caller(state(plan={"subject_employee": "Rachel Stein"}))
    assert "about Rachel Stein, not about the caller" in text
    assert "Never use E004 for Rachel Stein's records" in text


def test_without_a_subject_the_caller_is_still_told_who_they_are():
    """W-I-01: 'my ticket' must not make the model ask Rachel for her own id."""

    text = render_caller(state(caller_id="E010", user_group="UG_REGULAR", is_manager=False))
    assert "'my' refer to E010" in text
    assert "the request is about THAT person" in text


def test_the_caller_as_subject_adds_no_separation():
    text = render_caller(state(plan={"subject_employee": "e004"}))
    assert "not about the caller" not in text


def test_the_planner_never_sees_last_turns_subject():
    """The plan in state is the previous turn's until classify replaces it."""

    stale = state(plan={"subject_employee": "Rachel Stein"})
    prompt = planner_messages(stale)[1].content
    assert "Rachel Stein" not in prompt


def test_an_anonymous_caller_is_not_given_an_identity():
    assert "not identified" in render_caller(state(caller_id="anonymous"))
