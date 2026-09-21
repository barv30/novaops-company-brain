"""Typed return contracts.

Every workflow returns one of these, validated before it is returned. The types carry
the rules that would otherwise live in prose: a checklist item cannot exist without a
citation, a blocked item cannot exist without a reason, and access cannot be reported as
granted without the record that proves it.
"""

from novaops.contracts.access import (
    AccessDecision,
    AccessIntent,
    ActionTaken,
    NextStep,
    Outcome,
)
from novaops.contracts.common import Citation, Evidence, Status
from novaops.contracts.onboarding import ChecklistItem, ItemStatus, OnboardingChecklist

__all__ = [
    "AccessDecision",
    "AccessIntent",
    "ActionTaken",
    "ChecklistItem",
    "Citation",
    "Evidence",
    "ItemStatus",
    "NextStep",
    "OnboardingChecklist",
    "Outcome",
    "Status",
]
