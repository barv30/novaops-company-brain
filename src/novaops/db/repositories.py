"""Reads over the business database.

Plain functions over a connection rather than classes: there is no per-repository state
worth holding, and a function is easier to call from a tool, a test and a script alike.

Two things here are not obvious from the schema and matter to the workflows:

**There is no employee-to-seat table.** `software_subscriptions` records `seat_limit`
and `active_seats` as counts. `systems_allowed_by_role` on the employee row lists who is
*entitled* to a system — which looks exactly like the answer to "who holds a seat" and
is not. `seat_assignment_gap()` exists so that the honest answer to `W-I-02` is grounded
in a tool result rather than in the model showing restraint.

**Status values are not uniform.** `approvals.status` is `needed` in one seeded row and
`pending` in another; neither means approved. Anything reading a status treats it as a
strict allowlist rather than checking for a negative — see `write_gate`.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

# Approval statuses that count as a recorded decision to proceed. Everything else —
# `needed`, `pending`, `rejected`, a value nobody has thought of yet — is not approval.
# An allowlist fails closed; a denylist fails open the first time a new value appears.
APPROVED_STATUSES = frozenset({"approved", "granted"})

# Access-request statuses that mean the request is still live, so a new request for the
# same employee and system should reuse it rather than create a second row.
OPEN_REQUEST_STATUSES = frozenset(
    {"pending_approval", "blocked", "planned", "open", "in_progress"}
)

OPEN_TICKET_STATUSES = frozenset(
    {"open", "pending_approval", "waiting_on_manager", "planned", "in_progress"}
)


def _rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(row) for row in cursor.fetchall()]


def _row(cursor: sqlite3.Cursor) -> dict[str, Any] | None:
    row = cursor.fetchone()
    return dict(row) if row else None


# 1. PEOPLE ------------------------------------------------------------------

def all_employees(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every employee row. Used to derive manager status — see identity.py."""

    return _rows(connection.execute("SELECT * FROM employees ORDER BY employee_id"))


def get_employee(connection: sqlite3.Connection, employee_id: str) -> dict[str, Any] | None:
    return _row(
        connection.execute("SELECT * FROM employees WHERE employee_id = ?", (employee_id,))
    )


def find_employees(connection: sqlite3.Connection, name: str) -> list[dict[str, Any]]:
    """Employees matching a name fragment, case-insensitively.

    Returns a list, never a single row, because the dataset contains a deliberate
    identity collision: E001 Maya Cohen and E020 Maya Rosen. A caller that wants one
    person must disambiguate by id; silently returning the first match is how the wrong
    person's onboarding gets reported.
    """

    return _rows(
        connection.execute(
            "SELECT * FROM employees WHERE lower(full_name) LIKE ? ORDER BY employee_id",
            (f"%{name.lower()}%",),
        )
    )


def direct_reports(connection: sqlite3.Connection, manager_id: str) -> list[dict[str, Any]]:
    return _rows(
        connection.execute(
            "SELECT * FROM employees WHERE manager_id = ? ORDER BY employee_id",
            (manager_id,),
        )
    )


def entitled_systems(employee: dict[str, Any]) -> list[str]:
    """Parse `systems_allowed_by_role`, which is stored as a JSON string.

    Entitlement is not access. This tells you the role permits a system, not that the
    person has it — see `seat_assignment_gap`.
    """

    raw = employee.get("systems_allowed_by_role") or "[]"
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return [part.strip() for part in str(raw).split(",") if part.strip()]
    return parsed if isinstance(parsed, list) else []


# 2. SYSTEMS, SUBSCRIPTIONS, CONTRACTS ---------------------------------------

def get_system(connection: sqlite3.Connection, system_id: str) -> dict[str, Any] | None:
    return _row(connection.execute("SELECT * FROM systems WHERE system_id = ?", (system_id,)))


