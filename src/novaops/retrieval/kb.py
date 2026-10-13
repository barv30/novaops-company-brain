"""The steps inside an IT knowledge-base article, as a list rather than a sentence.

Every `it_kb/` article has a `## First-line Troubleshooting` section, written as one
sentence of comma-separated actions: "Confirm target actually requires VPN, verify MFA
and device trust, check VPN group. Outdated guidance about shared VPN passwords is
invalid." That sentence IS the procedure.

Asked for "the vpn steps" (S8 turn 1), Nova read the article and answered that it "does
not include the actual step-by-step instructions" — two runs in three, after an answer
rule saying in as many words that this section is the steps. So the steps are now handed
to the model as data: `search_knowledge_base` adds a `steps` list to every KB result
whose text carries the section. Deterministic, and testable without a model (ADR-027).
"""

from __future__ import annotations

import re

_SECTION = re.compile(
    r"^##\s*First-line Troubleshooting\s*\n+(?P<body>.+?)(?=\n\s*##|\Z)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)


def troubleshooting_steps(text: str) -> list[str]:
    """The article's First-line Troubleshooting section, one sentence per step, in order.

    Split on sentences only — never inside one. A first version split on commas, which
    reads well for VPN and mangles `mfa_reset.md`: "verify identity by manager, HR, or IT
    video check" became three steps, one of them "Or IT video check". The articles do not
    share a format, so a split finer than the sentence is a guess about each one's
    grammar. Returns [] when the section is absent, e.g. a chunk that starts after it.
    """

    match = _SECTION.search(text)
    if not match:
        return []
    body = " ".join(match.group("body").split())
    return [s.strip() for s in re.split(r"(?<=\.)\s+", body) if s.strip()]
