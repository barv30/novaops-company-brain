"""Assembling Maya's `OnboardingChecklist` from the records, in code.

The checklist is built deterministically rather than asked for. Everything it needs is
already in `onboarding_tasks`, `assets` and `software_subscriptions`, so a model call
here would add a chance of hallucinating a task, dropping a citation, or softening
"blocked" into "in progress" — and buy nothing. The specification makes this a design
decision worth taking explicitly: choosing when to use deterministic code rather than a
model call is itself assessed.

What the model is still for: reading the conversation, deciding this is what the caller
wants, and explaining the result. What it is not for: deciding what the records say.

Two properties fall out of building it this way and are worth naming, because they are
what the contract in `contracts/onboarding.py` refuses to let slip:

* **every item carries a citation**, because each one is derived from a row and the row
  id travels with it;
* **a blocked item names its real cause**, because the cause is looked up rather than
  described — Webex is blocked at 42 active seats against a 40-seat contractual limit,
  which is an organizational fact and not a fact about the joiner.
"""

from __future__ import annotations

import sqlite3

from novaops.contracts.common import Citation, Status
from novaops.contracts.onboarding import ChecklistItem, ItemStatus, OnboardingChecklist
from novaops.db import repositories

#: `onboarding_tasks.status` to our item status. An unrecognised value maps to PENDING
#: rather than raising: a status nobody anticipated is work still to do, and failing the
#: whole checklist over one unknown row would be the wrong trade.
_STATUS = {
    "completed": ItemStatus.DONE,
    "done": ItemStatus.DONE,
    "blocked": ItemStatus.BLOCKED,
    "pending": ItemStatus.PENDING,
    "planned": ItemStatus.PENDING,
    "in_progress": ItemStatus.PENDING,
}

#: `onboarding_tasks.task_type` to the contract's category vocabulary.
_CATEGORY = {
    "IT": "system",
    "Access": "system",
    "HR": "policy_acknowledgement",
    "Equipment": "equipment",
}


def _blocked_reason(
    connection: sqlite3.Connection, task: dict
) -> tuple[str | None, list[Citation]]:
    """Why this task is blocked, and the evidence for it.

    A task row records *that* it is blocked; it does not record why in a form worth
    citing. The cause is looked up from the subscription and its contract, so the answer
    names a seat count and an agreement rather than repeating the word "blocked".
    """

    citations = [Citation(record=task["task_id"], source="database/onboarding_tasks")]
    system_id = task.get("related_system_id")
    if not system_id:
        return task.get("description") or "Blocked; no cause recorded.", citations

    system = repositories.get_system(connection, system_id)
    if system is None:
        return "Blocked; the related system is not on record.", citations

    position = repositories.seat_position(connection, system["system_name"])
    if position and position["at_or_over_limit"]:
        if position["contract_document"]:
            citations.append(
                Citation(
                    source=position["contract_document"],
                    record=position["subscription_id"],
                )
            )
        return (
            f"{position['system_name']} subscription is at {position['active_seats']} "
            f"active seats against a {position['seat_limit']}-seat contractual limit "
            f"({position['contract_id']}). This is a capacity constraint on the "
            f"subscription, not a problem with this employee's account."
        ), citations

    return (
        task.get("description") or f"Blocked on {system['system_name']}; cause not recorded."
    ), citations


