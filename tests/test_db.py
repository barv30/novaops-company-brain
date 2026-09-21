"""Reads, keyed writes, and the two kinds of duplicate they prevent.

Each test runs against a fresh copy of the real seeded database, so what is asserted
here is true of the data the evaluation set runs on — not of a fixture that agrees with
the implementation by construction.
"""

from __future__ import annotations

import shutil
import sqlite3

import pytest
from scripts.reseed import reseed

from novaops import config
from novaops.db import idempotency, repositories, writes
from novaops.db.connection import connect, next_id


@pytest.fixture(scope="session")
def seeded_db(tmp_path_factory):
    """One reseed per session; each test gets its own copy of the result."""

    reseed()
    master = tmp_path_factory.mktemp("seed") / "novaops.db"
    shutil.copy(config.BUSINESS_DB, master)
    return master


@pytest.fixture
def db(seeded_db, tmp_path) -> sqlite3.Connection:
    path = tmp_path / "novaops.db"
    shutil.copy(seeded_db, path)
    connection = connect(path)
    yield connection
    connection.close()


# 1. THE RECORDS THE PROJECT TURNS ON ---------------------------------------

def test_rachel_stein_has_an_open_ticket_and_a_blocked_request(db):
    """T001 and AR001 exist before we arrive. That is the whole reuse problem."""

    ticket = repositories.find_open_ticket(db, "E010", "S001")
    request = repositories.find_open_access_request(db, "E010", "S001")

    assert ticket["ticket_id"] == "T001"
    assert request["request_id"] == "AR001"
    # The database already knows the answer; the workflow's job is to find it.
    assert request["status"] == "blocked"
    assert "seats exceed contract limit" in request["decision_reason"]


def test_webex_is_over_its_contractual_seat_limit(db):
    position = repositories.seat_position(db, "Webex")

    assert (position["active_seats"], position["seat_limit"]) == (42, 40)
    assert position["at_or_over_limit"] is True
    assert position["seats_over_limit"] == 2
    # The limit is a contractual fact, so the contract travels with it.
    assert position["contract_id"] == "C001"
    assert position["contract_document"]


def test_ar001_requires_two_approvals_not_one(db):
    """'Is it approved?' is not one row lookup — AP001 (IT) and AP002 (Finance)."""

    outstanding = repositories.outstanding_approvals(db, "AR001")
    assert {a["approval_id"] for a in outstanding} == {"AP001", "AP002"}
    # Neither 'needed' nor 'pending' means approved.
    assert {a["status"] for a in outstanding} == {"needed"}


def test_seat_assignment_gap_refuses_to_invent_an_answer(db):
    """W-I-02 has no precise answer, and the system must say so from a tool result."""

    gap = repositories.seat_assignment_gap(db, "Webex")

    assert gap["seat_assignments_recorded"] is False
    assert "no employee-to-seat table" in gap["why"]
    assert "entitlement, not seat assignment" in gap["do_not_substitute"]
    # It still reports what IS on record, so the answer is useful as well as honest.
    assert gap["seat_counts"]["active_seats"] == 42
    assert any(r["request_id"] == "AR001" for r in gap["access_requests_on_record"])


def test_name_lookup_returns_every_match(db):
    """E001 Maya Cohen and E020 Maya Rosen. Returning the first is how the wrong
    person's onboarding gets reported."""

    matches = repositories.find_employees(db, "Maya")
    assert {m["employee_id"] for m in matches} == {"E001", "E020"}


# 2. IDEMPOTENCY — THE TWO KINDS OF DUPLICATE -------------------------------

def _count(db: sqlite3.Connection, table: str) -> int:
    return db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608


def test_an_existing_record_is_reused_not_duplicated(db):
    """Duplicate kind 1: the row existed before this system ran.

    No ledger entry can know about AR001, so only a natural-key match finds it.
    """

    before = _count(db, "access_requests")
    result = writes.create_access_request(
        db,
        employee_id="E010",
        system_id="S001",
        access_level="standard_user",
        business_justification="Customer onboarding calls require Webex.",
        idempotency_key="thread-1:E010:S001:standard_user",
    )

    assert result.entity_id == "AR001"
    assert result.disposition == "reused"
    assert result.created is False
    assert _count(db, "access_requests") == before


def test_replaying_the_same_request_writes_nothing(db):
    """Duplicate kind 2: we run our own request twice."""

    key = "thread-9:E002:S004:repo_contributor"
    kwargs = {
        "employee_id": "E002",
        "system_id": "S004",
        "access_level": "repo_contributor",
        "business_justification": "Backend work on the connector repo.",
        "idempotency_key": key,
    }

    first = writes.create_access_request(db, **kwargs)
    after_first = _count(db, "access_requests")
    second = writes.create_access_request(db, **kwargs)

    assert first.disposition == "created"
    assert second.disposition == "replayed"
    assert second.entity_id == first.entity_id
    assert _count(db, "access_requests") == after_first


def test_a_genuinely_new_request_is_not_suppressed(db):
    """Idempotency must not become 'never write anything twice'."""

    before = _count(db, "access_requests")
    writes.create_access_request(
        db, employee_id="E002", system_id="S004", access_level="repo_contributor",
        business_justification="First.", idempotency_key="thread-a:E002:S004:repo_contributor",
    )
    writes.create_access_request(
        db, employee_id="E016", system_id="S004", access_level="repo_contributor",
        business_justification="Second, different person.",
        idempotency_key="thread-b:E016:S004:repo_contributor",
    )
    assert _count(db, "access_requests") == before + 2


