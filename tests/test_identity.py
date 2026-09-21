"""The permission boundary's foundation, checked against the real dataset.

These tests read `seed.json` rather than fixtures. The rules they cover are claims about
*this* data — that three of the five managers are `UG_REGULAR`, that 70 documents carry no
audience — and a fixture would let the data drift out from under them silently.
"""

from __future__ import annotations

import json

import pytest

from novaops import config
from novaops.identity import Caller, build_caller, managers_in


@pytest.fixture(scope="module")
def employees() -> list[dict]:
    return json.loads(config.SEED_JSON.read_text(encoding="utf-8"))["employees"]


# 1. MANAGER DERIVATION (ADR-005) --------------------------------------------

def test_managers_are_derived_from_manager_id(employees):
    """The five people who manage someone, by structure rather than by title."""

    assert managers_in(employees) == {"E004", "E006", "E012", "E013", "E018"}


def test_user_group_cannot_derive_manager(employees):
    """The reason ADR-005 exists.

    If `user_group` could derive it, this test would be pointless — and the permission
    boundary could be one field lookup. It cannot: three managers sit in `UG_REGULAR`
    alongside every regular employee.
    """

    by_id = {row["employee_id"]: row for row in employees}
    regular_managers = {
        eid for eid in managers_in(employees) if by_id[eid]["user_group_id"] == "UG_REGULAR"
    }
    assert regular_managers == {"E012", "E013", "E018"}


@pytest.mark.parametrize(
    ("employee_id", "expected_group", "expected_manager"),
    [
        ("E018", "UG_REGULAR", True),    # Customer Success Director — manages, despite the group
        ("E004", "UG_HR", True),         # People Operations Lead
        ("E006", "UG_IT", True),         # IT Manager
        ("E001", "UG_REGULAR", False),   # Maya Cohen — the S9 permission trap
        ("E010", "UG_REGULAR", False),   # Rachel Stein — the Webex case
        ("E020", "UG_HR", False),        # HR Coordinator, manages nobody
    ],
)
def test_build_caller(employees, employee_id, expected_group, expected_manager):
    caller = build_caller(employee_id, employees)
    assert caller.user_group == expected_group
    assert caller.is_manager is expected_manager


def test_unknown_caller_raises_rather_than_degrading(employees):
    """An unknown id is a harness bug, not a caller with no permissions."""

    with pytest.raises(KeyError):
        build_caller("E999", employees)


# 2. AUDIENCE RESOLUTION (ADR-004) -------------------------------------------

def test_absent_audience_is_unrestricted():
    """The single most consequential rule in the boundary.

    70 of 102 documents carry no audience field. Treating absent metadata as 'deny'
    would block all of them and fail every case in the evaluation set.
    """

    maya = Caller("E001", "UG_REGULAR", is_manager=False)
    assert maya.may_read(None) is True


def test_manager_playbook_is_unreachable_for_a_non_manager():
    """S9 turns 1 and 4. Maya Cohen asks twice; both must fail closed."""

    maya = Caller("E001", "UG_REGULAR", is_manager=False)
    assert maya.may_read("manager") is False
    assert maya.may_read("all") is True


def test_manager_playbook_is_reachable_for_a_manager_in_ug_regular():
    """Yael Romano is UG_REGULAR and manages people. She may read the playbook."""

    yael = Caller("E018", "UG_REGULAR", is_manager=True)
    assert yael.may_read("manager") is True


def test_unrecognised_audience_fails_closed():
    """A new restricted collection must fail closed, not silently open."""

    manager = Caller("E006", "UG_IT", is_manager=True)
    assert manager.may_read("executive") is False
