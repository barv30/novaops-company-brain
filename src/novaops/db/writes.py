"""Writes, and the three things that have to be true of every one of them.

**Reuse before create.** Every write first asks whether an equivalent record already
exists. Rachel Stein's `T001` and `AR001` were seeded before this system ran, so no
ledger entry could know about them; only a natural-key match finds them.

**Keyed, not appended.** Every write carries an idempotency key. Replaying the same
request returns the same row and writes nothing.

**Audited.** Each write appends to `audit_log`, so what this system did is visible in
the database and not only in a trace.

Every function returns a `WriteResult` that says plainly whether a row was *created*,
an existing row was *reused*, or the write was a *replay* of one we already did. That
distinction is what `AccessDecision.actions_taken` reports, and conflating it is how a
system comes to claim it filed something it merely found.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Literal

from novaops import config
from novaops.db import idempotency, repositories
from novaops.db.connection import next_id

Disposition = Literal["created", "reused", "replayed"]


@dataclass(frozen=True)
class WriteResult:
    """What a write actually did, and the row that proves it."""

    entity_type: str
    entity_id: str
    disposition: Disposition
    row: dict[str, Any]

    @property
    def created(self) -> bool:
        """True only when this call brought a new row into existence."""

        return self.disposition == "created"

    @property
    def reused_existing(self) -> bool:
        """True when an existing row answered the request. Not a failure — the goal."""

        return self.disposition in ("reused", "replayed")


def _audit(
    connection: sqlite3.Connection,
    *,
    entity_type: str,
    entity_id: str,
    event_type: str,
    actor: str | None,
    notes: str,
    request_id: str | None = None,
) -> None:
    """Append the audit row, tagging it with the request that caused it.

    The request id travels from the agent in an MCP header (see `mcp_server/context.py`)
    and lands here, so a write can be traced back to its request through the database as
    well as through the trace. That is what "survives across MCP calls" means in a form
    somebody can check without Langfuse open.
    """

    if request_id:
        notes = f"{notes} [request {request_id}]"
    connection.execute(
        "INSERT INTO audit_log "
        "(event_id, entity_type, entity_id, event_type, actor_employee_id, created_at, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            next_id(connection, "audit_log", "event_id", "EV"),
            entity_type,
            entity_id,
            event_type,
            actor,
            f"{config.AS_OF.isoformat()}T00:00:00Z",
            notes,
        ),
    )


def _replayed(
    connection: sqlite3.Connection,
    key: str,
    entity_type: str,
    table: str,
    id_column: str,
) -> WriteResult | None:
    """If this key has been handled before, return what it produced."""

    entry = idempotency.lookup(connection, key)
    if entry is None:
        return None
    row = connection.execute(
        f"SELECT * FROM {table} WHERE {id_column} = ?",  # noqa: S608
        (entry.entity_id,),
    ).fetchone()
    if row is None:
        # The ledger points at a row that no longer exists — a reseed between runs.
        # Treat the key as unused rather than returning a phantom record.
        return None
    return WriteResult(entity_type, entry.entity_id, "replayed", dict(row))


# 1. ACCESS REQUESTS ---------------------------------------------------------

def create_access_request(
    connection: sqlite3.Connection,
    *,
    employee_id: str,
    system_id: str,
    access_level: str,
    business_justification: str,
    idempotency_key: str,
    approver_id: str | None = None,
    actor: str | None = None,
    agent_request_id: str | None = None,
) -> WriteResult:
    """File an access request, reusing an existing one when there is one.

    `agent_request_id` is the *agent's* request id, not this row's. They are different
    things and the names have to say so: `request_id` below is the AR id this function
    mints, and tagging the audit row with that would record nothing a reader did not
    already know.

    Order matters: ledger first (did *we* already do this?), then natural-key match (did
    anyone?), then create. Checking the ledger first is what makes an exact replay cheap
    and exact; checking the match second is what makes a pre-existing record reusable.
    """

    replay = _replayed(
        connection, idempotency_key, "access_request", "access_requests", "request_id"
    )
    if replay is not None:
        return replay

    existing = repositories.find_open_access_request(connection, employee_id, system_id)
    if existing is not None:
        idempotency.record(
            connection,
            key=idempotency_key,
            entity_type="access_request",
            entity_id=existing["request_id"],
            reused=True,
        )
        return WriteResult("access_request", existing["request_id"], "reused", existing)

    request_id = next_id(connection, "access_requests", "request_id", "AR")
    connection.execute(
        "INSERT INTO access_requests "
        "(request_id, employee_id, system_id, access_level, business_justification, "
        " status, approver_id, created_at, decision_reason) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            request_id,
            employee_id,
            system_id,
            access_level,
            business_justification,
            "pending_approval",
            approver_id,
            f"{config.AS_OF.isoformat()}T00:00:00Z",
            None,
        ),
    )
    idempotency.record(
        connection,
        key=idempotency_key,
        entity_type="access_request",
        entity_id=request_id,
        reused=False,
    )
    _audit(
        connection,
        entity_type="access_request",
        entity_id=request_id,
        event_type="created",
        actor=actor,
        notes=f"Access request for {employee_id} on {system_id}.",
        request_id=agent_request_id,
    )
    row = dict(
        connection.execute(
            "SELECT * FROM access_requests WHERE request_id = ?", (request_id,)
        ).fetchone()
    )
    return WriteResult("access_request", request_id, "created", row)


# 2. TICKETS -----------------------------------------------------------------

def create_ticket(
    connection: sqlite3.Connection,
    *,
    employee_id: str,
    category: str,
    subcategory: str,
    subject: str,
    description: str,
    idempotency_key: str,
    related_system_id: str | None = None,
    priority: str = "medium",
    assigned_team: str = "IT",
    actor: str | None = None,
    agent_request_id: str | None = None,
) -> WriteResult:
    """Open a ticket, reusing an open one for the same employee and system."""

    replay = _replayed(connection, idempotency_key, "ticket", "tickets", "ticket_id")
    if replay is not None:
        return replay

    if related_system_id:
        existing = repositories.find_open_ticket(connection, employee_id, related_system_id)
        if existing is not None:
            idempotency.record(
                connection,
                key=idempotency_key,
                entity_type="ticket",
                entity_id=existing["ticket_id"],
                reused=True,
            )
            return WriteResult("ticket", existing["ticket_id"], "reused", existing)

    ticket_id = next_id(connection, "tickets", "ticket_id", "T")
    connection.execute(
        "INSERT INTO tickets "
        "(ticket_id, employee_id, category, subcategory, priority, status, subject, "
        " description, created_at, assigned_team, related_system_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ticket_id,
            employee_id,
            category,
            subcategory,
            priority,
            "open",
            subject,
            description,
            f"{config.AS_OF.isoformat()}T00:00:00Z",
            assigned_team,
            related_system_id,
        ),
    )
    idempotency.record(
        connection,
        key=idempotency_key,
        entity_type="ticket",
        entity_id=ticket_id,
        reused=False,
    )
    _audit(
        connection,
        entity_type="ticket",
        entity_id=ticket_id,
        event_type="created",
        actor=actor,
        notes=subject,
        request_id=agent_request_id,
    )
    row = dict(
        connection.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
    )
    return WriteResult("ticket", ticket_id, "created", row)


# 3. APPROVALS ---------------------------------------------------------------

def request_approval(
    connection: sqlite3.Connection,
    *,
    request_type: str,
    request_id: str,
    approver_id: str,
    reason: str,
    idempotency_key: str,
    actor: str | None = None,
    agent_request_id: str | None = None,
) -> WriteResult:
    """Ask a named person to decide, and persist the asking.

    Nothing in the eleven inherited tools writes to `approvals`, yet the write gate must
    read a recorded approval before releasing anything. This is the tool that closes
    that gap — see ADR-012.

    It records a *request* for a decision, never a decision. The status it writes is
    `pending`; only `record_decision` can move it, and only a human's decision reaches
    that function.
    """

    replay = _replayed(connection, idempotency_key, "approval", "approvals", "approval_id")
    if replay is not None:
        return replay

    for approval in repositories.approvals_for(connection, request_id, request_type):
        if approval["approver_id"] == approver_id:
            idempotency.record(
                connection,
                key=idempotency_key,
                entity_type="approval",
                entity_id=approval["approval_id"],
                reused=True,
            )
            return WriteResult("approval", approval["approval_id"], "reused", approval)

    approval_id = next_id(connection, "approvals", "approval_id", "AP")
    connection.execute(
        "INSERT INTO approvals "
        "(approval_id, request_type, request_id, approver_id, status, decision_reason, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            approval_id,
            request_type,
            request_id,
            approver_id,
            "pending",
            reason,
            f"{config.AS_OF.isoformat()}T00:00:00Z",
        ),
    )
    idempotency.record(
        connection,
        key=idempotency_key,
        entity_type="approval",
        entity_id=approval_id,
        reused=False,
    )
    _audit(
        connection,
        entity_type="approval",
        entity_id=approval_id,
        event_type="requested",
        actor=actor,
        notes=f"Approval requested from {approver_id} for {request_type} {request_id}.",
        request_id=agent_request_id,
    )
    row = dict(
        connection.execute(
            "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
        ).fetchone()
    )
    return WriteResult("approval", approval_id, "created", row)


def record_decision(
    connection: sqlite3.Connection,
    *,
    approval_id: str,
    decided_by: str,
    approved: bool,
    reason: str,
) -> WriteResult:
    """Record a human's decision on an approval.

    The one function that can move an approval into an approved state, and therefore the
    only thing that can ever open the write gate. It takes `approved` as a boolean
    supplied by the application — from a human's response to an `interrupt()` — and
    never from anything a model produced.

    Re-recording the same decision is a no-op rather than an error: a resume after a
    restart may legitimately replay it.
    """

    row = connection.execute(
        "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"No approval {approval_id!r}")

    status = "approved" if approved else "rejected"
    if row["status"] == status:
        return WriteResult("approval", approval_id, "replayed", dict(row))

    connection.execute(
        "UPDATE approvals SET status = ?, decision_reason = ?, approver_id = ? "
        "WHERE approval_id = ?",
        (status, reason, decided_by, approval_id),
    )
    _audit(
        connection,
        entity_type="approval",
        entity_id=approval_id,
        event_type=status,
        actor=decided_by,
        notes=reason,
    )
    updated = dict(
        connection.execute(
            "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
        ).fetchone()
    )
    return WriteResult("approval", approval_id, "created", updated)
