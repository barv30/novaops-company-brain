"""The LLM judges' wiring, checked without calling Langfuse or a model.

A judge runs on Langfuse's servers and fails quietly: a `jsonPath` that matches nothing
hands the rubric an empty variable, and the judge scores against nothing. So the contract
between `scripts/judges.py` and `agent/digest.py` is asserted here — every placeholder is
mapped, and every path lands on a field the digest actually writes.
"""

from __future__ import annotations

import re

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from scripts import judges

from novaops.agent.digest import TOOL_RESULT_CHARS, trajectory_digest
from novaops.observability.tracing import _Span


def _turn(result: str = "T001 open") -> list:
    return [
        HumanMessage("status of Rachel's ticket?"),
        AIMessage("", tool_calls=[{"id": "c1", "name": "list_employee_tickets",
                                   "args": {"employee_id": "E010"}}]),
        ToolMessage(result, name="list_employee_tickets", tool_call_id="c1"),
        AIMessage("Ticket T001 is open [source: tickets]."),
    ]


def _digest(**state) -> dict:
    base = {"plan": {"current_intent": "ticket_status", "requires_tools": True,
                     "relevant_facts": ["Rachel is E010"], "relevant_constraints": []},
            "scope": "webex"}
    base.update(state)
    return trajectory_digest(_turn(), base, status="completed", released=[])


def _resolve(document: dict, path: str):
    node = document
    for key in path.removeprefix("$.").split("."):
        node = node[key]
    return node


def test_every_placeholder_has_a_mapping_and_every_mapping_a_placeholder():
    for name, judge in judges.JUDGES.items():
        placeholders = set(re.findall(r"\{\{(\w+)\}\}", judge["prompt"]))
        mapped = {m["variable"] for m in judge["mapping"]}
        assert placeholders == mapped, name


def test_every_json_path_lands_on_a_digest_field():
    metadata = {"trajectory": _digest()}
    for judge in judges.JUDGES.values():
        for mapping in judge["mapping"]:
            if "jsonPath" in mapping:
                _resolve(metadata, mapping["jsonPath"])  # KeyError is the failure


def test_the_digest_pairs_each_call_with_its_result():
    calls = _digest()["calls"]
    assert calls == [{"tool": "list_employee_tickets", "args": {"employee_id": "E010"},
                      "result": "T001 open"}]


def test_a_long_tool_result_is_truncated_and_says_so():
    turn = _turn("x" * (TOOL_RESULT_CHARS + 50))
    digest = trajectory_digest(turn, {}, status="completed", released=[])
    assert digest["calls"][0]["result"].endswith("…[truncated]")


def test_the_write_record_is_what_application_code_decided():
    """no_false_grant compares the prose to this, so it must come from code, not the model."""

    writes = trajectory_digest(_turn(), {"gate_reason": "AP001 pending", "handoff": {"x": 1}},
                               status="blocked", released=[])["writes"]
    assert writes["write_tools_released"] == []
    assert writes["status"] == "blocked"
    assert writes["gate_reason"] == "AP001 pending"
    assert writes["handoff_prepared_not_filed"] is True


def test_the_digest_survives_the_span_wrapper_as_a_structure():
    """A stringified dict is one opaque value; no jsonPath can enter it."""

    span = _Span(span=None)
    span.update(metadata={"trajectory": _digest(), "turn": 4})
    assert isinstance(span.pending["metadata"]["trajectory"], dict)
    assert span.pending["metadata"]["turn"] == "4"


def test_one_rule_fires_both_judges_on_the_turn_span():
    rule = judges.rule_payload(["ev-1", "ev-2"], enabled=False)
    assert rule["filter"] == [{"type": "stringOptions", "column": "type",
                               "operator": "any of", "value": ["AGENT"]}]
    assert rule["evaluatorAssignments"] == [{"evaluatorId": "ev-1"}, {"evaluatorId": "ev-2"}]
    assert rule["enabled"] is False


def test_each_evaluator_carries_its_own_mapping_and_a_bounded_score():
    for name in judges.JUDGES:
        payload = judges.evaluator_payload(name, {"provider": "p", "model": "m"})
        assert payload["variableMapping"] == judges.JUDGES[name]["mapping"]
        assert (payload["outputDefinition"]["minValue"],
                payload["outputDefinition"]["maxValue"]) == (0.0, 1.0)


def test_only_the_two_judges_the_specification_names():
    assert set(judges.JUDGES) == {"citation_faithfulness", "no_false_grant"}
