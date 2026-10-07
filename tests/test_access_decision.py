"""Rachel Stein's case, diagnosed from the real seeded records.

The golden rubric `WF_WEBEX_001` lists what a correct answer must and must not contain.
These assert the same things against the typed object, where they are checkable without
a judge:

  must include      eligible · over seat limit · contract close to renewal ·
                    reuse T001 and AR001 · a durable approval · no claim of access
  must not include  access already granted · only a password reset ·
                    no approval needed · a duplicate record was created

No model is involved, and nothing here writes.
"""

from __future__ import annotations

import shutil
import sqlite3

import pytest
from scripts.reseed import reseed

from novaops import config
from novaops.contracts.access import Outcome
from novaops.contracts.common import Status
from novaops.db.connection import connect
from novaops.workflows.access import diagnose_access


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    reseed()
    master = tmp_path_factory.mktemp("access") / "novaops.db"
    shutil.copy(config.BUSINESS_DB, master)
    return master


@pytest.fixture
def db(seeded, tmp_path) -> sqlite3.Connection:
    path = tmp_path / "novaops.db"
    shutil.copy(seeded, path)
    connection = connect(path)
    yield connection
    connection.close()


@pytest.fixture
def rachel(db):
    return diagnose_access(db, "E010", "Webex")


# 1. THE RUBRIC'S "MUST INCLUDE" ---------------------------------------------

def test_she_is_reported_as_eligible(rachel):
    """Eligibility and capacity are different findings. Conflating them turns an
    organizational blocker into an accusation about the person."""

    facts = " ".join(f.statement for f in rachel.observed_facts)
    assert "is entitled to Webex by role" in facts
    assert "NOT entitled" not in facts


def test_the_seat_limit_is_reported_with_its_contract(rachel):
    facts = " ".join(f.statement for f in rachel.observed_facts)
    assert "42 active seats" in facts
    assert "40-seat contractual limit" in facts
    assert "C001" in facts


def test_the_renewal_date_is_reported(rachel):
    """The rubric asks for it: the contract is close to renewal, which is why the
    blocker has a foreseeable end."""

    facts = " ".join(f.statement for f in rachel.observed_facts)
    assert "2026-07-20" in facts


def test_the_existing_records_are_reused_not_recreated(rachel):
    reused = {a.record_id: a.reused_existing for a in rachel.actions_taken}
    assert reused.get("T001") is True
    assert reused.get("AR001") is True
    assert all(a.reused_existing for a in rachel.actions_taken), (
        "something was reported as newly created"
    )


def test_both_outstanding_approvals_are_named(rachel):
    """AP001 (IT Manager) and AP002 (Finance). Reporting one would understate what is
    required, and the write gate would open on half a decision."""

    facts = " ".join(f.statement for f in rachel.observed_facts)
    assert "AP001" in facts and "AP002" in facts
    owners = {s.owner for s in rachel.recommended_next_steps}
    assert {"E006", "E011"} <= owners


def test_the_blocker_is_named_as_organizational(rachel):
    assert rachel.blocking_reason
    assert "not a problem with this employee's account" in rachel.blocking_reason


# 2. THE RUBRIC'S "MUST NOT INCLUDE" -----------------------------------------

def test_access_is_never_reported_as_granted(rachel):
    assert rachel.outcome is not Outcome.GRANTED
    assert rachel.outcome is Outcome.BLOCKED
    assert rachel.status is Status.BLOCKED


def test_nothing_says_no_approval_is_needed(rachel):
    assert rachel.recommended_next_steps, "a blocked request with no next step reads as final"
    assert any("decides" in s.step for s in rachel.recommended_next_steps)


def test_the_diagnosis_writes_nothing(db):
    """It reports records it found. Filing is the write gate's business."""

    def counts() -> dict:
        return {
            t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]  # noqa: S608
            for t in ("tickets", "access_requests", "approvals", "audit_log")
        }

    before = counts()
    for _ in range(3):
        diagnose_access(db, "E010", "Webex")
    assert counts() == before


# 3. THE OTHER SHAPES IT HAS TO GET RIGHT ------------------------------------

