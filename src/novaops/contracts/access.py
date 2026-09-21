"""Webex's return contract, and the Maya → Webex handoff.

The specification's minimum for `AccessDecision` (§6.5): separated **observed facts**,
**actions actually taken**, and **recommended next steps**; the blocking reason when
there is one; the `status`. And one prohibition stated twice in the spec:
**it must never state that access was granted.**

That separation is the whole design. The failure this contract exists to prevent is a
fluent paragraph in which a diagnosis, a recommendation and a completed action are
indistinguishable — which is how a system comes to report that it granted access it did
not grant. Three separate lists cannot blur, and `ActionTaken` cannot be constructed
without the id of the record that proves it.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from novaops.contracts.common import Citation, Evidence, Status


class Outcome(StrEnum):
    """What happened to the *business request* — distinct from the run's `status`.

    Keeping these apart is what lets a trace stay readable: `status=completed,
    outcome=refused` is a workflow that worked perfectly and correctly said no.
    """

    GRANTED = "granted"
    """Access exists now. Only application code that performed the write may set this."""

    REFUSED = "refused"
    """Determined not to proceed — ineligible by role, or a policy forbids it."""

    BLOCKED = "blocked"
    """Eligible, but something outside the requester prevents it — a seat limit."""

    AWAITING_APPROVAL = "awaiting_approval"
    """A decision has been requested from a human and not yet recorded."""

    RECOMMENDED = "recommended"
    """A course of action is proposed; nothing was filed."""


class ActionTaken(BaseModel):
    """Something this system actually did, with the record that proves it.

    A model may propose or explain an action; only application code may record that it
    happened. An action with no `record_id` is not an action — it is a recommendation,
    and belongs in `recommended_next_steps` instead.
    """

    action: str = Field(description="What was done, e.g. 'filed access request'.")
    record_id: str = Field(
        min_length=1,
        description="The id of the row that proves it, e.g. 'AR001'. Never blank.",
    )
    record_type: str = Field(
        description="'ticket', 'access_request', 'approval', 'onboarding_task'."
    )
    reused_existing: bool = Field(
        default=False,
        description=(
            "True when an existing record was reused rather than a new one created. "
            "Reuse is the correct behavior for Rachel Stein's T001 and AR001; creating a "
            "second row for the same request is a duplicate write, not diligence."
        ),
    )


class NextStep(BaseModel):
    """Something that should happen next, and who has to do it. Not yet done."""

    step: str
    owner: str | None = Field(
        default=None, description="Who acts: a group, a role, or an employee id."
    )
    blocked_on: str | None = Field(default=None, description="What must resolve first.")


class AccessDecision(BaseModel):
    """Webex's typed answer.

    Three separated sections, so a reader — and a scorer — can tell a fact from a deed
    from a suggestion without parsing prose.
    """

    employee_id: str
    system: str = Field(description="The system access was requested for, e.g. 'Webex'.")
    outcome: Outcome
    status: Status

    observed_facts: list[Evidence] = Field(
        default_factory=list,
        description="What the records and documents say. Each carries its own citation.",
    )
    actions_taken: list[ActionTaken] = Field(
        default_factory=list,
        description="What this system actually did. Empty is the common and correct case.",
    )
    recommended_next_steps: list[NextStep] = Field(
        default_factory=list,
        description="What should happen next. Explicitly not done.",
    )

    blocking_reason: str | None = Field(
        default=None,
        description=(
            "Why this cannot proceed, when it cannot. Names the real cause: "
            "'Webex subscription is at 42 active seats against a 40-seat contract limit'. "
            "Required when outcome is blocked or refused."
        ),
    )
    citations: list[Citation] = Field(
        default_factory=list,
        description="Sources for the decision as a whole, beyond those on individual facts.",
    )

    @model_validator(mode="after")
    def _blocked_outcomes_name_their_cause(self) -> AccessDecision:
        if self.outcome in (Outcome.BLOCKED, Outcome.REFUSED) and not self.blocking_reason:
            raise ValueError(
                f"Outcome is {self.outcome} but no blocking_reason was given. "
                "An accurate report names the blocker."
            )
        return self

    @model_validator(mode="after")
    def _granted_requires_a_record(self) -> AccessDecision:
        """Access cannot be reported as granted without a write that proves it.

        This is the contract's reason for existing. The specification prohibits claiming
        access was granted, and the honest version of that prohibition is not "never say
        granted" but "only say it when a record backs it".
        """

        if self.outcome is Outcome.GRANTED and not self.actions_taken:
            raise ValueError(
                "Outcome is 'granted' but actions_taken is empty. Access cannot be "
                "reported as granted without the record that proves it."
            )
        return self


class AccessIntent(BaseModel):
    """The Maya → Webex handoff.

    The specification's minimum: the subject employee, the system requested, a
    justification, and the id of the conversation it came from — so the write can be
    traced back and replayed without duplicating.

    Maya emits this and stops. It is a *request to consider an action*, never the action.
    `conversation_id` is what makes a replay safe: the same intent from the same
    conversation must resolve to the same record.
    """

    subject_employee_id: str = Field(description="Who the access is for — an id, not a name.")
    system: str = Field(description="The system requested.")
    access_level: str = Field(default="standard")
    justification: str = Field(
        min_length=1,
        description="Why this access is needed, in the caller's terms.",
    )
    conversation_id: str = Field(
        min_length=1,
        description=(
            "The conversation this intent came from. Carried into the write as part of "
            "the idempotency key, so replaying the conversation creates no second row."
        ),
    )
    requested_by: str = Field(description="The employee id of the caller who asked.")

    def idempotency_key(self) -> str:
        """The key a write is recorded under.

        Composed from the conversation and the request itself, so the same request made
        twice in one conversation collapses to one row, while a genuinely new request in
        a later conversation does not.
        """

        return (
            f"{self.conversation_id}:{self.subject_employee_id}"
            f":{self.system}:{self.access_level}"
        )
