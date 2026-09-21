"""Replaying a request creates no second row.

One of the four checks the specification says the datasets deliberately do not ship:

> "whether a replay stayed idempotent … those are yours. Commit them under `evals/`;
> they are read as part of your submission."

Asserted on **row counts in the persisted database**, not on what a return value claims.
A write that reports `reused` while having inserted a row would pass a check on its own
output and fail this one.

No model is involved. Idempotency is a property of the write path, and proving it
through the agent would make the check slower, costlier and less conclusive.
"""

from __future__ import annotations

import shutil
import sqlite3

import pytest
from scripts.reseed import reseed

from novaops import config
from novaops.db import writes
from novaops.db.connection import connect


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    reseed()
    master = tmp_path_factory.mktemp("idem") / "novaops.db"
    shutil.copy(config.BUSINESS_DB, master)
    return master


@pytest.fixture
def db(seeded, tmp_path) -> sqlite3.Connection:
    path = tmp_path / "novaops.db"
    shutil.copy(seeded, path)
    connection = connect(path)
    yield connection
    connection.close()


def counts(db: sqlite3.Connection) -> dict[str, int]:
    """Every row count that a write in this system could move."""

    return {
        table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608
        for table in ("tickets", "access_requests", "approvals", "onboarding_tasks")
    }


RACHEL_WEBEX = {
    "employee_id": "E010",
    "system_id": "S001",
    "access_level": "standard_user",
    "business_justification": "Customer onboarding calls require Webex.",
    "idempotency_key": "eval:S8:E010:S001:standard_user",
}


def test_replaying_rachels_request_creates_no_second_row(db):
    """The measured case. `AR001` exists; asking again must not make `AR005`."""

    before = counts(db)

    first = writes.create_access_request(db, **RACHEL_WEBEX)
    second = writes.create_access_request(db, **RACHEL_WEBEX)
    third = writes.create_access_request(db, **RACHEL_WEBEX)

    assert counts(db) == before, "a replay moved a row count"
    assert first.entity_id == second.entity_id == third.entity_id == "AR001"
    assert not any(r.created for r in (first, second, third))


def test_a_new_request_is_written_once_and_only_once(db):
    """Idempotency must not collapse into "never write". The first call writes."""

    before = counts(db)
    args = {
        "employee_id": "E002",
        "system_id": "S004",
        "access_level": "repo_contributor",
        "business_justification": "Connector work.",
        "idempotency_key": "eval:new:E002:S004",
    }

    first = writes.create_access_request(db, **args)
    after_first = counts(db)
    for _ in range(3):
        writes.create_access_request(db, **args)

    assert first.created is True
    assert after_first["access_requests"] == before["access_requests"] + 1
    assert counts(db) == after_first, "a replay of a new request wrote again"


def test_replaying_a_ticket_creates_no_second_row(db):
    before = counts(db)
    args = {
        "employee_id": "E010",
        "category": "Access",
        "subcategory": "Webex license",
        "subject": "Webex access still not working",
        "description": "Follow-up on the existing report.",
        "related_system_id": "S001",
        "idempotency_key": "eval:S8:E010:S001:ticket",
    }

    for _ in range(3):
        result = writes.create_ticket(db, **args)

    assert result.entity_id == "T001", "the existing open ticket was not reused"
    assert counts(db) == before


def test_replaying_an_approval_request_creates_no_second_row(db):
    before = counts(db)
    args = {
        "request_type": "access_request",
        "request_id": "AR004",
        "approver_id": "E006",
        "reason": "IT Manager decision required.",
        "idempotency_key": "eval:AR004:E006",
    }

    first = writes.request_approval(db, **args)
    after_first = counts(db)
    for _ in range(3):
        writes.request_approval(db, **args)

    assert first.created is True
    assert after_first["approvals"] == before["approvals"] + 1
    assert counts(db) == after_first


def test_recording_the_same_decision_twice_changes_nothing(db):
    """A resume after a restart legitimately replays the decision."""

    approval = writes.request_approval(
        db, request_type="access_request", request_id="AR004", approver_id="E006",
        reason="Please decide.", idempotency_key="eval:decision:AR004",
    )
    args = {"approval_id": approval.entity_id, "decided_by": "E006",
            "approved": True, "reason": "Seat freed by a leaver."}

    writes.record_decision(db, **args)
    after = counts(db)
    statuses = {
        db.execute(
            "SELECT status FROM approvals WHERE approval_id = ?", (approval.entity_id,)
        ).fetchone()[0]
    }

    for _ in range(3):
        writes.record_decision(db, **args)

    assert counts(db) == after
    assert {
        db.execute(
            "SELECT status FROM approvals WHERE approval_id = ?", (approval.entity_id,)
        ).fetchone()[0]
    } == statuses


def test_the_whole_case_replayed_end_to_end_moves_nothing(db):
    """Rachel's case start to finish, three times over.

    The shape the specification asks a reviewer to be able to see: run the case twice and
    assert the row counts are unchanged.
    """

    def run_case() -> None:
        writes.create_ticket(
            db, employee_id="E010", category="Access", subcategory="Webex license",
            subject="Webex says my account is not licensed",
            description="Reported by the employee.", related_system_id="S001",
            idempotency_key="eval:case:E010:ticket",
        )
        writes.create_access_request(db, **{**RACHEL_WEBEX, "idempotency_key": "eval:case:E010:ar"})
        writes.request_approval(
            db, request_type="access_request", request_id="AR001", approver_id="E006",
            reason="Seat expansion needs the IT Manager.",
            idempotency_key="eval:case:AR001:E006",
        )

    run_case()
    after_once = counts(db)
    run_case()
    run_case()

    assert counts(db) == after_once, "replaying the case moved a row count"
