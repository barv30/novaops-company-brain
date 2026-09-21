"""Reading `GOLDEN-DATASETS.json`, and knowing which of its fields bind.

The dataset's own `_readme` draws a line this module makes structural, because getting
it wrong wastes days:

**Binding** — a mismatch is a real defect in our system:
`required_facts`, `forbidden_facts`, `forbidden_tools`, `forbidden_sources`,
`expected_sources`, `should_use_tools`.

**Illustrative** — these describe the course's reference implementation, whose tools are
the ten on the Lesson 10 server and whose retrieval is a local FAISS index over mock
policy files. Our tool decomposition is different on purpose, so these *will* drift from
a system that is behaving perfectly: `expected_tools`, `optional_tools`, `sequence`,
`arg_checks`, `expected_intent`, `expected_route`.

`Turn.binding_only()` is what the scorer uses. The illustrative fields are still loaded
and still reported, because "we used `search_policies` where the reference used
`get_policy`" is worth seeing — it is just never a failure.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from novaops import config

#: Sessions that do not name a caller. The persona line names the person, so the id is
#: read from there; these are the fallbacks where even that is absent.
DEFAULT_CALLER = "E010"  # Rachel Stein, UG_REGULAR — the least-privileged sensible default

_PERSONA_ID = re.compile(r"\((E\d{3})\b")


@dataclass(frozen=True)
class Turn:
    """One turn of a golden session, with its expectations."""

    n: int
    user: str
    kind: str | None = None
    note: str | None = None

    # binding
    should_use_tools: bool | None = None
    required_facts: list[str] = field(default_factory=list)
    forbidden_facts: list[str] = field(default_factory=list)
    forbidden_tools: list[str] = field(default_factory=list)
    expected_sources: list[str] = field(default_factory=list)
    forbidden_sources: list[str] = field(default_factory=list)

    # illustrative — reported, never scored
    expected_intent: str | None = None
    expected_tools: list[str] = field(default_factory=list)
    optional_tools: list[str] = field(default_factory=list)

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Turn:
        return cls(
            n=int(raw["n"]),
            user=str(raw.get("user", "")),
            kind=raw.get("kind"),
            note=raw.get("note"),
            should_use_tools=raw.get("should_use_tools"),
            required_facts=list(raw.get("required_facts") or []),
            forbidden_facts=list(raw.get("forbidden_facts") or []),
            forbidden_tools=list(raw.get("forbidden_tools") or []),
            expected_sources=list(raw.get("expected_sources") or []),
            forbidden_sources=list(raw.get("forbidden_sources") or []),
            expected_intent=raw.get("expected_intent"),
            expected_tools=list(raw.get("expected_tools") or []),
            optional_tools=list(raw.get("optional_tools") or []),
        )

    @property
    def binds_anything(self) -> bool:
        """Whether this turn carries any checkable expectation at all."""

        return bool(
            self.required_facts
            or self.forbidden_facts
            or self.forbidden_tools
            or self.expected_sources
            or self.forbidden_sources
            or self.should_use_tools is not None
        )


@dataclass(frozen=True)
class Session:
    """A golden session: one thread, replayed turn by turn in order."""

    id: str
    turns: list[Turn]
    caller_id: str
    caller_group: str
    persona: str = ""
    focus: str = ""

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Session:
        persona = str(raw.get("persona", ""))
        caller = raw.get("caller_id")
        if not caller:
            # Several sessions name the caller only in the persona line, e.g.
            # "Rachel Stein (E010, Customer Success) — a short policy question."
            match = _PERSONA_ID.search(persona)
            caller = match.group(1) if match else DEFAULT_CALLER
        return cls(
            id=str(raw["id"]),
            turns=[Turn.from_json(t) for t in raw["turns"]],
            caller_id=str(caller),
            caller_group=str(raw.get("caller_group", "")),
            persona=persona,
            focus=str(raw.get("focus", "")),
        )


@lru_cache(maxsize=1)
def _raw() -> dict[str, Any]:
    return json.loads(config.GOLDEN_DATASETS.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def sessions() -> dict[str, Session]:
    """Every golden session, keyed by id."""

    return {s["id"]: Session.from_json(s) for s in _raw()["sessions"]}


def session(prefix: str) -> Session:
    """One session by id or by prefix — `session("S2")` finds `S2-onboarding-maya`."""

    found = [s for key, s in sessions().items() if key == prefix or key.startswith(f"{prefix}-")]
    if not found:
        raise KeyError(f"No golden session matching {prefix!r}. Have: {sorted(sessions())}")
    return found[0]


@lru_cache(maxsize=1)
def single_turn_cases() -> list[dict[str, Any]]:
    return list(_raw()["single_turn_cases"])


@lru_cache(maxsize=1)
def retrieval_expectations() -> list[dict[str, Any]]:
    return list(_raw()["retrieval_expectations"])


@lru_cache(maxsize=1)
def rubrics() -> list[dict[str, Any]]:
    return list(_raw()["golden_rubrics"])