def find_system(connection: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    """Look a system up by name, e.g. 'Webex' -> S001."""

    return _row(
        connection.execute(
            "SELECT * FROM systems WHERE lower(system_name) LIKE ? ORDER BY system_id",
            (f"%{name.lower()}%",),
        )
    )


def get_subscription_for_system(
    connection: sqlite3.Connection, system_id: str
) -> dict[str, Any] | None:
    return _row(
        connection.execute(
            "SELECT * FROM software_subscriptions WHERE system_id = ?", (system_id,)
        )
    )


def get_contract(connection: sqlite3.Connection, contract_id: str) -> dict[str, Any] | None:
    return _row(
        connection.execute("SELECT * FROM contracts WHERE contract_id = ?", (contract_id,))
    )


def seat_position(connection: sqlite3.Connection, system_name: str) -> dict[str, Any] | None:
    """Seats, limit and the contract behind them, for one system.

    Joins the subscription to its contract because the limit is a *contractual* fact:
    reporting "42 of 40" without naming the agreement it comes from is an assertion, not
    evidence.
    """

    system = find_system(connection, system_name)
    if system is None:
        return None
    subscription = get_subscription_for_system(connection, system["system_id"])
    if subscription is None:
        return None
    contract = get_contract(connection, subscription["contract_id"])
    over = subscription["active_seats"] - subscription["seat_limit"]
    return {
        "system_id": system["system_id"],
        "system_name": system["system_name"],
        "subscription_id": subscription["subscription_id"],
        "seat_limit": subscription["seat_limit"],
        "active_seats": subscription["active_seats"],
        "seats_over_limit": max(0, over),
        "at_or_over_limit": over >= 0,
        "contract_id": subscription["contract_id"],
        "contract_document": contract["document_path"] if contract else None,
        "renewal_date": subscription["renewal_date"],
    }


def seat_assignment_gap(connection: sqlite3.Connection, system_name: str) -> dict[str, Any]:
    """Who holds a seat on this system — and why that cannot be answered.

    `W-I-02` asks a manager which of her team holds a Webex seat. There is no
    employee-to-seat table, so the question has no precise answer. The wrong move is to
    read `systems_allowed_by_role` and report entitlement as if it were access: that
    produces a fluent, citable reply that says two blocked people have access.

    This function represents the missing capability explicitly. It returns
    `seat_assignments_recorded: False` plus what *is* on record, so the honest answer is
    grounded in a tool result rather than in the model choosing to be modest.
    """

    position = seat_position(connection, system_name)
    system = find_system(connection, system_name)
    system_id = system["system_id"] if system else None

    entitled = []
    if system:
        for employee in all_employees(connection):
            if system["system_name"] in entitled_systems(employee):
                entitled.append(
                    {
                        "employee_id": employee["employee_id"],
                        "full_name": employee["full_name"],
                        "entitled_by_role": True,
                    }
                )

    requests = (
        _rows(
            connection.execute(
                "SELECT * FROM access_requests WHERE system_id = ? ORDER BY request_id",
                (system_id,),
            )
        )
        if system_id
        else []
    )
    tasks = (
        _rows(
            connection.execute(
                "SELECT * FROM onboarding_tasks WHERE related_system_id = ? ORDER BY task_id",
                (system_id,),
            )
        )
        if system_id
        else []
    )

    return {
        "system": system_name,
        "seat_assignments_recorded": False,
        "why": (
            "software_subscriptions records seat_limit and active_seats as counts only. "
            "There is no employee-to-seat table, so which individuals hold a seat is not "
            "recorded anywhere in this database."
        ),
        "do_not_substitute": (
            "systems_allowed_by_role lists role entitlement, not seat assignment. "
            "Reporting it as access would state that blocked employees have access."
        ),
        "seat_counts": position,
        "entitled_by_role": entitled,
        "access_requests_on_record": requests,
        "onboarding_tasks_on_record": tasks,
    }


# 3. TICKETS, REQUESTS, APPROVALS --------------------------------------------

def list_employee_tickets(
    connection: sqlite3.Connection, employee_id: str, status: str | None = None
) -> list[dict[str, Any]]:
    if status:
        return _rows(
            connection.execute(
                "SELECT * FROM tickets WHERE employee_id = ? AND status = ? ORDER BY ticket_id",
                (employee_id, status),
            )
        )
    return _rows(
        connection.execute(
            "SELECT * FROM tickets WHERE employee_id = ? ORDER BY ticket_id", (employee_id,)
        )
    )


def find_open_ticket(
    connection: sqlite3.Connection, employee_id: str, system_id: str
) -> dict[str, Any] | None:
    """An existing live ticket for this employee and system, if there is one.

    This is the natural-key match that makes `T001` get reused. Rachel Stein already has
    an open Webex ticket; creating a second one is a duplicate write, not diligence.
    """

    for ticket in list_employee_tickets(connection, employee_id):
        if ticket["related_system_id"] == system_id and ticket["status"] in OPEN_TICKET_STATUSES:
            return ticket
    return None


def list_access_requests(
    connection: sqlite3.Connection, employee_id: str
) -> list[dict[str, Any]]:
    return _rows(
        connection.execute(
            "SELECT * FROM access_requests WHERE employee_id = ? ORDER BY request_id",
            (employee_id,),
        )
    )


def find_open_access_request(
    connection: sqlite3.Connection, employee_id: str, system_id: str
) -> dict[str, Any] | None:
    """An existing live access request for this employee and system.

    The natural-key match that makes `AR001` get reused. Note that `AR001.status` is
    already `blocked` with the reason recorded — the database knows the answer, and the
    workflow's job is to find it and report it, not to rediscover it.
    """

    for request in list_access_requests(connection, employee_id):
        if request["system_id"] == system_id and request["status"] in OPEN_REQUEST_STATUSES:
            return request
    return None


def approvals_for(
    connection: sqlite3.Connection, request_id: str, request_type: str | None = None
) -> list[dict[str, Any]]:
    """Every approval attached to a request.

    Returns a list because a request can require more than one: `AR001` carries `AP001`
    (IT Manager) and `AP002` (Finance), both `status=needed`. "Is it approved?" is
    therefore not one row lookup, which is exactly the mistake the write gate must not
    make.
    """

    if request_type:
        return _rows(
            connection.execute(
                "SELECT * FROM approvals WHERE request_id = ? AND request_type = ? "
                "ORDER BY approval_id",
                (request_id, request_type),
            )
        )
    return _rows(
        connection.execute(
            "SELECT * FROM approvals WHERE request_id = ? ORDER BY approval_id", (request_id,)
        )
    )


def outstanding_approvals(
    connection: sqlite3.Connection, request_id: str
) -> list[dict[str, Any]]:
    """The approvals on this request that have not been granted."""

    return [
        approval
        for approval in approvals_for(connection, request_id)
        if approval["status"] not in APPROVED_STATUSES
    ]


# 4. ONBOARDING AND ASSETS ---------------------------------------------------

def list_onboarding_tasks(
    connection: sqlite3.Connection, employee_id: str
) -> list[dict[str, Any]]:
    return _rows(
        connection.execute(
            "SELECT * FROM onboarding_tasks WHERE employee_id = ? ORDER BY task_id",
            (employee_id,),
        )
    )


def check_asset_inventory(
    connection: sqlite3.Connection, asset_type: str = "", location: str = ""
) -> list[dict[str, Any]]:
    clauses, params = [], []
    if asset_type:
        clauses.append("lower(asset_type) LIKE ?")
        params.append(f"%{asset_type.lower()}%")
    if location:
        clauses.append("lower(location) LIKE ?")
        params.append(f"%{location.lower()}%")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return _rows(
        connection.execute(f"SELECT * FROM assets {where} ORDER BY asset_id", params)  # noqa: S608
    )


def assets_for_employee(
    connection: sqlite3.Connection, employee_id: str
) -> list[dict[str, Any]]:
    return _rows(
        connection.execute(
            "SELECT * FROM assets WHERE employee_id = ? ORDER BY asset_id", (employee_id,)
        )
    )
