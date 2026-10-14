# NovaOps final project — submission

Fill this in and commit it to your repository as `SUBMISSION.md`. It is the first thing read,
and it is how every trace gets found.

---

## 1. Repository

| | |
| ------------------- | ------------------------------------------------ |
| **Repository URL**  | `https://github.com/barv30/novaops-company-brain` |
| **Access**          | instructor added as collaborator |
| **Commit reviewed** | `b2f36cff4fd6726f2b363253fb01e64dc0c658d4` — the code that produced the traces below; later commits add only documentation and judge fixes (ADR-029) |

## 2. Langfuse

| | |
| ------------------ | --------------------------------------------- |
| **Host region**    | `cloud.langfuse.com` EU |
| **Project name**   | `novaops-submission` |
| **Instructor role**| Member *(required — traces are read through the API)* |
| **Invitation accepted** | pending |

## 3. Scope completed

| Workflow | Built | Notes |
| --------------------------- | ------ | ----- |
| 1 — Maya · HR and onboarding | required | yes — read-only scope, permission filter inside FAISS search, typed `OnboardingChecklist`, `AccessIntent` handoff to Webex |
| 2 — Webex · IT operations    | required | yes — write gate on persisted approvals only, idempotent writes, reuses T001 / AR001 |
| 3 — Vendor · CRM extraction  | optional, +5 | not attempted (ADR-001) |
| 4 — Renewal · contract renewal | optional, +10 | not attempted (ADR-001) |

| Optional stage | Done | Evidence |
| ------------------------------- | -------- | -------- |
| Lesson 12 — loop engineering     | no | |
| Lesson 14 — packaging and deploy | no | |

## 4. Durable-behavior evidence

Some required behavior cannot be seen in a single trace. Point at the commit, test, or trace
that proves each — one line, or "not attempted".

| Check | Evidence |
| ------------------------------------------------------------- | -------- |
| Replaying a request creates no second ticket or access request | `evals/test_idempotency.py` — `test_replaying_rachels_request_creates_no_second_row`, `test_the_whole_case_replayed_end_to_end_moves_nothing` |
| A pending approval survives the process being killed and restarted | `evals/test_restart_survival.py` — `test_a_pending_approval_survives_the_process_dying`, `test_the_gate_opens_in_a_later_process_against_the_recorded_decision` (separate OS processes, `AsyncSqliteSaver` on disk) |
| The write gate releases only against a recorded approval | `tests/test_write_gate.py`, `evals/test_approval_loop.py` — `test_no_phrasing_opens_it`, `test_a_partly_approved_request_stays_shut`; in traces: `gate_reason` on `W-I-03` and `W-S-01` t4 |
| *Renewal (optional): every event replayed twice leaves one update and one notification per recipient* | not attempted |

## 5. Trace index

One row per item in `EVALUATION-INPUTS.yaml` — 27 required, 33 with both optional workflows.
**Fill in the trace id column only**; the ids and turn numbers are already correct. Leave a row
blank if you did not run it.


### Maya - HR and onboarding

