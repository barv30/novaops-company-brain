"""The terminal status a turn reports, and the trap in computing it.

Every run ends in exactly one `status`, the prose must agree with it, and the
specification says it is what a marker reads first. So the turn takes it from the
workflow's own typed return rather than guessing from the answer text.

The trap is nesting. An `OnboardingChecklist` is `completed` while containing items
whose own status is `blocked` — deliberately, because the blocked items *are* the
answer rather than a failure to produce one. The first version of `_terminal_status`
matched the string `"status": "blocked"` anywhere in the tool result, found those nested
item statuses, and reported every Maya onboarding turn as blocked.

Both directions are pinned here: a checklist with blocked items inside it must not make
the turn blocked, and an `AccessDecision` that *is* blocked must.
"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from novaops.agent.runner import _terminal_status, _top_level_status
from novaops.contracts.common import Status


def turn(*tool_payloads: str, answer: str = "an answer") -> list:
    """One turn's messages: the user, some tool results, and the reply."""

    messages: list = [HumanMessage("a question")]
    for index, payload in enumerate(tool_payloads):
        messages.append(ToolMessage(content=payload, tool_call_id=f"c{index}", name="a_tool"))
    messages.append(AIMessage(answer))
    return messages


CHECKLIST = json.dumps(
    {
        "employee_id": "E001",
        "status": "completed",
        "items": [
            {"name": "Okta profile", "status": "pending"},
            {"name": "Webex licence", "status": "blocked",
             "blocked_reason": "42 active seats against a 40-seat limit"},
        ],
    }
)

BLOCKED_DECISION = json.dumps(
    {
        "employee_id": "E010",
        "system": "Webex",
        "outcome": "blocked",
        "status": "blocked",
        "blocking_reason": "42 active seats against a 40-seat contractual limit",
    }
)


# 1. THE BUG THIS MODULE IS NAMED AFTER --------------------------------------

def test_a_checklist_with_blocked_items_is_not_a_blocked_turn():
    """The regression. Nested item statuses must not decide the turn's status."""

    assert '"status": "blocked"' in CHECKLIST, "the fixture must contain the trap"
    assert _terminal_status(turn(CHECKLIST), "Maya needs Okta and Webex.") == "completed"


def test_the_top_level_status_is_read_not_matched():
    assert _top_level_status(CHECKLIST) is Status.COMPLETED
    assert _top_level_status(BLOCKED_DECISION) is Status.BLOCKED


# 2. THE CASE THE SPEC SINGLES OUT -------------------------------------------

def test_a_blocked_access_decision_makes_the_turn_blocked():
    """Rachel Stein's case. "A trace showing `blocked` for the right reason earns
    credit; a confident wrong answer does not.\""""

    answer = "Rachel is eligible but the subscription is at 42 of 40 seats."
    assert _terminal_status(turn(BLOCKED_DECISION), answer) == "blocked"


def test_the_worst_status_wins_across_several_tools():
    """A turn that reached a blocker and also answered something trivial is blocked."""

    assert _terminal_status(turn(CHECKLIST, BLOCKED_DECISION), "both") == "blocked"


# 3. EVERYTHING ELSE ---------------------------------------------------------

def test_a_turn_with_no_typed_return_completes():
    """Most turns call tools that return plain rows. Absence of a status is not a
    failure — it means nothing reported one."""

    assert _terminal_status(turn(json.dumps([{"ticket_id": "T001"}])), "found it") == "completed"


def test_a_turn_with_no_tools_completes():
    assert _terminal_status(turn(), "answered from memory") == "completed"


def test_an_empty_answer_is_a_failure():
    """A run that produced nothing did not complete, whatever its tools said."""

    assert _terminal_status(turn(CHECKLIST), "") == "failed"
    assert _terminal_status(turn(CHECKLIST), "   ") == "failed"


def test_unparseable_and_unknown_payloads_are_ignored_not_fatal():
    assert _top_level_status("not json at all") is None
    assert _top_level_status(json.dumps({"status": "something_new"})) is None
    assert _top_level_status(json.dumps(["a", "list"])) is None
    assert _terminal_status(turn("not json at all"), "fine") == "completed"


def test_every_status_the_contract_allows_round_trips():
    for status in Status:
        payload = json.dumps({"status": status.value})
        assert _top_level_status(payload) is status