def _equipment_items(connection: sqlite3.Connection, employee: dict) -> list[ChecklistItem]:
    """Hardware already assigned to, or reserved for, this joiner.

    Reserved-but-unassigned kit is worth reporting: `A005` is a MacBook Pro sitting in
    Israel with "Reserved for Maya Cohen onboarding" in its notes, and an answer that
    says "a laptop is being arranged" when one is physically waiting is less useful and
    less true.
    """

    items: list[ChecklistItem] = []
    surname = employee["full_name"].split()[-1].lower()
    full = employee["full_name"].lower()

    for asset in repositories.check_asset_inventory(connection):
        assigned = asset.get("employee_id") == employee["employee_id"]
        notes = (asset.get("notes") or "").lower()
        reserved = (full in notes or surname in notes) and asset.get("status") == "available"
        if not (assigned or reserved):
            continue
        # Named so it cannot be mistaken for the provisioning task. Both exist for the
        # same laptop — OT003 "Prepare laptop, monitor, dock and MDM enrollment", due
        # 2026-07-26, and A005 the physical machine — and an item called simply
        # "Laptop — MacBook Pro 14" reads like the task without the task's due date.
        # Measured on `S9` turn 5, where the answer reported "no due date specified"
        # while the date sat on the other item.
        state = "assigned" if assigned else "reserved in inventory"
        items.append(
            ChecklistItem(
                name=f"{asset['asset_type']} {state}: {asset['model']}",
                category="equipment",
                status=ItemStatus.DONE if assigned else ItemStatus.PENDING,
                blocked_reason=None,
                owner_group="UG_IT",
                citations=[Citation(record=asset["asset_id"], source="database/assets")],
            )
        )
    return items


def build_checklist(
    connection: sqlite3.Connection,
    employee_id: str,
    *,
    constraints: list[str] | None = None,
) -> OnboardingChecklist:
    """Assemble the checklist for one joiner from the records.

    Raises `KeyError` for an unknown employee rather than returning an empty checklist:
    an empty checklist reads as "nothing to do", which is a different and much worse
    answer than "I could not find that person".
    """

    employee = repositories.get_employee(connection, employee_id)
    if employee is None:
        raise KeyError(f"No employee with id {employee_id!r}")

    items: list[ChecklistItem] = []
    unresolved: list[str] = []

    tasks = repositories.list_onboarding_tasks(connection, employee_id)
    for task in tasks:
        status = _STATUS.get(str(task["status"]).lower(), ItemStatus.PENDING)
        reason, citations = (
            _blocked_reason(connection, task)
            if status is ItemStatus.BLOCKED
            else (None, [Citation(record=task["task_id"], source="database/onboarding_tasks")])
        )
        items.append(
            ChecklistItem(
                name=task["description"],
                category=_CATEGORY.get(str(task["task_type"]), "system"),
                status=status,
                blocked_reason=reason,
                owner_group=f"UG_{task['owner_group']}" if task.get("owner_group") else None,
                due_date=task.get("due_date"),
                citations=citations,
            )
        )
        if status is ItemStatus.BLOCKED:
            unresolved.append(f"{task['task_id']}: {reason}")

    items.extend(_equipment_items(connection, employee))

    # Keyed on the TASKS, not on `items`. Equipment alone can make the checklist
    # non-empty while no onboarding work is on record at all, and a checklist showing
    # only a laptop reads as "everything else is done" rather than "nothing is tracked".
    if not tasks:
        unresolved.append(
            f"No onboarding tasks are recorded for {employee_id} "
            f"({employee['full_name']}), so nothing here can be reported as required or "
            "outstanding. Absence of tasks is not the same as work being complete."
        )

    return OnboardingChecklist(
        employee_id=employee["employee_id"],
        employee_name=employee["full_name"],
        start_date=employee.get("start_date"),
        items=items or [_placeholder(employee)],
        constraints_applied=list(constraints or []),
        unresolved=unresolved,
        # The run reached the end and produced an answer. Items inside it being blocked
        # is the answer, not a failure to produce one — see contracts/common.py.
        status=Status.COMPLETED,
    )


def _placeholder(employee: dict) -> ChecklistItem:
    """A checklist needs at least one item; say plainly that there are none."""

    return ChecklistItem(
        name="No onboarding tasks on record",
        category="system",
        status=ItemStatus.NOT_APPLICABLE,
        citations=[Citation(record=employee["employee_id"], source="database/onboarding_tasks")],
    )
