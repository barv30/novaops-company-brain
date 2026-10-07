"""The Maya → Webex handoff: a typed intent, built by application code.

§6.5 requires it — "the subject employee, the system requested, a justification, and the
id of the conversation it came from, so the write can be traced back and replayed without
duplicating" — and Milestone 3 says when: "Emit a typed handoff when the caller explicitly
asks to act — Maya proposes, she never files."

**Why it is not a tool.** `S2` turn 10 ("Go ahead and file the Webex access request for
her now") is annotated `should_use_tools: False`. The handoff is not a lookup; it is the
application recording what the caller asked for. So the model's job stops at the plan —
it may say which employee and which system the request is about, which is proposing, and
is allowed — and this module builds the typed object from that plus what only the
application knows: the conversation id and the caller.

**Why it consults Webex.** The spec describes the handoff as crossing "once into the
separate Webex workflow and return[ing] its pending result". The intent is resolved
against the same diagnosis Webex uses, so Maya can say truthfully what would happen —
blocked at 42 of 40 seats, an existing request reused — without either scope writing a
row. Filing remains the Webex scope's business, behind the write gate.
"""

from __future__ import annotations

import sqlite3

from pydantic import BaseModel, Field

from novaops.contracts.access import AccessIntent, Outcome
from novaops.db import repositories
from novaops.workflows.access import diagnose_access


class Handoff(BaseModel):
    """The intent, and what Webex would make of it right now."""

    intent: AccessIntent
    webex_outcome: Outcome
    webex_status: str
    blocking_reason: str | None = None
    existing_request_id: str | None = Field(
        default=None,
        description="An open request the intent would resolve to, rather than a new one.",
    )

    def summary(self) -> str:
        """One line for the answering model and the trace."""

        reuse = (
            f" It resolves to existing request {self.existing_request_id}; nothing new "
            f"would be created." if self.existing_request_id else ""
        )
        reason = f" {self.blocking_reason}" if self.blocking_reason else ""
        return (
            f"Prepared, not filed: {self.intent.system} access for "
            f"{self.intent.subject_employee_id}, requested by {self.intent.requested_by} "
            f"in conversation {self.intent.conversation_id}. Webex would report it as "
            f"{self.webex_outcome.value}.{reason}{reuse}"
        )


def _resolve(connection: sqlite3.Connection, who: str | None) -> str | None:
    """An employee id from an id or a unique name. Ambiguity resolves to nothing."""

    if not who:
        return None
    who = who.strip()
    if repositories.get_employee(connection, who.upper()):
        return who.upper()
    matches = repositories.find_employees(connection, who)
    return matches[0]["employee_id"] if len(matches) == 1 else None


def prepare_handoff(
    connection: sqlite3.Connection,
    *,
    subject: str | None,
    system: str | None,
    justification: str | None,
    conversation_id: str,
    requested_by: str,
) -> Handoff | None:
    """Build the typed handoff, or None when there is not enough to build one honestly.

    `subject`, `system` and `justification` come from the plan — the model proposing.
    `conversation_id` and `requested_by` come from the application. An unresolvable
    subject (or two people called Maya) yields None rather than a guess: a handoff about
    the wrong person is worse than none.
    """

    employee_id = _resolve(connection, subject)
    if not employee_id or not system:
        return None

    intent = AccessIntent(
        subject_employee_id=employee_id,
        system=system,
        justification=(justification or f"{system} access requested for {employee_id}"),
        conversation_id=conversation_id,
        requested_by=requested_by,
    )

    decision = diagnose_access(connection, employee_id, system)
    existing = next(
        (a.record_id for a in decision.actions_taken if a.record_type == "access_request"),
        None,
    )
    return Handoff(
        intent=intent,
        webex_outcome=decision.outcome,
        webex_status=decision.status.value,
        blocking_reason=decision.blocking_reason,
        existing_request_id=existing,
    )