def test_tickets_reuse_an_open_ticket_for_the_same_system(db):
    before = _count(db, "tickets")
    result = writes.create_ticket(
        db,
        employee_id="E010",
        category="Access",
        subcategory="Webex license",
        subject="Webex access still not working",
        description="Follow-up.",
        related_system_id="S001",
        idempotency_key="thread-2:E010:S001:ticket",
    )
    assert result.entity_id == "T001"
    assert result.reused_existing is True
    assert _count(db, "tickets") == before


def test_replaying_a_ticket_creates_no_second_row(db):
    key = "thread-3:E011:S015:ticket"
    kwargs = {
        "employee_id": "E011",
        "category": "Hardware",
        "subcategory": "Monitor",
        "subject": "Second monitor request",
        "description": "Working from home two days a week.",
        "related_system_id": "S015",
        "idempotency_key": key,
    }
    first = writes.create_ticket(db, **kwargs)
    after = _count(db, "tickets")
    second = writes.create_ticket(db, **kwargs)

    assert first.disposition == "created"
    assert second.disposition == "replayed"
    assert _count(db, "tickets") == after


# 3. APPROVALS — THE TOOL NOBODY HANDED US ----------------------------------

def test_requesting_an_approval_persists_it_as_pending(db):
    """The gap: nothing in the eleven inherited tools writes to `approvals`."""

    before = _count(db, "approvals")
    result = writes.request_approval(
        db,
        request_type="access_request",
        request_id="AR004",
        approver_id="E006",
        reason="IT Manager must approve Webex provisioning for a new joiner.",
        idempotency_key="thread-4:AR004:E006",
    )
    assert result.disposition == "created"
    assert result.row["status"] == "pending"
    assert _count(db, "approvals") == before + 1


def test_requesting_the_same_approval_twice_creates_one_row(db):
    key = "thread-5:AR004:E006"
    kwargs = {
        "request_type": "access_request",
        "request_id": "AR004",
        "approver_id": "E006",
        "reason": "IT Manager approval.",
        "idempotency_key": key,
    }
    writes.request_approval(db, **kwargs)
    after = _count(db, "approvals")
    again = writes.request_approval(db, **kwargs)

    assert again.disposition == "replayed"
    assert _count(db, "approvals") == after


def test_requesting_an_approval_never_grants_it(db):
    """A request for a decision is not a decision. This is the gate's whole basis."""

    result = writes.request_approval(
        db, request_type="access_request", request_id="AR004", approver_id="E006",
        reason="Please decide.", idempotency_key="thread-6:AR004:E006",
    )
    assert result.row["status"] not in repositories.APPROVED_STATUSES


def test_only_record_decision_can_approve(db):
    approval = writes.request_approval(
        db, request_type="access_request", request_id="AR004", approver_id="E006",
        reason="Please decide.", idempotency_key="thread-7:AR004:E006",
    )
    assert repositories.outstanding_approvals(db, "AR004")

    writes.record_decision(
        db, approval_id=approval.entity_id, decided_by="E006", approved=True,
        reason="Seat freed by a leaver.",
    )
    remaining = [
        a["approval_id"] for a in repositories.outstanding_approvals(db, "AR004")
    ]
    assert approval.entity_id not in remaining


def test_recording_the_same_decision_twice_is_a_no_op(db):
    """A resume after a restart may legitimately replay the decision."""

    approval = writes.request_approval(
        db, request_type="access_request", request_id="AR004", approver_id="E006",
        reason="Please decide.", idempotency_key="thread-8:AR004:E006",
    )
    args = {"approval_id": approval.entity_id, "decided_by": "E006",
            "approved": True, "reason": "Approved."}
    writes.record_decision(db, **args)
    second = writes.record_decision(db, **args)
    assert second.disposition == "replayed"


def test_a_rejection_is_not_an_approval(db):
    approval = writes.request_approval(
        db, request_type="access_request", request_id="AR004", approver_id="E006",
        reason="Please decide.", idempotency_key="thread-10:AR004:E006",
    )
    writes.record_decision(
        db, approval_id=approval.entity_id, decided_by="E006", approved=False,
        reason="Contract renegotiation first.",
    )
    still_outstanding = {
        a["approval_id"] for a in repositories.outstanding_approvals(db, "AR004")
    }
    assert approval.entity_id in still_outstanding


# 4. MECHANICS --------------------------------------------------------------

def test_writes_are_audited(db):
    before = _count(db, "audit_log")
    writes.create_access_request(
        db, employee_id="E002", system_id="S004", access_level="repo_contributor",
        business_justification="Connector work.", idempotency_key="thread-11:E002:S004",
        actor="E006",
    )
    assert _count(db, "audit_log") == before + 1


def test_new_ids_follow_the_dataset_convention(db):
    assert next_id(db, "access_requests", "request_id", "AR") == "AR005"
    assert next_id(db, "tickets", "ticket_id", "T") == "T007"
    assert next_id(db, "approvals", "approval_id", "AP") == "AP005"


def test_a_stale_ledger_entry_does_not_return_a_phantom_row(db):
    """After a reseed the ledger may point at an id that no longer exists."""

    idempotency.record(
        db, key="stale", entity_type="ticket", entity_id="T999", reused=False
    )
    result = writes.create_ticket(
        db, employee_id="E011", category="Hardware", subcategory="Dock",
        subject="Dock request", description="New desk setup.",
        idempotency_key="stale",
    )
    assert result.disposition == "created"
    assert result.entity_id != "T999"
