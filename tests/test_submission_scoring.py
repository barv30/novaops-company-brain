"""The submission scorer's offline half: which bar each of the 27 traces is held to.

No Langfuse, no model. The scorer reads traces back over the API; what is tested here is
the part that decides the result before any trace is read — that every row of the trace
index gets expectations, and that those come from the course material, not from us.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from evals.submission_expectations import (
    SESSIONS,
    SINGLES,
    WRITE_TOOLS,
    expectation_for,
)
from scripts.score_submission import index_rows

ROWS = index_rows(Path("SUBMISSION.md").read_text(encoding="utf-8"))


def test_the_index_has_all_27_rows():
    assert len(ROWS) == 27


@pytest.mark.parametrize("row", ROWS, ids=lambda r: f"{r.item}-{r.turn}")
def test_every_submitted_trace_has_expectations_with_a_source(row):
    expectation = expectation_for(row.item, row.turn)
    assert expectation.origin.startswith(("GOLDEN-DATASETS.json", "PROJECT-DESCRIPTION.md"))
    assert (expectation.turn.required_facts or expectation.turn.expected_sources
            or expectation.turn.forbidden_tools)


def test_session_turns_are_the_golden_turns_unchanged():
    """Never relax a binding field: the golden turn object is used as it is."""

    from evals import golden

    for item, session_id in SESSIONS.items():
        session = golden.session(session_id)
        for turn in session.turns:
            assert expectation_for(item, turn.n).turn is turn


def test_the_singles_match_their_golden_case_by_message():
    import yaml

    inputs = yaml.safe_load(Path("EVALUATION-INPUTS.yaml").read_text(encoding="utf-8"))
    text = str(inputs)
    for item in SINGLES:
        assert expectation_for(item, None).turn.user[:40] in text


def test_no_maya_item_may_write():
    for item in ("M-I-01", "M-I-02", "M-I-03"):
        assert set(WRITE_TOOLS) <= set(expectation_for(item, None).turn.forbidden_tools)


def test_w_i_03_forbids_the_duplicate_writes_the_spec_names():
    forbidden = expectation_for("W-I-03", None).turn.forbidden_tools
    assert {"create_ticket", "create_access_request"} <= set(forbidden)


def test_w_i_02_requires_the_gap_and_all_three_people():
    required = expectation_for("W-I-02", None).turn.required_facts
    assert "not recorded" in required[0]
    assert {"Rachel", "Maya", "Noam"} <= set(required)


def test_a_session_item_needs_a_turn():
    with pytest.raises(KeyError):
        expectation_for("M-S-01", None)