| Item | Turn | Trace ID |
| ---- | ---- | -------- |
| `M-I-01` | — | 6e6213e212d11628e0ce3ba3637da4a8 |
| `M-I-02` | — | fd7cd8a87338ace6c9353982af1af223 |
| `M-I-03` | — | fe8661ec4bcd855f5d0f558f3d337690 |
| `M-S-01` | 1 | b7ed166fa32b49dad034a9bccf9b5cd0 |
| `M-S-01` | 2 | 016241521e367678a17148e32803562f |
| `M-S-01` | 3 | 4af6bccd389e05dd19b0296bbe8fc9c2 |
| `M-S-01` | 4 | ef8a25216d8fb76fb5fb0625e7ca18c3 |
| `M-S-01` | 5 | e859b6507bf23e68c3e87f56597191c0 |
| `M-S-01` | 6 | cd00a984b3bacc8001efc4d3bed618a8 |
| `M-S-01` | 7 | 6e2fb7079535819ce8ee36e231919d99 |
| `M-S-01` | 8 | 9146550997632c676801ab77a7ceb6f3 |
| `M-S-01` | 9 | 164d147e406a8f784a85cfbe9588e1bd |
| `M-S-01` | 10 | c4f187de358ffca2bda2ff263e1f87e6 |
| `M-S-01` | 11 | 961fc27cb9babcc22b12b20498416e60 |
| `M-S-01` | 12 | 33a2fa3dc224df8e2daa8a541bd3003d |
| `M-S-02` | 1 | da59f6c22d9f37ca7502880f55c0278b |
| `M-S-02` | 2 | 9166645c7fabaa7ffdb6052ae61f78ea |
| `M-S-02` | 3 | 149295ebb50dd044544045ea526f417b |
| `M-S-02` | 4 | 959420fbd7fe1029f75e0be66a794d51 |
| `M-S-02` | 5 | 85202da70a8ddffb7496d99d82e7eaef |

### Webex - IT operations

| Item | Turn | Trace ID |
| ---- | ---- | -------- |
| `W-I-01` | — | 90a947e60d2c50ee27766760e8bc4323 |
| `W-I-02` | — | 83f6d45bd499f0e3bba4fd75ffda3aad |
| `W-I-03` | — | 4df4e47e4dec5271ccca7b566c092dcd |
| `W-S-01` | 1 | 9596bf03b50f87487dc038e002e56ef4 |
| `W-S-01` | 2 | 203b5512ce14514c9ae74d518308d719 |
| `W-S-01` | 3 | ead579802dbf66ca2b68041a1bee619a |
| `W-S-01` | 4 | 8205b498ebd9fa52e8ce1a317e62f8b5 |

### Vendor - CRM extraction  *(optional)*

| Item | Turn | Trace ID |
| ---- | ---- | -------- |
| `V-I-01` | — | |
| `V-I-02` | — | |
| `V-I-03` | — | |

### Renewal - contract renewal  *(optional)*

| Item | Turn | Trace ID |
| ---- | ---- | -------- |
| `R-I-01` | — | |
| `R-I-02` | — | |
| `R-I-03` | — | |

---

## 6. Anything I should know

Two or three sentences, optional. Known gaps, a deliberate deviation and why, something you
would do differently. Honesty here costs nothing and helps — a stated limitation reads better
than one discovered while marking.

**Notes.**
- **Every trace carries deterministic scores**: `binding_checks` and `turn_clean`, posted
  by `scripts/score_submission.py` against expectations taken from the golden data or the
  spec, each naming its source (ADR-026). All 27 at a glance in
  [`docs/SUBMISSION-SCORES.md`](docs/SUBMISSION-SCORES.md): **146/151 checks, 23/27 clean.**
- **Two LLM judges** (`scripts/judges.py`, ADR-025) score every turn:
  `citation_faithfulness` 0.70 mean, `no_false_grant` 0.93. I read every case where a judge
  and a code check disagree against the full trace
  ([`docs/JUDGE-VS-CODE.md`](docs/JUDGE-VS-CODE.md)): six of eight were the judge's error —
  three from my own digest cutting tool results at 1,500 characters — and two were real
  citation gaps. **Both `no_false_grant` zeros are false positives: no answer claims access
  was granted or a record filed.** The judge fixes are in the code for future runs; the
  traces were not re-scored (ADR-029).
- Each turn span carries `trajectory` metadata (tool calls, carried context, the write
  record), the scope as `agent_scope` (`scope` is reserved by Langfuse), `plan_override`
  where application code overruled the planner (ADR-024, ADR-027), and `handoff` where Maya
  prepared a request for Webex (ADR-023).
- Run history: the evaluation set was run twice; fixes between runs were measured on golden
  development data. First run's results: commit `38e7572`. Details: ADR-029.
