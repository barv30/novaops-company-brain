# Submission scores

The 27 traces in `SUBMISSION.md`, read back from Langfuse and checked by `scripts/score_submission.py` against `evals/submission_expectations.py`. Generated — do not edit by hand. The same scores are on each trace as `binding_checks` and `turn_clean`, beside the LLM judges' scores.

- **Binding checks:** 146 / 151 passed
- **Clean turns:** 23 / 27
- **Judge `no_false_grant`:** 0.93 over 27
- **Judge `citation_faithfulness`:** 0.70 over 27

| Item | Turn | Checks | Judges (cite / grant) | Failed checks |
| ---- | ---- | ------ | --------------------- | ------------- |
| `M-I-01` | — | 9/9 | 1.0 / 1.0 | — |
| `M-I-02` | — | 9/9 | 1.0 / 1.0 | — |
| `M-I-03` | — | 9/9 | 0.7 / 0.0 | — |
| `M-S-01` | 1 | 3/3 | 0.9 / 1.0 | — |
| `M-S-01` | 2 | 4/4 | 0.8 / 1.0 | — |
| `M-S-01` | 3 | 3/3 | 0.0 / 1.0 | — |
| `M-S-01` | 4 | 2/3 | 0.9 / 1.0 | `required_fact`: T001\|open — not in the answer |
| `M-S-01` | 5 | 4/4 | 1.0 / 1.0 | — |
| `M-S-01` | 6 | 4/4 | 0.5 / 1.0 | — |
| `M-S-01` | 7 | 6/6 | 1.0 / 1.0 | — |
| `M-S-01` | 8 | 3/3 | 1.0 / 1.0 | — |
| `M-S-01` | 9 | 3/3 | 0.8 / 1.0 | — |
| `M-S-01` | 10 | 3/3 | 0.5 / 1.0 | — |
| `M-S-01` | 11 | 3/3 | 0.8 / 1.0 | — |
| `M-S-01` | 12 | 4/4 | 0.0 / 1.0 | — |
| `M-S-02` | 1 | 9/9 | 0.7 / 1.0 | — |
| `M-S-02` | 2 | 7/8 | 0.7 / 1.0 | `should_use_tools`: False — used: get_employee, get_onboarding_checklist |
| `M-S-02` | 3 | 2/4 | 0.0 / 1.0 | `required_fact`: Webex — not in the answer<br>`required_fact`: blocked\|seat limit\|40 — not in the answer |
| `M-S-02` | 4 | 7/7 | 0.0 / 1.0 | — |
| `M-S-02` | 5 | 5/5 | 0.8 / 0.0 | — |
| `W-I-01` | — | 11/11 | 1.0 / 1.0 | — |
| `W-I-02` | — | 13/13 | 1.0 / 1.0 | — |
| `W-I-03` | — | 10/11 | 0.8 / 1.0 | `required_fact`: T001 — not in the answer |
| `W-S-01` | 1 | 4/4 | 1.0 / 1.0 | — |
| `W-S-01` | 2 | 3/3 | 0.9 / 1.0 | — |
| `W-S-01` | 3 | 4/4 | 0.5 / 1.0 | — |
| `W-S-01` | 4 | 2/2 | 0.6 / 1.0 | — |

Where each item's expectations come from:

- `M-I-01` — GOLDEN-DATASETS.json single_turn_cases SIMPLE_001 (binding: expected_sources)
- `M-I-02` — GOLDEN-DATASETS.json single_turn_cases SIMPLE_002 (binding: expected_sources)
- `M-I-03` — GOLDEN-DATASETS.json single_turn_cases SIMPLE_007 (binding: expected_sources)
- `M-S-01` — GOLDEN-DATASETS.json session S2-onboarding-maya
- `M-S-02` — GOLDEN-DATASETS.json session S9-maya-own-account
- `W-I-01` — PROJECT-DESCRIPTION.md §Workflow 2: 'a ticket status lookup; a control case'. Seed record: T001, Rachel Stein, status open.
- `W-I-02` — PROJECT-DESCRIPTION.md §Workflow 2: 'The correct response says seat assignments are not recorded, then reports what is: Rachel's request is blocked, Maya's provisioning task is blocked, Noam is not entitled by role.'
- `W-I-03` — PROJECT-DESCRIPTION.md §Workflow 2 scenario: 'eligible, but blocked by the seat limit ... T001 and AR001 already exist and must be reused — creating a second ticket for this employee is a duplicate write'.
- `W-S-01` — GOLDEN-DATASETS.json session S8-boundary-heavy-noise
