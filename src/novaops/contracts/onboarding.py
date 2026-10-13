"""Maya's return contract: an evidence-backed onboarding checklist.

The specification's minimum (§6.5): the items, each with its status and **at least one
citation**; anything blocked carries the reason; and the overall `status`.

Two properties are enforced by the types rather than left to prose. A checklist item
cannot be constructed without a citation, and a blocked item cannot be constructed
without a reason — so "Webex, blocked" with no explanation is a validation error here
rather than a weak answer discovered while marking.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from novaops.contracts.common import Citation, Status


class ItemStatus(StrEnum):
    """Where one checklist item stands."""

    DONE = "done"
    """Already provisioned or completed, per the records."""

    PENDING = "pending"
    """Not done yet, and nothing is preventing it."""

    BLOCKED = "blocked"
    """Cannot proceed. Requires a reason."""

    NOT_APPLICABLE = "not_applicable"
    """Does not apply to this joiner — a contractor addendum, a role-specific system."""


class ChecklistItem(BaseModel):
    """One required system, piece of equipment, or policy acknowledgement."""

    name: str = Field(description="What the item is, e.g. 'Webex license' or 'MacBook Pro 14'.")
    category: str = Field(description="'system', 'equipment', or 'policy_acknowledgement'.")
    status: ItemStatus
    blocked_reason: str | None = Field(
        default=None,
        description=(
            "Why this cannot proceed. Required when status is blocked. States the real "
            "cause — 'subscription at 42 active seats against a 40-seat contract limit', "
            "not 'unavailable'."
        ),
    )
    owner_group: str | None = Field(
        default=None, description="Which group owns the action: UG_HR, UG_IT."
    )
    due_date: str | None = Field(default=None, description="ISO date, when the records carry one.")
    record_status: str | None = Field(
        default=None,
        description=(
            "The status exactly as the source record states it — 'planned', 'pending', "
            "'completed'. `status` groups these for logic; this keeps the record's own "
            "word, which is what an answer must quote."
        ),
    )
    citations: list[Citation] = Field(
        min_length=1,
        description="At least one source per item. An uncited item is not evidence-backed.",
    )

    @model_validator(mode="after")
    def _blocked_items_explain_themselves(self) -> ChecklistItem:
        if self.status is ItemStatus.BLOCKED and not self.blocked_reason:
            raise ValueError(
                f"Checklist item {self.name!r} is blocked but carries no reason. "
                "Naming the blocker is the point of reporting one."
            )
        if self.status is not ItemStatus.BLOCKED and self.blocked_reason:
            raise ValueError(
                f"Checklist item {self.name!r} carries a blocked_reason but is not blocked."
            )
        return self


class OnboardingChecklist(BaseModel):
    """Maya's typed answer. Proposed, never filed — Maya writes nothing.

    `status` is the run's terminal state. A checklist that was assembled successfully is
    `completed` even when items inside it are blocked; the blocked items are the answer,
    not a failure to produce one. `blocked` at this level means the checklist itself
    could not be assembled.
    """

    employee_id: str = Field(description="The joiner this checklist is for. Ids, not names — "
                                         "E001 Maya Cohen and E020 Maya Rosen share a first name.")
    employee_name: str
    start_date: str | None = Field(default=None, description="ISO date.")
    items: list[ChecklistItem] = Field(min_length=1)
    constraints_applied: list[str] = Field(
        default_factory=list,
        description=(
            "Constraints the caller stated that shaped this checklist and are still in "
            "force — a spend freeze, a location, an approval requirement. Recorded so a "
            "reader can see that a constraint stated early was still honoured late."
        ),
    )
    unresolved: list[str] = Field(
        default_factory=list,
        description="What could not be determined, and why. Stated rather than guessed at.",
    )
    status: Status

    @property
    def blocked_items(self) -> list[ChecklistItem]:
        """The items that cannot proceed. What a reader looks for first."""

        return [item for item in self.items if item.status is ItemStatus.BLOCKED]
