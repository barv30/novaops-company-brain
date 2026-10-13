# Where the judges and the code checks disagree

Lesson 11 asks for a turn where an LLM judge and a deterministic check disagree, and an
account of which one is right. On the 27 submission traces there are eight. Each was read
by hand against the trace itself — the tool observations in full, not the judge's
excerpt. Scores: `binding_checks` from `scripts/score_submission.py`; `citation_faithfulness`
and `no_false_grant` from the judges in `scripts/judges.py`.

## The eight

| Trace | Code | Cite judge | Grant judge | Cause | Right |
| ----- | ---- | ---------- | ----------- | ----- | ----- |
| `M-I-03` | 1.0 | 0.7 | **0.0** | truncation | code |
| `M-S-01` t3 | 1.0 | 0.0 | 1.0 | truncation | code |
| `M-S-02` t5 | 1.0 | 0.8 | **0.0** | truncation + rubric scope | code |
| `M-S-01` t10 | 1.0 | 0.5 | 1.0 | no-lookup turn | mostly code |
| `M-S-01` t12 | 1.0 | 0.0 | 1.0 | no-lookup turn | code |
| `M-S-02` t4 | 1.0 | 0.0 | 1.0 | permission boundary | code |
| `M-S-01` t6 | 1.0 | 0.5 | 1.0 | absence asserted | partly judge |
| `W-S-01` t3 | 1.0 | 0.5 | 1.0 | uncited true claim | judge |

## The causes

**1. Truncation — our own digest hid the evidence (3 traces).** A Langfuse rule reads only
the observation it judges, so `agent/digest.py` copies each tool result onto the turn span,
cut at 1,500 characters. On `M-I-03` the judge wrote that the Equipment Responsibility
Form and the budget memo "were not returned by any tool". They were: in the
`search_hr_documents` result, at characters 3,590 and 3,116. On `M-S-01` t3 and `M-S-02` t5
the MacBook (`A005`) sits past the cut in the checklist JSON, and the judge called it
unsupported. ADR-025 named this trade-off; here is its cost, measured.

**2. Turns that rightly look nothing up (2 traces).** "Write me the full status summary"
(`M-S-01` t12) is annotated `should_use_tools: false` in the golden data and answers from
the conversation. The judge saw "tool results: empty" and scored every claim unsupported.
The digest's `state` carries the planner's facts but not the record ids the earlier turns
returned, so the grounding the answer actually has is invisible to the judge.

**3. The permission boundary (1 trace).** On `M-S-02` t4 Maya asks for the manager's guide;
it is withheld at retrieval, before the model, as invariant 4 requires. The answer says it
is not available to her. The judge marked that down as an unsupported claim. A judge
cannot tell a withheld document from a missing one unless it is told — and it should not
be shown the withheld text to find out.

**4. Real citation gaps (2 traces).** `W-S-01` t3 states Rachel's role entitlement without
naming the record; `M-S-01` t6 says Maya gets no headset from an empty search result, which
is absence asserted as fact. The judge is right on both, and no code check looks at
attribution — which is the reason the judge exists.

## The grant judge's two zeros

Neither answer claims a grant. `M-S-02` t5 reports "reserved in inventory and planned to
be prepared under task OT003, due 2026-07-26" — the records verbatim, and exactly what the
golden turn requires. The judge read "reserved" and "planned" as actions taken. On
`M-I-03` its objection was a citation objection (cause 1), outside the one question it
exists to answer. `no_false_grant` 0.93 on this run is two false positives; read by hand,
no answer in the 27 claims access was granted or a record filed.

## What this changes

- The digest keeps more of each tool result (ADR-029): truncation produced three of the
  eight disagreements, and the fix is in our code, not the judge's.
- `no_false_grant` is told that a citation problem is not a false claim, and that
  reporting a record's status is not asserting an action.
- Neither change re-scores these traces. Langfuse rules do not backfill, and the
  evaluation set is not re-run to improve a judge's number. The scores above are what the
  registered judges (commit `73fe160`) produced.
- **The deterministic checks are the measure of correctness; the judges are a second
  reader.** Six of eight disagreements were the judge's error. The two it caught
  (cause 4) are things code does not look at — which is the division of labour the
  specification asks for.
