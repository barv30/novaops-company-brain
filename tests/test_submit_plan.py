"""The submission run's plan, checked without running anything.

The run itself costs ~30 minutes of model calls, so the parts that can be wrong without a
model — which cases, in what order, under whose identity and which scope — are pinned
here. A mistake in any of them would make the traces show the harness's failures rather
than the system's.
"""

from __future__ import annotations

from scripts.submit import TraceRecord, load_plan, planned_traces, write_outputs

from novaops.agent.scopes import MAYA, WEBEX


def test_the_required_set_is_exactly_27_traces():
    """`EVALUATION-INPUTS.yaml`: 27 required traces, one per turn."""

    cases, _ = load_plan()
    assert planned_traces(cases) == 27


def test_maya_is_20_and_webex_is_7():
    cases, _ = load_plan()
    by_workflow: dict[str, int] = {}
    for case in cases:
        by_workflow[case.workflow] = by_workflow.get(case.workflow, 0) + len(case.turns)
    assert by_workflow == {"maya": 20, "webex": 7}


def test_optional_workflows_are_listed_as_skipped_not_dropped():
    _, skipped = load_plan()
    assert any(line.startswith("vendor") for line in skipped)
    assert any(line.startswith("renewal") for line in skipped)


def test_scope_comes_from_the_workflow():
    """Never from the message: M-S-01 turn 10 says "file it" and is still Maya."""

    cases, _ = load_plan()
    for case in cases:
        assert case.scope == (MAYA if case.workflow == "maya" else WEBEX), case.case_id


def test_callers_come_from_the_yaml():
    """Invariant 1: identity is an application input."""

    cases = {c.case_id: c for c in load_plan()[0]}
    assert cases["M-S-01"].caller_id == "E004"   # Sara, HR, coordinating Maya
    assert cases["M-S-02"].caller_id == "E001"   # Maya herself — the permission session
    assert cases["W-I-02"].caller_id == "E018"   # Yael, the manager in UG_REGULAR


def test_session_turns_are_in_order():
    for case in load_plan()[0]:
        if case.kind == "session":
            numbers = [t.n for t in case.turns]
            assert numbers == sorted(numbers) == list(range(1, len(numbers) + 1))


def test_session_lengths_match_the_spec():
    cases = {c.case_id: c for c in load_plan()[0]}
    assert len(cases["M-S-01"].turns) == 12
    assert len(cases["M-S-02"].turns) == 5
    assert len(cases["W-S-01"].turns) == 4


def test_one_shot_inputs_have_no_turn_number():
    """SUBMISSION.md shows those rows as "—"."""

    for case in load_plan()[0]:
        if case.kind == "input":
            assert [t.n for t in case.turns] == [None]


def test_only_selects_cases_and_rejects_unknown_ones():
    import pytest

    cases, _ = load_plan(only=["W-I-02", "m-i-01"])
    assert [c.case_id for c in cases] == ["M-I-01", "W-I-02"]

    with pytest.raises(SystemExit, match="V-I-01"):
        load_plan(only=["V-I-01"])  # vendor is out of scope, so not a runnable case


def test_the_markdown_rows_match_submission_md(tmp_path):
    """The output is pasted into SUBMISSION.md, so it must use the same row shape."""

    records = [
        TraceRecord("M-I-01", None, "abc123", "completed", MAYA, "E010"),
        TraceRecord("M-S-01", 1, "def456", "completed", MAYA, "E004"),
    ]
    _, md_path = write_outputs(records, tmp_path)
    text = md_path.read_text(encoding="utf-8")
    assert "| `M-I-01` | — | abc123 |" in text
    assert "| `M-S-01` | 1 | def456 |" in text


def test_every_planned_row_exists_in_submission_md():
    """The case ids and turn numbers must line up with the form we will fill in."""

    from pathlib import Path

    form = Path("SUBMISSION.md").read_text(encoding="utf-8")
    for case in load_plan()[0]:
        for turn in case.turns:
            label = "—" if turn.n is None else str(turn.n)
            assert f"| `{case.case_id}` | {label} |" in form, (case.case_id, label)