def test_an_ineligible_employee_is_refused_not_blocked(db):
    """Noam Sharon is not entitled to Webex by role. That is a different answer from
    "the subscription is full", and reporting it as a seat limit would be wrong about
    both the cause and the remedy."""

    decision = diagnose_access(db, "E003", "Webex")
    assert decision.outcome is Outcome.REFUSED
    assert "not entitled" in decision.blocking_reason.lower()


def test_an_unknown_system_is_refused_clearly(db):
    decision = diagnose_access(db, "E010", "Toucan")
    assert decision.outcome is Outcome.REFUSED
    assert "no system" in decision.blocking_reason.lower()


def test_an_unknown_employee_raises(db):
    with pytest.raises(KeyError):
        diagnose_access(db, "E999", "Webex")


def test_the_decision_survives_a_round_trip(rachel):
    """It crosses the MCP boundary as JSON, so it has to."""

    from novaops.contracts.access import AccessDecision

    restored = AccessDecision.model_validate(rachel.model_dump(mode="json"))
    assert restored.outcome is rachel.outcome
    assert restored.blocking_reason == rachel.blocking_reason
    assert len(restored.actions_taken) == len(rachel.actions_taken)


# 4. W-I-02 — "WHICH OF MY TEAM HAS A WEBEX SEAT?" ---------------------------

def test_team_access_states_the_gap_first(db):
    """No table assigns seats to people, so the honest answer starts there."""

    from novaops.workflows.access import team_system_access

    result = team_system_access(db, "E018", "Webex")
    assert result["seat_assignments_recorded"] is False
    assert "must not be reported as one" in result["why"]


def test_team_access_reports_what_the_records_show(db):
    """The spec's expected answer: Rachel's request blocked, Maya's provisioning task
    blocked, Noam not entitled by role."""

    from novaops.workflows.access import team_system_access

    team = {m["employee_id"]: m for m in team_system_access(db, "E018", "Webex")["team"]}
    assert set(team) == {"E001", "E003", "E010"}

    rachel = " ".join(team["E010"]["what_the_records_show"])
    maya = " ".join(team["E001"]["what_the_records_show"])
    noam = " ".join(team["E003"]["what_the_records_show"])
    assert "AR001" in rachel and "blocked" in rachel
    assert "OT005" in maya and "blocked" in maya
    assert "not entitled" in noam


def test_nobody_is_reported_as_holding_a_seat(db):
    """The trap: entitlement read as access reports Webex for two blocked people."""

    from novaops.workflows.access import team_system_access

    for member in team_system_access(db, "E018", "Webex")["team"]:
        assert member["holds_a_seat"].startswith("not recorded"), member["name"]


def test_the_entitlement_column_is_relabelled_on_the_way_out():
    """The column the agent misread is renamed at the server boundary, not hidden."""

    from novaops.mcp_server.server import _employee_view

    view = _employee_view({"employee_id": "E010", "systems_allowed_by_role": '["Webex"]'})
    assert "systems_allowed_by_role" not in view
    assert view["systems_entitled_by_role_NOT_access"] == '["Webex"]'


# 5. TOOLS ACCEPT A NAME WHERE THEY ASK FOR AN ID ----------------------------

def test_a_unique_name_resolves_to_the_id(db):
    """S2 turn 4: the model passed "Rachel Stein" and got an empty list, which it
    reported as "I don't see any tickets for Rachel Stein" — while T001 is hers."""

    from novaops.mcp_server.server import _resolve_employee

    assert _resolve_employee(db, "Rachel Stein") == "E010"
    assert _resolve_employee(db, "e010") == "E010"


def test_an_ambiguous_name_is_refused_with_the_candidates(db):
    """Two employees are called Maya. Picking one reports the wrong person's records."""

    from novaops.mcp_server.server import _resolve_employee

    with pytest.raises(ValueError, match="E001.*E020|E020.*E001"):
        _resolve_employee(db, "Maya")


def test_an_unknown_name_is_an_error_not_an_empty_result(db):
    """An empty list reads as "no records"; an error reads as "wrong input"."""

    from novaops.mcp_server.server import _resolve_employee

    with pytest.raises(ValueError, match="No employee matches"):
        _resolve_employee(db, "Toucan Sam")
