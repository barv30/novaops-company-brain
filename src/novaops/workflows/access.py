"""Diagnosing an access request from the records, in code.

Same argument as `onboarding.py`: everything needed is already in `employees`,
`software_subscriptions`, `contracts`, `tickets`, `access_requests` and `approvals`, so
the diagnosis is assembled rather than asked for. The model reads the conversation and
explains the result; it does not decide what the records say.

Here the argument is stronger than for the checklist, because the failure mode is worse.
`AccessDecision` must separate **observed facts** from **actions actually taken** from
**recommended next steps**, and must never claim access was granted. Those are exactly
the distinctions a fluent paragraph blurs. Built from rows, they cannot blur: an action
only appears if a write returned a record id, and `Outcome.GRANTED` is refused by the
contract unless something is in `actions_taken`.

What the Webex case turns on, all of it read rather than inferred:

* Rachel Stein (`E010`) is eligible by department — her role entitles her to Webex;
* `SUB001` runs 42 active seats against a 40-seat contractual limit, set by `C001`;
* `T001` and `AR001` already exist, so the correct action is to reuse them;
* `AP001` (IT Manager) and `AP002` (Finance) are outstanding, so nothing may be released.

The blocker is organizational, not personal. Saying so is the answer.
"""

from __future__ import annotations

import sqlite3

from novaops.contracts.access import AccessDecision, ActionTaken, NextStep, Outcome
from novaops.contracts.common import Citation, Evidence, Status
from novaops.db import repositories


def _entitled(employee: dict, system_name: str) -> bool:
    allowed = [s.lower() for s in repositories.entitled_systems(employee)]
    return any(system_name.lower() in item for item in allowed)


def diagnose_access(
    connection: sqlite3.Connection,
    employee_id: str,
    system_name: str,
) -> AccessDecision:
    """Work out where an access request stands, and why.

    Returns a decision, never performs one. Nothing here writes: the actions it reports
    are records that already existed, found by looking. Filing anything is the write
    gate's business, and it answers to a recorded approval rather than to this function.
    """

    employee = repositories.get_employee(connection, employee_id)
    if employee is None:
        raise KeyError(f"No employee with id {employee_id!r}")

    system = repositories.find_system(connection, system_name)
    if system is None:
        return AccessDecision(
            employee_id=employee_id,
            system=system_name,
            outcome=Outcome.REFUSED,
            status=Status.BLOCKED,
            blocking_reason=f"No system called {system_name!r} is on record.",
        )

    facts: list[Evidence] = []
    actions: list[ActionTaken] = []
    steps: list[NextStep] = []

    # 1. Is this person eligible at all? Entitlement is not access, but its absence is
    #    a different refusal from a seat limit and must not be reported as the same one.
    entitled = _entitled(employee, system["system_name"])
    facts.append(
        Evidence(
            statement=(
                f"{employee['full_name']} ({employee_id}) is {employee['role']} in "
                f"{employee['department_id']} and is "
                f"{'entitled' if entitled else 'NOT entitled'} to "
                f"{system['system_name']} by role."
            ),
            citations=[Citation(record=employee_id, source="database/employees")],
        )
    )

    # 2. The seat position, with the contract that sets the limit.
    position = repositories.seat_position(connection, system["system_name"])
    over_limit = bool(position and position["at_or_over_limit"])
    if position:
        facts.append(
            Evidence(
                statement=(
                    f"{position['system_name']} subscription {position['subscription_id']} "
                    f"is at {position['active_seats']} active seats against a "
                    f"{position['seat_limit']}-seat contractual limit "
                    f"({position['contract_id']}); renewal {position['renewal_date']}."
                ),
                citations=[
                    Citation(
                        record=position["subscription_id"],
                        source=position["contract_document"] or "database/software_subscriptions",
                    )
                ],
            )
        )

    # 3. Records that already exist. Finding one is not filing one — the disposition
    #    says `reused`, and the answer must say the same.
    ticket = repositories.find_open_ticket(connection, employee_id, system["system_id"])
    if ticket:
        actions.append(
            ActionTaken(
                action="referenced the existing open ticket",
                record_id=ticket["ticket_id"],
                record_type="ticket",
                reused_existing=True,
            )
        )
        facts.append(
            Evidence(
                statement=(
                    f"Ticket {ticket['ticket_id']} is {ticket['status']}: "
                    f"{ticket['subject']}."
                ),
                citations=[Citation(record=ticket["ticket_id"], source="database/tickets")],
            )
        )

    request = repositories.find_open_access_request(connection, employee_id, system["system_id"])
    outstanding: list[dict] = []
    if request:
        actions.append(
            ActionTaken(
                action="referenced the existing access request",
                record_id=request["request_id"],
                record_type="access_request",
                reused_existing=True,
            )
        )
        facts.append(
            Evidence(
                statement=(
                    f"Access request {request['request_id']} is {request['status']}"
                    + (f": {request['decision_reason']}" if request.get("decision_reason") else "")
                ),
                citations=[
                    Citation(record=request["request_id"], source="database/access_requests")
                ],
            )
        )
        outstanding = repositories.outstanding_approvals(connection, request["request_id"])
        for approval in outstanding:
            approver = repositories.get_employee(connection, approval["approver_id"])
            facts.append(
                Evidence(
                    statement=(
                        f"Approval {approval['approval_id']} is {approval['status']} from "
                        f"{approver['full_name'] if approver else approval['approver_id']}"
                        f" ({approval['approver_id']}): {approval['decision_reason']}"
                    ),
                    citations=[
                        Citation(record=approval["approval_id"], source="database/approvals")
                    ],
                )
            )

    # 4. What governs. Order matters: ineligibility is about the person, a seat limit is
    #    not, and reporting the second as the first is the error the case exists to catch.
    if not entitled:
        outcome, status = Outcome.REFUSED, Status.BLOCKED
        reason = (
            f"{employee['full_name']} is not entitled to {system['system_name']} by role. "
            f"This is an eligibility question, not a capacity one."
        )
    elif over_limit and position:
        outcome, status = Outcome.BLOCKED, Status.BLOCKED
        reason = (
            f"{position['system_name']} is at {position['active_seats']} active seats "
            f"against a {position['seat_limit']}-seat contractual limit "
            f"({position['contract_id']}). {employee['full_name']} is eligible; the "
            f"subscription is full. This is a capacity constraint on the subscription, "
            f"not a problem with this employee's account."
        )
    elif outstanding:
        outcome, status = Outcome.AWAITING_APPROVAL, Status.PENDING
        reason = (
            "Eligible and within seat limits, but "
            + ", ".join(
                f"{a['approval_id']} ({a['approver_id']}) is {a['status']}" for a in outstanding
            )
            + "."
        )
    else:
        outcome, status = Outcome.RECOMMENDED, Status.COMPLETED
        reason = None

    # 5. What should happen next, and who owns it.
    for approval in outstanding:
        steps.append(
            NextStep(
                step=(
                    f"{approval['approver_id']} decides {approval['approval_id']} "
                    f"({approval['request_type']})"
                ),
                owner=approval["approver_id"],
                blocked_on=approval["decision_reason"],
            )
        )
    if over_limit and position:
        steps.append(
            NextStep(
                step=(
                    f"Reclaim a seat or approve an expansion of {position['subscription_id']} "
                    f"beyond {position['seat_limit']} seats"
                ),
                owner="UG_IT",
                blocked_on=f"{position['seats_over_limit']} seats over the contractual limit",
            )
        )
    if not outstanding and not over_limit and entitled:
        steps.append(
            NextStep(step="File an access request for a human to approve", owner="UG_IT")
        )

    return AccessDecision(
        employee_id=employee_id,
        system=system["system_name"],
        outcome=outcome,
        status=status,
        observed_facts=facts,
        actions_taken=actions,
        recommended_next_steps=steps,
        blocking_reason=reason,
    )


