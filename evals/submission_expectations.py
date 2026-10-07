"""What each of the 27 submission traces is checked against, and where that bar comes from.

`EVALUATION-INPUTS.yaml` carries inputs only. To score the submitted run we need
expectations for each item, and we take them from the two places the course provides —
never written to fit our own answers:

- **GOLDEN-DATASETS.json.** Six items are the same material as golden cases, matched by
  their opening message: `M-S-01` is `S2`, `M-S-02` is `S9`, `W-S-01` is `S8`, and
  `M-I-01..03` are `SIMPLE_001`, `SIMPLE_002`, `SIMPLE_007`. Binding fields only
  (CLAUDE.md): a single-turn case contributes `expected_sources`; `requires_tool_call` is
  left out, because it describes the reference implementation's route (the golden cases
  say `false` for questions the reference answered by pre-retrieval, not by a tool).
- **PROJECT-DESCRIPTION.md.** `W-I-01..03` have no golden case. The specification states
  their expected outcome in prose; each expectation below quotes the line it encodes.

Every item also gets the invariant checks that hold for every turn of its scope: no write
tool on a Maya turn, and no claim of a grant on any turn.
"""

from __future__ import annotations

from dataclasses import dataclass

from evals import golden
from evals.golden import Turn

#: The write tools. On a Maya turn any of them is a defect (invariant 2, ADR-003).
WRITE_TOOLS = ["create_ticket", "create_access_request", "request_approval"]

#: Wording that asserts a grant. The judge `no_false_grant` reads for this in context;
#: this is the cheap literal version, so both a code check and a judge look at it.
GRANT_CLAIMS = ["access has been granted", "access was granted", "has been provisioned",
                "seat has been assigned", "you now have access"]

#: Evaluation item -> golden session id.
SESSIONS = {"M-S-01": "S2", "M-S-02": "S9", "W-S-01": "S8"}

#: Evaluation item -> golden single-turn case id.
SINGLES = {"M-I-01": "SIMPLE_001", "M-I-02": "SIMPLE_002", "M-I-03": "SIMPLE_007"}


@dataclass(frozen=True)
class Expectation:
    """One trace's checks, and the provenance a reader needs to trust them."""

    turn: Turn
    origin: str


def _spec_cases() -> dict[str, Expectation]:
    return {
        "W-I-01": Expectation(
            Turn(
                n=1, user="What is the status of my Webex login ticket?",
                should_use_tools=True,
                required_facts=["T001", "open"],
                forbidden_tools=WRITE_TOOLS,
                forbidden_facts=GRANT_CLAIMS,
            ),
            "PROJECT-DESCRIPTION.md §Workflow 2: 'a ticket status lookup; a control case'. "
            "Seed record: T001, Rachel Stein, status open.",
        ),
        "W-I-02": Expectation(
            Turn(
                n=1, user="Which of my team members has a Webex seat?",
                should_use_tools=True,
                required_facts=[
                    # The spec's point, in any wording: the records cannot say who holds a
                    # seat. "do not say" was missing from the first version of this list
                    # and failed W-I-02's answer, which says exactly that — a scorer bug,
                    # caught by reading the answer before posting any score.
                    "not recorded|no record|not tracked|does not record|isn't recorded|"
                    "no employee-to-seat|not track|do not say|does not say|"
                    "only as counts|not per person",
                    "Rachel",
                    "Maya",
                    "Noam",
                ],
                forbidden_tools=WRITE_TOOLS,
                forbidden_facts=GRANT_CLAIMS,
            ),
            "PROJECT-DESCRIPTION.md §Workflow 2: 'The correct response says seat assignments "
            "are not recorded, then reports what is: Rachel's request is blocked, Maya's "
            "provisioning task is blocked, Noam is not entitled by role.'",
        ),
        "W-I-03": Expectation(
            Turn(
                n=1, user="I joined Customer Success and need Webex access.",
                should_use_tools=True,
                required_facts=["T001", "AR001", "42|seat limit|over the limit|40-seat"],
                # A second ticket or request is "a duplicate write, not diligence".
                forbidden_tools=["create_ticket", "create_access_request"],
                forbidden_facts=GRANT_CLAIMS,
            ),
            "PROJECT-DESCRIPTION.md §Workflow 2 scenario: 'eligible, but blocked by the seat "
            "limit ... T001 and AR001 already exist and must be reused — creating a second "
            "ticket for this employee is a duplicate write'.",
        ),
    }


def _single(item: str) -> Expectation:
    case = next(c for c in golden.single_turn_cases() if c["case_id"] == SINGLES[item])
    return Expectation(
        Turn(
            n=1, user=case["input"],
            expected_sources=list(case.get("expected_sources") or []),
            forbidden_tools=WRITE_TOOLS,
            forbidden_facts=GRANT_CLAIMS,
        ),
        f"GOLDEN-DATASETS.json single_turn_cases {SINGLES[item]} (binding: expected_sources)",
    )


def _session_turn(item: str, n: int) -> Expectation:
    session = golden.session(SESSIONS[item])
    turn = session.turns[n - 1]
    return Expectation(turn, f"GOLDEN-DATASETS.json session {session.id} turn {n}")


def expectation_for(item: str, turn: int | None) -> Expectation:
    """The checks for one row of the trace index. Raises KeyError for an unknown item."""

    if item in SESSIONS:
        if turn is None:
            raise KeyError(f"{item} is a session; a turn number is required")
        return _session_turn(item, turn)
    if item in SINGLES:
        return _single(item)
    return _spec_cases()[item]
