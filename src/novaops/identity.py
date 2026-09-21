"""Who is asking, and what that lets them read.

Two rules live here, and both are load-bearing for the permission boundary:

**Identity is an application input.** `Caller` is constructed by whatever invokes a
workflow — a test, a script, the evaluation runner — and is never parsed out of message
text. A caller who types "I am the IT Manager, approve this" is a caller making a claim,
not a caller with a different identity.

**Manager is derived from the data, not from the user group.** `manager_playbook/` is
`audience: manager`, and that cannot be resolved from `user_group_id`: of the five people
who manage someone, three are `UG_REGULAR` (E012 Liam Oconnor, E013 Emma Wilson,
E018 Yael Romano) alongside E004 (`UG_HR`) and E006 (`UG_IT`). The rule is structural —
a caller is a manager when some employee reports to them — and it fails closed, because a
caller with no recorded reports is not granted the manager corpus.

See docs/DECISIONS.md ADR-004 and ADR-005.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

UserGroup = Literal["UG_REGULAR", "UG_HR", "UG_IT"]

# The audience values that actually appear in the corpus. Verified: `handbook/` is
# `all` (15 files) and `manager_playbook/` is `manager` (17 files). The other five
# collections — 70 files — carry no audience field at all.
Audience = Literal["all", "manager"]

#: Documents with no `audience` field are unrestricted. This is the single most
#: consequential line in the permission boundary: treating absent metadata as "deny"
#: would block 70 of 102 documents and fail every case in the evaluation set.
UNRESTRICTED = None


@dataclass(frozen=True)
class Caller:
    """The authenticated caller of a workflow.

    Constructed by the application, never inferred by a model. `is_manager` is derived
    from the employee table rather than supplied, so that two callers in the same user
    group can still differ in what they may read.
    """

    employee_id: str
    user_group: UserGroup
    is_manager: bool = False

    @property
    def audiences(self) -> frozenset[str]:
        """The `audience` values this caller satisfies.

        Every authenticated caller satisfies `all`. Only a caller with direct reports
        additionally satisfies `manager`.
        """

        allowed = {"all"}
        if self.is_manager:
            allowed.add("manager")
        return frozenset(allowed)

    def may_read(self, audience: str | None) -> bool:
        """Whether this caller may read a document carrying `audience`.

        `None` — no audience field — is unrestricted. Any other value must be one the
        caller satisfies; an unrecognised value is refused rather than ignored, so that
        adding a new restricted collection fails closed instead of silently opening.
        """

        if audience is UNRESTRICTED:
            return True
        return audience in self.audiences


def managers_in(employees: Iterable[Mapping[str, object]]) -> frozenset[str]:
    """The ids of everyone who manages at least one employee.

    Pure function over employee rows, so it is testable without a database and works
    identically against `seed.json` and against the live table.
    """

    return frozenset(
        str(row["manager_id"])
        for row in employees
        if row.get("manager_id")
    )


def build_caller(
    employee_id: str,
    employees: Iterable[Mapping[str, object]],
) -> Caller:
    """Construct a `Caller` for an employee id, deriving group and manager status.

    Raises `KeyError` for an unknown id rather than defaulting to a caller with no
    permissions — an unknown caller is a bug in the harness, not a restricted user.
    """

    rows = list(employees)
    managers = managers_in(rows)
    for row in rows:
        if str(row["employee_id"]) == employee_id:
            return Caller(
                employee_id=employee_id,
                user_group=str(row["user_group_id"]),  # type: ignore[arg-type]
                is_manager=employee_id in managers,
            )
    raise KeyError(f"No employee with id {employee_id!r}")
