# NovaOps final project — submission

Fill this in and commit it to your repository as `SUBMISSION.md`. It is the first thing read,
and it is how every trace gets found.

---

## 1. Repository

| | |
| ------------------- | ------------------------------------------------ |
| **Repository URL**  | `https://github.com/barv30/novaops-company-brain` |
| **Access**          | instructor added as collaborator |
| **Commit reviewed** | `73fe160f6ac5f15dbfbc37cf9aca67b694c8c833` — the code that produced the traces below |

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
| `M-I-01` | — | 9d8b01c63ed98d4900dfa237b060fa33 |
| `M-I-02` | — | 99e17af7daf628bf03f667bfe8c67a5a |
| `M-I-03` | — | 9cf8a54f5b0132c523682a5d8142dda0 |
| `M-S-01` | 1 | 7662ad480e0f14835d43f3da8faabf3e |
| `M-S-01` | 2 | 80716fce883af61e684cac96d76eced0 |
| `M-S-01` | 3 | 63312ef102138f1d641573b3c489d897 |
| `M-S-01` | 4 | da22648ccf0c8647dc231d4da8c86985 |
| `M-S-01` | 5 | 25759bc897a1a1935457f753f91cdec4 |
| `M-S-01` | 6 | 4a451c9979bf7cffe111e161acbf8db2 |
| `M-S-01` | 7 | 2c0c4b37b892661827d57a5637c149db |
| `M-S-01` | 8 | ce9c7e1852d22582fb4943c4cf011eba |
| `M-S-01` | 9 | 52ecc7118ca189ca33e17261ab42affe |
| `M-S-01` | 10 | 3c06a0c22b669defd5bd7b48e13cc766 |
| `M-S-01` | 11 | 7be5233caca0351bfca8a202d1949086 |
| `M-S-01` | 12 | 0fc63700465fa865cc247539713a2d09 |
| `M-S-02` | 1 | e6b3381e01e8f25ac0cda5f61fe730c9 |
| `M-S-02` | 2 | c0bd52bb9f2a12913674df3ec459140a |
| `M-S-02` | 3 | aba405f913a0da1e1569f999058b7ec5 |
| `M-S-02` | 4 | 472dfcb13a1f4a8012d2fd3cf194fc01 |
| `M-S-02` | 5 | 734b8725c0480d7dafc350740407e550 |

### Webex - IT operations

| Item | Turn | Trace ID |
| ---- | ---- | -------- |
| `W-I-01` | — | e06a60f144d13e2e4fee6b70ac868045 |
| `W-I-02` | — | c1ed8e0e28c08962d51d334d2df3f265 |
| `W-I-03` | — | ee1865eb4db93441fe935eee655b29f9 |
| `W-S-01` | 1 | d872056dffa4242b10abe5b0d98f652c |
| `W-S-01` | 2 | 211c1d07c9b8f5b58b6c933203bfb641 |
| `W-S-01` | 3 | 9aff4f7c221e05f0b07db49261054c73 |
| `W-S-01` | 4 | 05660d641f60be080a4730ffc183dd7b |

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
- **Measured against `GOLDEN-DATASETS.json` before submitting**, binding fields only: S2 39/43,
  S9 30/33, S8 11/13 on this run. Most misses are turns where Nova answered a status question
  from an earlier turn instead of looking again (ADR-022 records this as variance, measured
  over repeated runs). S8 turn 1 listed the VPN steps in 3 of 3 runs beforehand and omitted
  them in this one. The set was run once, as asked, and not re-run for a better result.
- **Every trace carries deterministic scores** too — `binding_checks` and `turn_clean`,
  posted after the run by `scripts/score_submission.py` against expectations taken from
  the golden data or the spec (ADR-026). All 27 at a glance:
  [`docs/SUBMISSION-SCORES.md`](docs/SUBMISSION-SCORES.md) — 142/151 checks, 21/27 clean.
- **LLM judges** (`scripts/judges.py`, ADR-025) score every turn in this project:
  `no_false_grant` 1.00 on all 27; `citation_faithfulness` 0.73 mean. Some low citation
  scores are fair, while others penalise true statements about the system itself (e.g.
  "Maya's scope is read-only"), which no tool result states.
- Each turn span carries `trajectory` metadata (tool calls, carried context, the write
  record), the scope as `agent_scope` (`scope` is reserved by Langfuse), and `plan_override`
  where application code overruled the planner (ADR-024).
