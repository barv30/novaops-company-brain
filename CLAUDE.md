# CLAUDE.md — NovaOps operational company brain

Project instructions for coding agents. Read this before touching anything.

## What this is

NovaOps' internal assistant over a messy, permission-sensitive enterprise world: an
unstructured document corpus, a structured operational database, and governed actions.
Built as the capstone for the AI Engineer course. The specification is
[PROJECT-DESCRIPTION.md](PROJECT-DESCRIPTION.md) — **it is authoritative; this file is not.**
When they disagree, the spec wins and this file gets fixed.

**Scope for this build: the required path only.**

| Workflow | Status |
| --- | --- |
| 1 — Maya · HR and onboarding (agent, HR scope, read-only) | in scope |
| 2 — Webex · IT operations (agent, IT scope, gated writes) | in scope |
| Lesson 11 — observability and evals | in scope, required |
| 3 — Vendor · CRM extraction | **out of scope** |
| 4 — Renewal · contract renewal | **out of scope** |
| Lesson 12 / 13 / 14 stages | **out of scope** |

Do not start out-of-scope work. If something seems to need it, say so and stop.

## How this is assessed — the single most important fact

**Assessed by reading, not by running.** Nobody executes this code. The repository and the
Langfuse traces are the entire submission. Two consequences that govern every decision:

1. Anything asserted in [README.md](README.md) must be visible in the code. An undocumented
   behavior and an undelivered one look identical from the outside.
2. Where you can choose where to prove something, **prove it in the trace**.

Marking: 50 points engineering quality (architecture, boundaries, tests, the write gate,
idempotency, what the repo shows about how we worked) + 50 points Maya/Webex correctness
across 27 traces.

## The five invariants

These are not style preferences. Each one is directly assessed. Never weaken one to make a
test pass — fix the test or raise the conflict.

1. **Caller identity and user group are application inputs, never model inferences.**
   They enter through `Caller` and are never parsed out of message text.
2. **A model may propose or explain an action. Only application code may record that it
   happened.** No tool result is trusted as proof of a write.
3. **Every write is idempotent.** Replaying a request creates no second ticket, access
   request or approval. Writes are keyed, not appended.
4. **Permission filtering happens at the retrieval layer, before chunks reach the model.**
   A restricted chunk that reaches the model has already leaked, whatever the model then
   says about it. Never filter by refusing afterwards.
5. **Every run ends in exactly one `status`** — `completed`, `blocked`, `pending`,
   `needs_human`, `failed` — **and the prose must agree with it.** `status` is where the
   workflow got to. What happened to the business request (granted, refused, recommended)
   belongs in the typed return object, never in the status.

A sixth rule that is not quite an invariant but breaks cases when ignored:
**newer or more specific guidance overrides older or more general guidance.** The corpus
contradicts itself on purpose — an internal memo has quietly changed a threshold a policy
still states.

## Stack

Fixed by the assessment:

- **Bedrock**, `us.amazon.nova-2-lite-v1:0`, behind the single seam `src/novaops/llm/client.py`
- **Langfuse** for tracing
- **Python 3.12**
- **Tools behind an MCP server boundary** — never functions called directly from the graph

Chosen (see [docs/DECISIONS.md](docs/DECISIONS.md) for the reasoning):

- **LangGraph** — extending the Lesson 10 stage-3 graph, not rewriting it
- **FAISS** behind a `DocumentStore` interface — swapping to OpenSearch touches one file
- **SQLite** for both the business database and the LangGraph checkpointer
- **Pydantic** for every return contract

## Repository layout

```
src/novaops/
  config.py           settings, paths, and the pinned clock
  identity.py         Caller, manager derivation, audience resolution
  contracts/          typed returns — OnboardingChecklist, AccessDecision, AccessIntent
  llm/                the one Bedrock seam
  retrieval/          ingest, store, permission filter, rerank
  db/                 schema load, reseed, repositories
  mcp_server/         the tool server — every tool lives behind this boundary
  agent/              graph, classify, scopes, write_gate, state
  observability/      Langfuse wiring, span names, trace metadata
evals/                our own checks — read as part of the submission
tests/                unit and integration tests
scripts/              ingest, reseed, run the evaluation set
docs/DECISIONS.md     ADR log — append an entry for every real choice
novaops-enterprise-agent-dataset/   the world (read-only, never edit)
```

## Source-of-truth data — read, never modify

- `novaops-enterprise-agent-dataset/documents/` — 102 markdown files, seven collections
- `novaops-enterprise-agent-dataset/database/` — `schema.sql`, `seed.sql`, `seed.json`
- `GOLDEN-DATASETS.json` — annotated material we develop against
- `EVALUATION-INPUTS.yaml` — the measured set, inputs only. **Run it once, at the end.**

## Verified facts about the data

Checked directly against the dataset. Trust these over prose in any document.

- **Only 32 of 102 documents carry `audience` front matter**: `handbook/` (15, `audience: all`)
  and `manager_playbook/` (17, `audience: manager`). The other 70 carry none.
  **Absent means unrestricted, not deny.** A filter that excluded unlabelled documents would
  block 70 of 102 files and fail every case in the set.
- **Manager cannot be derived from `user_group`.** Five employees manage someone —
  E004 (`UG_HR`), E006 (`UG_IT`), and **E012, E013, E018 — all `UG_REGULAR`**.
  The rule is: *a caller is a manager when some employee's `manager_id` equals their id.*
- The clock is pinned to **2026-07-01** for every case. The dataset's `as_of_date` is
  2026-07-02. Never call `date.today()`; take the date from `config.AS_OF`.
