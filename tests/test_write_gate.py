"""The write gate: four conditions, each one able to keep it shut on its own.

No model is involved in any of these tests, which is the point. `release_writes` takes
no plan, no message and no model output — so if these pass, nothing the model produces
can open the gate, and that is a property of the signature rather than of the prompt.
"""

from __future__ import annotations

import shutil
import sqlite3

import pytest
from scripts.reseed import reseed

from novaops import config
from novaops.agent.policy import LOADOUTS, WRITE_TOOLS
from novaops.agent.scopes import MAYA_SCOPE, WEBEX_SCOPE, get_scope
from novaops.agent.write_gate import approval_is_recorded, release_writes
from novaops.db import writes
from novaops.db.connection import connect

CATALOGUE = sorted(
    {
        "get_employee", "check_software_subscription", "list_employee_tickets",
        "list_access_requests", "get_approval_status", "search_policies",
        "search_knowledge_base", "search_handbook", "get_policy", "list_policies",
        "create_ticket", "create_access_request", "request_approval",
    }
)


@pytest.fixture(scope="session")
def seeded(tmp_path_factory):
    """Reseed once; each test copies the result rather than rebuilding it."""

    reseed()
    master = tmp_path_factory.mktemp("gate") / "novaops.db"
    shutil.copy(config.BUSINESS_DB, master)
    return master


@pytest.fixture
def db(seeded, tmp_path) -> sqlite3.Connection:
    path = tmp_path / "novaops.db"
    shutil.copy(seeded, path)
    connection = connect(path)
    yield connection
    connection.close()


def gate(db, *, scope=WEBEX_SCOPE, intent="access_request", request_id="AR001"):
    return release_writes(
        scope=scope, intent=intent, request_id=request_id,
        connection=db, catalogue=CATALOGUE,
    )


# 1. CONDITION 1 — THE SCOPE ------------------------------------------------

def test_maya_can_never_write(db):
    """Maya's read-only property, enforced as a checked condition.

    Not a folder boundary: this is the same function, the same database and the same
    request that would open for Webex.
    """

    decision = gate(db, scope=MAYA_SCOPE)
    assert decision.released == []
    assert "read-only" in decision.reason


def test_maya_stays_shut_even_with_every_approval_granted(db):
    """Grant everything AR001 needs, then ask as Maya. Still nothing."""

    for approval in ("AP001", "AP002"):
        writes.record_decision(
            db, approval_id=approval, decided_by="E006", approved=True, reason="ok"
        )
    assert gate(db, scope=WEBEX_SCOPE).opened is True   # the approvals really are granted
    assert gate(db, scope=MAYA_SCOPE).opened is False   # and Maya still cannot write


def test_no_maya_loadout_contains_a_write_tool():
    """Belt as well as braces: the visibility table never offers a write either."""

    for intent in MAYA_SCOPE.intents:
        visible = MAYA_SCOPE.visible_reads(intent, CATALOGUE)
        assert not (set(visible) & WRITE_TOOLS), intent


def test_no_loadout_anywhere_contains_a_write_tool():
    """Visibility and authorization are separate; no loadout row may blur them."""

    for intent, tools in LOADOUTS.items():
        assert not (set(tools or []) & WRITE_TOOLS), intent


# 2. CONDITION 2 — THE INTENT ------------------------------------------------

@pytest.mark.parametrize("intent", ["benefits_question", "career_question", "policy_question"])
def test_a_non_action_intent_releases_nothing(db, intent):
    decision = gate(db, intent=intent)
    assert decision.released == []
    assert "not an action" in decision.reason


# 3. CONDITION 3 — AN APPROVAL MUST EXIST ------------------------------------

def test_no_request_id_releases_nothing(db):
    decision = gate(db, request_id=None)
    assert decision.released == []
    assert "no request id" in decision.reason


def test_a_request_with_no_approval_record_releases_nothing(db):
    """AR003 is approved as a request but carries no rows in `approvals`."""

    decision = gate(db, request_id="AR003")
    assert decision.released == []
    assert "no approval record" in decision.reason


def test_an_invented_request_id_releases_nothing(db):
    decision = gate(db, request_id="AR-DOES-NOT-EXIST")
    assert decision.released == []


# 4. CONDITION 4 — EVERY APPROVAL, NOT JUST ONE ------------------------------

def test_a_partly_approved_request_stays_shut(db):
    """The condition that matters most.

    AR001 needs AP001 (IT Manager) and AP002 (Finance). Granting one is not a decision;
    a gate that asked "is there an approved row?" would open here.
    """

    writes.record_decision(
        db, approval_id="AP001", decided_by="E006", approved=True, reason="IT agrees"
    )
    decision = gate(db)
    assert decision.released == []
    assert "still outstanding" in decision.reason
    assert "AP002" in decision.reason


def test_the_gate_opens_only_when_nothing_is_outstanding(db):
    assert gate(db).opened is False
    writes.record_decision(db, approval_id="AP001", decided_by="E006", approved=True, reason="ok")
    assert gate(db).opened is False
    writes.record_decision(db, approval_id="AP002", decided_by="E011", approved=True, reason="ok")

    decision = gate(db)
    assert decision.opened is True
    assert set(decision.released) == WRITE_TOOLS & set(CATALOGUE)
    assert decision.approval_ids == ("AP001", "AP002")


def test_a_rejection_does_not_open_the_gate(db):
    writes.record_decision(db, approval_id="AP001", decided_by="E006", approved=True, reason="ok")
    writes.record_decision(
        db, approval_id="AP002", decided_by="E011", approved=False, reason="renegotiate first"
    )
    decision = gate(db)
    assert decision.released == []
    assert "AP002" in decision.reason


def test_requesting_an_approval_does_not_open_the_gate(db):
    """Asking for a decision is not receiving one.

    This is why `request_approval` is a tool and `record_decision` is not: if the agent
    could open its own gate by asking, the durable-approval design would be decorative.
    """

    writes.request_approval(
        db, request_type="access_request", request_id="AR004", approver_id="E006",
        reason="Please decide.", idempotency_key="gate-test:AR004",
    )
    assert gate(db, request_id="AR004").opened is False


# 5. THE HELPER, DIRECTLY ----------------------------------------------------

def test_approval_is_recorded_reports_ids_even_when_refusing(db):
    """The trace needs to show WHICH approvals were outstanding, not just that some were."""

    granted, ids, reason = approval_is_recorded(db, "AR001")
    assert granted is False
    assert ids == ("AP001", "AP002")
    assert "E006" in reason and "E011" in reason


def test_an_unknown_scope_is_refused_rather_than_defaulted():
    """Defaulting to Maya would be safe by accident; to Webex, unsafe by accident."""

    with pytest.raises(ValueError, match="Unknown scope"):
        get_scope("admin")
