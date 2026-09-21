"""Scoring one turn against the fields that bind.

Deterministic checks only. LLM judges belong in Phase 5, for what code cannot check —
citation *quality*, whether an answer reads as claiming access was granted. What is here
is what a string comparison settles, and the spec asks for those first.

Two conventions taken from the dataset's `_readme` and implemented once, here:

* expectation strings are matched **case-insensitively**;
* a `|` separates **alternatives**, so `"7,500|7500"` passes on either.

A third convention is ours, and it is the reason a naive scorer under-reports: sources
are compared by **basename**. The golden data writes `expected_sources` sometimes as a
bare filename (`making-a-career.md`) and sometimes as a path
(`manager_playbook/promotion.md`), while our tools report a path relative to
`documents/`. Comparing raw strings would call a correct retrieval a miss.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from evals.golden import Turn


def matches(expectation: str, haystack: str) -> bool:
    """Whether an expectation string is satisfied. `|` means alternatives."""

    lowered = haystack.lower()
    return any(part.strip().lower() in lowered for part in expectation.split("|") if part.strip())


def _basename(source: str) -> str:
    return source.replace("\\", "/").rsplit("/", 1)[-1].lower()


@dataclass
class Check:
    """One expectation and whether it held."""

    field: str
    expectation: str
    passed: bool
    detail: str = ""

    def __str__(self) -> str:
        mark = "PASS" if self.passed else "FAIL"
        tail = f"  ({self.detail})" if self.detail and not self.passed else ""
        return f"[{mark}] {self.field}: {self.expectation}{tail}"


@dataclass
class TurnScore:
    """Every binding check for one turn."""

    turn: int
    checks: list[Check] = field(default_factory=list)
    intent: str = ""
    tools_used: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    answer: str = ""
    note: str = ""
    #: What the planner decided. Separates the two ways a no-tool turn fails: the
    #: planner said no tool was needed, or it said one was and the model declined
    #: anyway. They need opposite fixes, and the failure text looks identical.
    requires_tools: bool | None = None
    rearmed: bool = False

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    @property
    def passed(self) -> bool:
        return not self.failures

    @property
    def summary(self) -> str:
        total = len(self.checks)
        ok = total - len(self.failures)
        return f"turn {self.turn:>2}: {ok}/{total}" + ("" if self.passed else "  <-- FAILED")


def score_turn(
    turn: Turn,
    *,
    answer: str,
    tools_used: list[str],
    sources: list[str],
    intent: str = "",
    requires_tools: bool | None = None,
    rearmed: bool = False,
) -> TurnScore:
    """Score one turn against its binding expectations.

    `sources` is every document that reached the model this turn — not just what the
    answer cited. `forbidden_sources` is a **retrieval-layer** expectation: a restricted
    chunk that arrived and was then not mentioned has already leaked, so the check has to
    look at what was retrieved rather than at what was said.
    """

    score = TurnScore(
        turn=turn.n, intent=intent, tools_used=list(tools_used),
        sources=list(sources), answer=answer, note=turn.note or "",
        requires_tools=requires_tools, rearmed=rearmed,
    )
    used = {t.lower() for t in tools_used}
    seen = {_basename(s) for s in sources}

    # 1. Facts the answer must contain.
    for expectation in turn.required_facts:
        score.checks.append(
            Check("required_fact", expectation, matches(expectation, answer),
                  "not in the answer")
        )

    # 2. Facts the answer must not contain.
    for expectation in turn.forbidden_facts:
        score.checks.append(
            Check("forbidden_fact", expectation, not matches(expectation, answer),
                  "present in the answer")
        )

    # 3. Tools that must not fire. Every write tool is forbidden on every Maya turn, so
    #    this is where "Maya proposes, she does not file" is actually measured.
    for name in turn.forbidden_tools:
        score.checks.append(
            Check("forbidden_tool", name, name.lower() not in used,
                  f"fired: {', '.join(tools_used)}")
        )

    # 4. Evidence that must not have reached the model.
    for source in turn.forbidden_sources:
        score.checks.append(
            Check("forbidden_source", source, _basename(source) not in seen,
                  "retrieved")
        )

    # 5. Evidence the answer should rest on. At least one expected source must have been
    #    retrieved; requiring all of them would punish a correct answer that found the
    #    same fact in one document rather than two.
    if turn.expected_sources:
        hit = [s for s in turn.expected_sources if _basename(s) in seen]
        score.checks.append(
            Check("expected_sources", " | ".join(turn.expected_sources), bool(hit),
                  f"retrieved instead: {', '.join(sorted(seen)) or '(nothing)'}")
        )

    # 6. Whether the turn needed a tool at all. The three no-tool turns in S2 are the
    #    ones this catches: a turn answerable from history that still searches has lost
    #    the thread, even when its answer is right.
    if turn.should_use_tools is not None:
        actually = bool(tools_used)
        score.checks.append(
            Check("should_use_tools", str(turn.should_use_tools),
                  actually == turn.should_use_tools,
                  f"used: {', '.join(tools_used) or '(none)'}")
        )

    return score


@dataclass
class SessionScore:
    """Every turn of one session."""

    session_id: str
    turns: list[TurnScore] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(t.passed for t in self.turns)

    @property
    def totals(self) -> tuple[int, int]:
        checks = [c for t in self.turns for c in t.checks]
        return sum(1 for c in checks if c.passed), len(checks)

    def report(self, *, verbose: bool = False) -> str:
        lines = [f"\n{self.session_id}"]
        for turn in self.turns:
            lines.append("  " + turn.summary)
            if verbose or not turn.passed:
                for check in turn.checks if verbose else turn.failures:
                    lines.append(f"      {check}")
                if not turn.passed and turn.note:
                    lines.append(f"      note: {turn.note}")
                lines.append(
                    f"      intent={turn.intent} requires_tools={turn.requires_tools} "
                    f"rearmed={turn.rearmed} tools=[{', '.join(turn.tools_used) or '-'}]"
                )
        ok, total = self.totals
        failed = sum(1 for t in self.turns if not t.passed)
        lines.append(
            f"  ---- {ok}/{total} checks, {len(self.turns) - failed}/{len(self.turns)} turns clean"
        )
        return "\n".join(lines)