- Key records this project turns on: `SUB001` (Webex, **42 active seats against a 40-seat
  limit**), `C001` (the Webex contract), `T001` and `AR001` (Rachel Stein's existing ticket
  and access request — **reuse, never duplicate**).
- `E001` Maya Cohen and `E020` Maya Rosen share a first name. Disambiguate by id.
- `software_subscriptions` records seat *counts* only. **There is no employee-to-seat table.**
  `systems_allowed_by_role` lists who is *entitled*, which looks like the answer and is not.

## The six deliberate conflicts

The dataset disagrees with itself on purpose. The course deck names six categories; each is
a trap a naive implementation walks into, and handling one is worth more than fluency.

| Conflict | What it looks like here |
| --- | --- |
| **Policy drift** | A policy states a threshold; a later memo quietly changed it. Newer and more specific wins. |
| **Source conflict** | The database and the documents disagree about the same fact — e.g. a contract's owner department. Decide which governs, say which, and cite it. |
| **Valid but impossible** | The request is legitimate and the requester is eligible, but it cannot be fulfilled. Rachel Stein at 42-of-40 seats. |
| **Access boundary** | Perfect evidence exists and the requester may not see it. `manager_playbook/` for a non-manager. |
| **Identity collision** | People share first names — E001 Maya Cohen and E020 Maya Rosen. Retrieval cannot safely infer who is meant; disambiguate by id. |
| **Governance** | The system must decide what governs, cite the evidence, and explain why. |

**A correct operational answer is often "no", with the governing evidence attached.**
Truthful failure scores; confidently wrong does not.

Each workflow has a focus worth keeping in mind while building it: **Maya is RAG-focused**
(retrieval quality, citations, the permission boundary) and **Webex is authorization-focused**
(the write gate, durable approval, idempotency). And: choosing when to use deterministic code
rather than a model call is itself part of the design being assessed.

## Conventions

- Every workflow returns a **typed Pydantic object**, validated before it is returned.
- Every factual claim in an answer carries a **citation** to its source document path. An
  uncited correct answer is a failure.
- Retrieval refinement is **bounded to one extra round**, then answer with what is available
  and say what is missing. "Search again until something turns up" has no stopping point.
- `classify` **is** the router. Never add a second top-level router above it.
- The write gate separates two questions that are easy to conflate:
  **visibility** (which read tools enter the context — may use the model-inferred
  `action_confirmed`) and **authorization** (whether a write may be released — reads *only* a
  persisted approval row, fails closed). Never release a write on `action_confirmed`.
- Use `AsyncSqliteSaver`, never `InMemorySaver`. The restart test cannot pass otherwise.
- **Two stores persist between runs, not one.** `var/novaops.db` holds business records and
  `var/checkpoints.db` holds conversation history per `thread_id`. An independent case needs
  a fresh database *and* an unused thread id; reusing a thread id replays last run's history,
  the planner sets `requires_tools=False`, and the turn uses no tools. That reads as a broken
  loadout and is stale state.
- Async throughout — the MCP tools are async-only.

## Working rules for the agent

- **Append to [docs/DECISIONS.md](docs/DECISIONS.md)** whenever a real choice is made. The
  decision record is graded; an undocumented decision earns nothing.
- Keep [README.md](README.md)'s claim-to-file map current in the same commit as the code.
- Core logic stays **directly callable**. Nothing may be reachable only through a UI.
- Never commit AWS keys, Langfuse keys, approval tokens, `.env` files, or dataset documents
  moved out of their folder.
- Prefer extending Lesson 10's graph over writing new orchestration. The project delta is the
  wider corpus, the permission boundary, the durable write gate and the twelve-turn replay —
  not new graph mechanics.
- When a golden expectation disagrees with our implementation, check which kind of field it
  is first. **Binding**: `required_facts`, `forbidden_facts`, `forbidden_tools`,
  `forbidden_sources`, `expected_sources`, `should_use_tools` — a mismatch is a real defect.
  **Illustrative**: `expected_tools`, `optional_tools`, `sequence`, `arg_checks`,
  `expected_intent`, `expected_route` — these describe the course's reference implementation
  and will drift. Map them once in our scorer. **Never relax a binding field.**

## Commands

```bash
python -m venv .venv
source .venv/Scripts/activate        # PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"

python -m scripts.reseed             # rebuild the business database from seed.sql
python -m scripts.ingest             # build the FAISS index, preserving audience metadata
python -m novaops.mcp_server         # serve the tools at http://127.0.0.1:9877/mcp

python -m scripts.submit --dry-run   # the 27 graded traces, planned; drop --dry-run to run
pytest                               # unit + integration tests
pytest evals/                        # our own graded checks
ruff check src tests evals
```

## Reference implementations to draw from

| Need | Where |
| --- | --- |
| graph, loadouts, write gate, distillation | `../lesson-10-context-engineering/code/02-dynamic-tool-loadout/`, `03-history-distillation/` |
| MCP server, FAISS-backed tools | `../lesson-10-context-engineering/code/server/` |
| `interrupt()` + `AsyncSqliteSaver` durable approval | `../lesson-09-agentic-frameworks/code/03-human-in-the-loop/` |
| Langfuse `CallbackHandler`, `propagate_attributes`, scoring, judges | `../lesson-11-observability-and-evals/code/` |
| retrieval filtering and reranking | `../lesson-07-rag-advanced/code/02-filtering/`, `03-reranking/` |

These are reference material. Copy deliberately and adapt; do not import across repositories.
