"""Maya and Webex: two scopes on one graph.

They differ by **authority and evidence standard**, not by topic:

| Scope | Reads               | Writes                      | Judged on                    |
| ----- | ------------------- | --------------------------- | ---------------------------- |
| Maya  | documents + records | **nothing**                 | citations, checklist cover   |
| Webex | documents + records | tickets, requests, approvals| persisted rows, exactly once |

Maya's read-only property is enforced here as a *checked condition* — `writes_allowed`
is False, so the write gate can never release anything on this scope — rather than by
keeping her code in a different folder. A folder boundary is not a boundary; a reviewer
cannot see it in a trace, and nothing fails if someone imports across it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from novaops.agent.policy import LOADOUTS, WRITE_TOOLS

MAYA = "maya"
WEBEX = "webex"


@dataclass(frozen=True)
class Scope:
    """One authority level the agent can run a turn under."""

    name: str
    description: str
    writes_allowed: bool
    #: Intents this scope handles. An intent outside it still answers — the scope
    #: governs authority, not subject matter — but it is recorded in the trace, so a
    #: Maya session repeatedly classifying `access_request` is visible rather than
    #: silent.
    intents: frozenset[str] = field(default_factory=frozenset)

    def visible_reads(self, intent: str, catalogue: list[str]) -> list[str]:
        """Which read tools enter this turn's context.

        `None` in the loadout table means "the whole read catalogue" — failing open for
        reads is safe and recovers from a near-miss classification. Write tools are
        filtered out here unconditionally: even a scope that allows writes gets them
        only from the write gate, never from the loadout.
        """

        selected = LOADOUTS.get(intent)
        names = sorted(catalogue) if selected is None else list(selected)
        return [name for name in names if name in catalogue and name not in WRITE_TOOLS]


#: HR and onboarding. Answers from evidence, cites everything, **writes nothing**.
#: Maya proposes an action as a typed `AccessIntent`; she never files it.
MAYA_SCOPE = Scope(
    name=MAYA,
    description=(
        "HR and onboarding. Reads documents and records, cites everything, writes nothing."
    ),
    writes_allowed=False,
    intents=frozenset({
        "policy_question", "benefits_question", "career_question", "employee_lookup",
        "onboarding_status", "equipment_request", "other",
    }),
)

#: IT operations. May write — but only through the write gate, and only against a
#: recorded approval.
WEBEX_SCOPE = Scope(
    name=WEBEX,
    description=(
        "IT operations. Diagnoses, reuses existing records, writes only after a human "
        "approves."
    ),
    writes_allowed=True,
    intents=frozenset({
        "access_request", "ticket_status", "subscription_review", "policy_question",
        "employee_lookup", "other",
    }),
)

SCOPES: dict[str, Scope] = {MAYA: MAYA_SCOPE, WEBEX: WEBEX_SCOPE}


def get_scope(name: str) -> Scope:
    """Look a scope up by name, refusing an unknown one rather than defaulting.

    Defaulting an unrecognised scope to Maya would be safe by accident; defaulting it to
    Webex would be unsafe by accident. Refusing is neither.
    """

    try:
        return SCOPES[name]
    except KeyError:
        raise ValueError(
            f"Unknown scope {name!r}. Known scopes: {sorted(SCOPES)}."
        ) from None
