"""Maya's checklist, built from the real seeded records.

The spec's minimum for `OnboardingChecklist`: the items, each with its status and at
least one citation; anything blocked carries the reason; and the overall status. These
tests check that against the data the evaluation set runs on, not against a fixture.

No model is involved. That is the design — see `workflows/onboarding.py`.
"""

from __future__ import annotations

import shutil
import sqlite3

import pytest
from scripts.reseed import reseed

from novaops import config
from novaops.contracts.common import Status
from novaops.contracts.onboarding import ItemStatus
from novaops.db.connection import connect
from novaops.workflows.onboarding import build_checklist


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    reseed()
    master = tmp_path_factory.mktemp("checklist") / "novaops.db"
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
def maya(db):
    return build_checklist(db, "E001", constraints=["Q3 SaaS freeze applies"])


# 1. THE SHAPE THE SPEC ASKS FOR --------------------------------------------

def test_every_item_carries_at_least_one_citation(maya):
    """An uncited item is not evidence-backed, and the contract refuses to build one.

    Asserted anyway: the guarantee is worth having a test for, because it is the one
    property a reader is most likely to spot-check.
    """

    assert maya.items
    for item in maya.items:
        assert item.citations, item.name


def test_the_checklist_is_for_the_right_maya(maya):
    """E001 Maya Cohen, not E020 Maya Rosen."""

    assert maya.employee_id == "E001"
    assert maya.employee_name == "Maya Cohen"
    assert maya.start_date == "2026-08-01"


def test_constraints_stated_by_the_caller_are_recorded(maya):
    """Turn 1's spend freeze has to be visible in the output, not only honoured in it."""

    assert any("freeze" in c.lower() for c in maya.constraints_applied)


def test_the_run_completed_even_though_items_are_blocked(maya):
    """`status` is where the workflow got to, not what happened to the request."""

    assert maya.status is Status.COMPLETED
    assert maya.blocked_items


# 2. THE WEBEX BLOCKER, NAMED PROPERLY ---------------------------------------

def test_webex_is_blocked_and_says_why(maya):
    webex = [i for i in maya.items if "webex" in i.name.lower()]
    assert webex, "no Webex item on the checklist"
    item = webex[0]

    assert item.status is ItemStatus.BLOCKED
    assert item.blocked_reason
    assert "42" in item.blocked_reason
    assert "40" in item.blocked_reason


def test_the_blocked_reason_is_organizational_not_personal(maya):
    """The whole point of the case: it is a seat limit, not Maya's account."""

    item = next(i for i in maya.items if "webex" in i.name.lower())
    assert "not a problem with this employee" in item.blocked_reason.lower()


def test_the_blocker_cites_the_contract_not_just_the_task(maya):
    """A seat limit is a contractual fact. Reporting it without the agreement that
    sets it is an assertion rather than evidence."""

    item = next(i for i in maya.items if "webex" in i.name.lower())
    sources = " ".join(c.source or "" for c in item.citations)
    records = {c.record for c in item.citations}

    assert "contracts/" in sources, sources
    assert "SUB001" in records, records


def test_the_blocked_item_appears_in_unresolved(maya):
    assert any("OT005" in u for u in maya.unresolved)


# 3. THE REST OF THE CHECKLIST ------------------------------------------------

def test_completed_work_is_reported_as_done(maya):
    """OT001 — the signed offer letter — is already complete."""

    done = [i for i in maya.items if i.status is ItemStatus.DONE]
    assert any("offer letter" in i.name.lower() for i in done)


def test_reserved_equipment_is_reported(maya):
    """A005 is a MacBook sitting in Israel reserved for her.

    'A laptop is being arranged' would be less useful and less true than naming the
    machine that is physically waiting.
    """

    equipment = [i for i in maya.items if i.category == "equipment"]
    assert any("MacBook" in i.name for i in equipment), [i.name for i in equipment]
    assert any(c.record == "A005" for i in equipment for c in i.citations)


def test_inventory_items_are_distinguishable_from_the_task(maya):
    """Two items exist for the same laptop and must not be confused.

    OT003 is the provisioning task, due 2026-07-26. A005 is the physical machine sitting
    reserved. An inventory item named simply "Laptop — MacBook Pro 14" reads like the
    task minus its due date, and an answer then reports "no due date specified" while
    the date sits on the other item.
    """

    equipment = [i for i in maya.items if i.category == "equipment"]
    assert equipment
    for item in equipment:
        assert "reserved in inventory" in item.name or "assigned" in item.name, item.name
        assert item.due_date is None, "inventory has no due date; the task carries it"

    task = next(i for i in maya.items if "prepare laptop" in i.name.lower())
    assert task.due_date == "2026-07-26"


def test_owner_groups_are_carried_through(maya):
    """Who has to act is part of the answer."""

    assert {i.owner_group for i in maya.items if i.owner_group} >= {"UG_IT", "UG_HR"}


def test_due_dates_survive(maya):
    okta = next(i for i in maya.items if "okta" in i.name.lower())
    assert okta.due_date == "2026-07-25"


# 4. FAILURE MODES ------------------------------------------------------------

def test_an_unknown_employee_raises_rather_than_returning_an_empty_checklist(db):
    """An empty checklist reads as 'nothing to do' — a different and worse answer."""

    with pytest.raises(KeyError):
        build_checklist(db, "E999")


def test_an_employee_with_no_tasks_says_so_rather_than_looking_complete(db):
    """E002 has no onboarding tasks, but does have an assigned laptop.

    Reporting that laptop as done is correct — it is assigned. What must not happen is
    the checklist reading as "one item, complete, nothing outstanding" when the truth is
    that no onboarding work is tracked for him at all. So the assertion is on
    `unresolved` saying so in as many words, not on the absence of done items.
    """

    checklist = build_checklist(db, "E002")

    assert checklist.unresolved, "silence would read as 'nothing outstanding'"
    note = " ".join(checklist.unresolved).lower()
    assert "no onboarding tasks are recorded" in note
    assert "not the same as work being complete" in note
