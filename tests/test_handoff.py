"""The Maya → Webex handoff: a typed `AccessIntent`, built by application code.

§6.5's minimum: the subject employee, the system requested, a justification, and the id
of the conversation it came from. These tests run against the real seeded records and
call no model.
"""

from __future__ import annotations

import shutil
import sqlite3

import pytest
from scripts.reseed import reseed

from novaops import config
from novaops.contracts.access import Outcome
from novaops.db.connection import connect
from novaops.workflows.handoff import prepare_handoff


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    reseed()
    master = tmp_path_factory.mktemp("handoff") / "novaops.db"
    shutil.copy(config.BUSINESS_DB, master)
    return master


@pytest.fixture
def db(seeded, tmp_path) -> sqlite3.Connection:
    path = tmp_path / "novaops.db"
    shutil.copy(seeded, path)
    connection = connect(path)
    yield connection
    connection.close()


def maya_handoff(db, **overrides):
    args = {
        "subject": "Maya Cohen",
        "system": "Webex",
        "justification": "Customer Success onboarding needs Webex for client calls.",
        "conversation_id": "M-S-01:test",
        "requested_by": "E004",
    }
    args.update(overrides)
    return prepare_handoff(db, **args)


def test_the_handoff_carries_what_the_spec_requires(db):
    handoff = maya_handoff(db)
    intent = handoff.intent
    assert intent.subject_employee_id == "E001"
    assert intent.system == "Webex"
    assert intent.justification
    assert intent.conversation_id == "M-S-01:test"
    assert intent.requested_by == "E004"


def test_it_reports_what_webex_would_make_of_it(db):
    """'Returns its pending result': Maya can say truthfully what would happen."""

    handoff = maya_handoff(db)
    assert handoff.webex_outcome is Outcome.BLOCKED
    assert "42 active seats" in handoff.blocking_reason


def test_the_summary_never_says_filed(db):
    summary = maya_handoff(db).summary()
    assert summary.startswith("Prepared, not filed")


def test_an_existing_request_is_named_rather_than_duplicated(db):
    """For Rachel the intent resolves to AR001."""

    handoff = maya_handoff(db, subject="Rachel Stein")
    assert handoff.existing_request_id == "AR001"
    assert "nothing new would be created" in handoff.summary()


def test_an_ambiguous_subject_builds_nothing(db):
    """Two employees are called Maya. A handoff about the wrong one is worse than none."""

    assert maya_handoff(db, subject="Maya") is None


def test_no_system_builds_nothing(db):
    assert maya_handoff(db, system=None) is None


def test_the_handoff_writes_nothing(db):
    """Maya proposes; she never files. Asserted on row counts."""

    def counts() -> dict:
        return {
            t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]  # noqa: S608
            for t in ("tickets", "access_requests", "approvals", "audit_log")
        }

    before = counts()
    for _ in range(3):
        maya_handoff(db)
    assert counts() == before


def test_replaying_it_gives_the_same_idempotency_key(db):
    """The conversation id is in the key, so a replay resolves to the same write."""

    first = maya_handoff(db).intent.idempotency_key()
    second = maya_handoff(db).intent.idempotency_key()
    assert first == second
