"""Shared pieces of every return contract: the terminal status, and a citation.

`Status` is deliberately narrow. The specification requires that every run ends in
exactly one of these five and that the answer text agrees with it — and it draws a line
that is easy to blur: **`status` is where the workflow got to, not what happened to the
business request.** A run that correctly determined access must be refused is
`completed`; the refusal itself belongs in the typed object's own fields. Encoding
"refused" as a status would make the trace unreadable, because a reader could no longer
tell a refusal from a crash.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Status(StrEnum):
    """Where a run got to. Exactly one per run, and the prose must agree with it."""

    COMPLETED = "completed"
    """The workflow ran to the end and produced its answer."""

    BLOCKED = "blocked"
    """The workflow stopped against a condition it cannot resolve — a seat limit, a
    permission boundary, a missing prerequisite. A correct, useful terminal state."""

    PENDING = "pending"
    """Paused awaiting something external: an approval decision, an inbound event."""

    NEEDS_HUMAN = "needs_human"
    """The workflow requires a person to decide before it can continue."""

    FAILED = "failed"
    """The workflow could not run. A defect, not a business outcome."""


class Citation(BaseModel):
    """Where a claim came from.

    Every factual claim carries at least one. An uncited correct answer is a failure in
    this project, so citations are part of the type rather than a formatting convention
    applied to prose.

    `source` is the document path relative to the dataset's `documents/` directory — the
    same form the golden dataset's `expected_sources` and `forbidden_sources` use, so a
    scorer can compare them without normalising first.
    """

    source: str = Field(
        description="Document path relative to documents/, e.g. 'policies/equipment_policy.md'."
    )
    quote: str | None = Field(
        default=None,
        description="The supporting span from the source, when a specific line carries the claim.",
    )
    record: str | None = Field(
        default=None,
        description=(
            "A database record id when the claim rests on a row rather than a "
            "document, e.g. 'SUB001'."
        ),
    )


class Evidence(BaseModel):
    """One observed fact and the citation that supports it.

    Used where a contract must separate what was *observed* from what was *done* or
    *recommended* — a distinction `AccessDecision` is required to make explicit.
    """

    statement: str = Field(description="The fact, stated plainly.")
    citations: list[Citation] = Field(
        min_length=1,
        description=(
            "At least one source. A fact with no source does not belong in a return object."
        ),
    )