def team_system_access(
    connection: sqlite3.Connection,
    manager_id: str,
    system_name: str,
) -> dict:
    """What can be said about a manager's team and one system — and what cannot.

    `W-I-02`: Yael Romano asks "which of my team members has a Webex seat?". There is no
    precise answer, and that is the test. `software_subscriptions` records seats as
    counts; no table says who holds one. `systems_allowed_by_role` says who is
    *entitled*, which looks exactly like the answer and is not — read that way it
    reports Webex for Rachel Stein and Maya Cohen, the two people whose access is
    actually blocked. Measured: the agent said precisely that on its first run.

    So the gap is stated by the tool rather than left to the model's restraint, and each
    team member gets what the records actually show: an access request and its state,
    an onboarding task and its state, or no entitlement at all.
    """

    system = repositories.find_system(connection, system_name)
    reports = repositories.direct_reports(connection, manager_id)
    position = repositories.seat_position(connection, system_name) if system else None

    members = []
    for employee in reports:
        entitled = _entitled(employee, system["system_name"]) if system else False
        on_record: list[str] = []
        evidence: list[str] = []

        if system:
            request = repositories.find_open_access_request(
                connection, employee["employee_id"], system["system_id"]
            )
            if request:
                on_record.append(
                    f"access request {request['request_id']} is {request['status']}"
                    + (f" ({request['decision_reason']})" if request.get("decision_reason") else "")
                )
                evidence.append(request["request_id"])
            for task in repositories.list_onboarding_tasks(connection, employee["employee_id"]):
                if task.get("related_system_id") == system["system_id"]:
                    on_record.append(
                        f"onboarding task {task['task_id']} is {task['status']}: "
                        f"{task['description']}"
                    )
                    evidence.append(task["task_id"])

        if not entitled:
            on_record.insert(0, f"not entitled to {system_name} by role")

        members.append(
            {
                "employee_id": employee["employee_id"],
                "name": employee["full_name"],
                "role": employee["role"],
                "entitled_by_role": entitled,
                "holds_a_seat": "not recorded — no table assigns seats to people",
                "what_the_records_show": on_record or ["nothing on record for this system"],
                "evidence": evidence or [employee["employee_id"]],
            }
        )

    return {
        "system": system_name,
        "manager_id": manager_id,
        "seat_assignments_recorded": False,
        "why": (
            "Seats are recorded as counts on the subscription only. No record says which "
            "individual holds a seat, so 'who has a seat' cannot be answered from the data. "
            "Entitlement by role is NOT a seat and must not be reported as one."
        ),
        "seat_counts": (
            f"{position['active_seats']} active of {position['seat_limit']} "
            f"({position['subscription_id']}, contract {position['contract_id']})"
            if position else "unknown"
        ),
        "team": members,
    }
