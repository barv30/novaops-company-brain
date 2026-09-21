"""The return contracts enforce their rules, not just describe them.

Each test here corresponds to a sentence in the specification that would otherwise be a
convention someone has to remember.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from novaops.contracts import (
    AccessDecision,
    AccessIntent,
    ActionTaken,
    ChecklistItem,
    Citation,
    ItemStatus,
    OnboardingChecklist,
    Outcome,
    Status,
)

WEBEX_SEAT_LIMIT = "Webex subscription is at 42 active seats against a 40-seat contract limit"
CONTRACT_CITE = Citation(source="contracts/webex_vendor_agreement.md", record="SUB001")


# 1. CHECKLIST ITEMS --------------------------------------------------------

def test_checklist_item_requires_a_citation():
    """An uncited correct answer is a failure in this project."""

    with pytest.raises(ValidationError):
        ChecklistItem(
            name="Webex license",
            category="system",
            status=ItemStatus.PENDING,
            citations=[],
        )


def test_blocked_item_must_name_its_blocker():
    with pytest.raises(ValidationError, match="carries no reason"):
        ChecklistItem(
            name="Webex license",
            category="system",
            status=ItemStatus.BLOCKED,
            citations=[CONTRACT_CITE],
        )


def test_blocked_reason_without_blocked_status_is_rejected():
    with pytest.raises(ValidationError, match="not blocked"):
        ChecklistItem(
            name="Webex license",
            category="system",
            status=ItemStatus.PENDING,
            blocked_reason=WEBEX_SEAT_LIMIT,
            citations=[CONTRACT_CITE],
        )


def test_a_valid_blocked_item_surfaces_through_blocked_items():
    """The shape Maya's answer must produce for Webex."""

    checklist = OnboardingChecklist(
        employee_id="E001",
        employee_name="Maya Cohen",
        start_date="2026-08-01",
        items=[
            ChecklistItem(
                name="Webex license",
                category="system",
                status=ItemStatus.BLOCKED,
                blocked_reason=WEBEX_SEAT_LIMIT,
                citations=[CONTRACT_CITE],
            ),
        ],
        constraints_applied=["Q3 SaaS renewal freeze applies to anything this costs"],
        status=Status.COMPLETED,
    )
    assert [item.name for item in checklist.blocked_items] == ["Webex license"]
    # A checklist assembled successfully is `completed` even when items inside it are
    # blocked. The blocked items are the answer, not a failure to produce one.
    assert checklist.status is Status.COMPLETED


# 2. ACCESS DECISION --------------------------------------------------------

def test_access_cannot_be_reported_as_granted_without_a_record():
    """The prohibition the spec states twice, enforced by the type."""

    with pytest.raises(ValidationError, match="without the record that proves it"):
        AccessDecision(
            employee_id="E010",
            system="Webex",
            outcome=Outcome.GRANTED,
            status=Status.COMPLETED,
        )


def test_blocked_outcome_must_name_its_cause():
    with pytest.raises(ValidationError, match="names the blocker"):
        AccessDecision(
            employee_id="E010",
            system="Webex",
            outcome=Outcome.BLOCKED,
            status=Status.BLOCKED,
        )


def test_the_rachel_stein_shape():
    """Eligible, blocked by the seat limit, existing records reused, nothing granted."""

    decision = AccessDecision(
        employee_id="E010",
        system="Webex",
        outcome=Outcome.BLOCKED,
        status=Status.BLOCKED,
        blocking_reason=WEBEX_SEAT_LIMIT,
        actions_taken=[
            ActionTaken(
                action="reused existing ticket",
                record_id="T001",
                record_type="ticket",
                reused_existing=True,
            ),
            ActionTaken(
                action="reused existing access request",
                record_id="AR001",
                record_type="access_request",
                reused_existing=True,
            ),
        ],
    )
    assert decision.outcome is not Outcome.GRANTED
    assert all(action.reused_existing for action in decision.actions_taken)


def test_an_action_cannot_have_a_blank_record_id():
    """An action with no record is a recommendation, and belongs elsewhere."""

    with pytest.raises(ValidationError):
        ActionTaken(action="filed access request", record_id="", record_type="access_request")


# 3. THE HANDOFF ------------------------------------------------------------

def test_intent_idempotency_key_is_stable_within_a_conversation():
    """The same request twice in one conversation must collapse to one row."""

    def intent() -> AccessIntent:
        return AccessIntent(
            subject_employee_id="E001",
            system="Webex",
            justification="Required for Customer Success onboarding",
            conversation_id="S2-onboarding-maya",
            requested_by="E004",
        )

    assert intent().idempotency_key() == intent().idempotency_key()


def test_intent_key_differs_across_conversations():
    """A genuinely new request later must not be suppressed as a duplicate."""

    base = {
        "subject_employee_id": "E001",
        "system": "Webex",
        "justification": "Required for Customer Success onboarding",
        "requested_by": "E004",
    }
    first = AccessIntent(conversation_id="thread-1", **base)
    second = AccessIntent(conversation_id="thread-2", **base)
    assert first.idempotency_key() != second.idempotency_key()


def test_intent_requires_a_conversation_id():
    """Without it a write cannot be traced back or replayed safely."""

    with pytest.raises(ValidationError):
        AccessIntent(
            subject_employee_id="E001",
            system="Webex",
            justification="Required for onboarding",
            conversation_id="",
            requested_by="E004",
        )
