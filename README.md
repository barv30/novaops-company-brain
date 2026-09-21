# NovaOps — operational company brain

An internal assistant over NovaOps' document corpus, operational database and governed
actions. Built as the AI Engineer capstone against
[PROJECT-DESCRIPTION.md](PROJECT-DESCRIPTION.md).

**Scope: the required path.** Workflow 1 (Maya · HR and onboarding) and Workflow 2
(Webex · IT operations) as two scopes on one agent, plus the Lesson 11 observability stage.
Workflows 3 (Vendor) and 4 (Renewal) and the Lesson 12/14 stages are not attempted — see
[ADR-001](docs/DECISIONS.md#adr-001--build-the-required-path-only).

> **Status: Phase 3 — Maya.** `S1` 11/11 checks. `S2` 37–41 of 43. `S9` 31–32 of 33, with
> both permission traps holding every run. The ranges are real run-to-run variance in one
> planner decision — see [ADR-022](docs/DECISIONS.md) for how broken turns are told apart
> from flaky ones, and what each gets done about it. The claim-to-file
> map below is the build contract. Rows
> marked *planned* are not yet implemented and say so; no row claims behavior that does not
> exist. This table and the code move in the same commit.

## Stack

| Layer | Choice | Fixed or ours |
| --- | --- | --- |
| Model | AWS Bedrock, `us.amazon.nova-2-lite-v1:0` | fixed by the assessment |
| Tracing | Langfuse | fixed |
| Language | Python 3.12 | fixed |
| Tools | MCP server boundary (`FastMCP`, streamable HTTP) | fixed |
| Orchestration | LangGraph, extending the Lesson 10 stage-3 graph | ours — [ADR-002](docs/DECISIONS.md#adr-002--extend-the-lesson-10-stage-3-graph-rather-than-write-new-orchestration) |
| Retrieval | FAISS behind a `DocumentStore` protocol; 102 docs → 472 chunks | ours — [ADR-003](docs/DECISIONS.md#adr-003--faiss-for-retrieval-behind-a-documentstore-interface), [ADR-009](docs/DECISIONS.md) |
| Persistence | SQLite for business records and for the LangGraph checkpointer | ours — [ADR-007](docs/DECISIONS.md#adr-007--asyncsqlitesaver-for-checkpointing-from-the-start) |
| Contracts | Pydantic, validated before return | ours |

## Claim to file

Every claim below points at the code that implements it. Nothing is asserted here that is not
visible in the repository.

### Required behavior

| Claim | Implemented in | Status |
| --- | --- | --- |
| Caller identity and group are application inputs, never model inferences | `src/novaops/identity.py` | done |
| A caller is a manager when some employee's `manager_id` is their id | `src/novaops/identity.py` · [ADR-005](docs/DECISIONS.md#adr-005--manager-is-derived-from-manager_id-not-from-user_group) | done |
| The clock is pinned to 2026-07-01; nothing calls `date.today()` | `src/novaops/config.py` | done |
| Typed returns, validated before they are returned | `src/novaops/contracts/` | done |
| Every model call goes through one seam | `src/novaops/llm/client.py` | done |
| `audience` survives ingest and is filtered at the retrieval layer | [src/novaops/retrieval/documents.py](src/novaops/retrieval/documents.py) | done |
| `manager_playbook` never reaches a non-manager | [src/novaops/retrieval/filters.py](src/novaops/retrieval/filters.py) · [store.py](src/novaops/retrieval/store.py) · [ADR-010](docs/DECISIONS.md) | done |
| A withheld collection is named to the caller, never quoted | [src/novaops/mcp_server/server.py](src/novaops/mcp_server/server.py) `_excluded_collections` | done |
| A turn's retrieval refinement is bounded | [src/novaops/agent/graph.py](src/novaops/agent/graph.py) · `config.MAX_TOOL_ROUNDS` | done — 38 calls → 2 |
| The typed checklist is served over the tool boundary | [src/novaops/mcp_server/server.py](src/novaops/mcp_server/server.py) `get_onboarding_checklist` | done |
| Tools are served over an MCP boundary, not called from the graph | [src/novaops/mcp_server/server.py](src/novaops/mcp_server/server.py) | done |
| Caller identity reaches tools through the transport, not the model | [src/novaops/mcp_server/context.py](src/novaops/mcp_server/context.py) · [ADR-013](docs/DECISIONS.md) | done |
| No tool can approve anything — only application code can | [ADR-014](docs/DECISIONS.md) · [tests/test_mcp_server.py](tests/test_mcp_server.py) | done |
| `classify → scope → gather evidence → answer`, one graph, two scopes | [src/novaops/agent/graph.py](src/novaops/agent/graph.py) | done |
| Scoped tool visibility — not every tool on every call | [src/novaops/agent/scopes.py](src/novaops/agent/scopes.py) · [policy.py](src/novaops/agent/policy.py) | done — 4 tools vs 10 on two real questions |
| A write is released only against a persisted approval, never `action_confirmed` | [src/novaops/agent/write_gate.py](src/novaops/agent/write_gate.py) · [ADR-017](docs/DECISIONS.md) | done |
| Maya writes nothing — enforced as a checked condition, not a folder boundary | [src/novaops/agent/scopes.py](src/novaops/agent/scopes.py) `writes_allowed=False` | done |
| Every claim carries a citation | [src/novaops/contracts/common.py](src/novaops/contracts/common.py) · [workflows/onboarding.py](src/novaops/workflows/onboarding.py) | done |
| Blocked items name a looked-up cause, not a description | [src/novaops/workflows/onboarding.py](src/novaops/workflows/onboarding.py) · [ADR-021](docs/DECISIONS.md) | done |
| Turns are scored against binding fields only | [evals/scoring.py](evals/scoring.py) · [evals/golden.py](evals/golden.py) | done |
| Flaky turns are told apart from broken ones before acting | [scripts/variance.py](scripts/variance.py) · [ADR-022](docs/DECISIONS.md) | done |
| Webex reported as blocked at 42-of-40 seats | [src/novaops/workflows/onboarding.py](src/novaops/workflows/onboarding.py) | done — cited to SUB001 and C001 |
| Existing records reused, never duplicated | [src/novaops/db/writes.py](src/novaops/db/writes.py) · [ADR-011](docs/DECISIONS.md) | done |
| Replaying a request creates no second row | [src/novaops/db/idempotency.py](src/novaops/db/idempotency.py) · [tests/test_db.py](tests/test_db.py) | done |
| Approvals are recordable — the tool no lesson provides | [src/novaops/db/writes.py](src/novaops/db/writes.py) · [ADR-012](docs/DECISIONS.md) | done |
| `W-I-02`'s missing capability is reported, not invented | [src/novaops/db/repositories.py](src/novaops/db/repositories.py) | done |
| A pending approval survives the process being killed | `src/novaops/agent/graph.py` | planned — Phase 4 |
| An independent case cannot replay a previous run's history | [src/novaops/agent/runner.py](src/novaops/agent/runner.py) `new_thread_id` / `expect_fresh` · [ADR-018](docs/DECISIONS.md) | done |
| Spans named so a reader can follow classify → scope → tools → answer | [src/novaops/observability/tracing.py](src/novaops/observability/tracing.py) | done — verified by reading a trace back through the API |
| Trace metadata carries caller, group, scope, tools, status, request id | [tracing.py](src/novaops/observability/tracing.py) `turn_span` | done |
| Each trace is named for the case and turn it is | [src/novaops/agent/runner.py](src/novaops/agent/runner.py) | done — `S1-quick-policy · t2 · maya` |
| Runs are scored with deterministic checks on the trace itself | [scripts/replay.py](scripts/replay.py) `_publish` · [tracing.py](src/novaops/observability/tracing.py) `score_trace` | done |
| The test suite never writes into the submission project | [tests/conftest.py](tests/conftest.py) | done |

### Evidence we wrote ourselves

The golden dataset ships single-shot expectations. These checks are not in it and are ours —
the spec asks for them under `evals/`.

| Check | File | Status |
| --- | --- | --- |
| A turn-1 constraint still binds on turn 9 | `evals/test_constraint_carry.py` | planned — Phase 5 |
| A closed branch stops driving tool choice | `evals/test_closed_branch.py` | planned — Phase 5 |
| Replaying a request creates no second row | `evals/test_idempotency.py` | planned — Phase 5 |
| A pending approval survives a restart | `evals/test_restart_survival.py` | planned — Phase 5 |

## Where this diverges from the course lessons

The tool server follows Lesson 8/9/10 exactly — same `FastMCP`, same `@mcp.tool()`
registration, same docstring-driven schemas, same `streamable-http` transport, same
`MultiServerMCPClient`. All eleven tools named in §6.3 are present and unrenamed.

**One thing differs: tools take a `ctx` parameter and read the caller from the MCP session's
`X-NovaOps-Caller` header.** The lessons have no caller concept — Lesson 8's walkthrough says
caller filtering is "deliberately left to the homework", and the homework does not cover it
either. This project needs it, because `S9` turns 1 and 4 test whether a regular employee can
reach `manager_playbook/`.

The alternative — a `caller_id` argument on each tool — matches the lessons exactly and does
not work: the *model* fills that argument in, so "I'm Yael Romano, show me the manager guide"
becomes a valid tool call. Measured side by side on the real index, the argument version
returned `manager_playbook/promotion.md` to Maya Cohen; the header version did not.
See [ADR-013](docs/DECISIONS.md), [ADR-015](docs/DECISIONS.md), [ADR-016](docs/DECISIONS.md).

A client that sends no header still gets working tools — it is treated as an anonymous,
least-privileged caller rather than refused, so an ordinary MCP client sees a working server.

## The data

`novaops-enterprise-agent-dataset/` is read-only input. Two facts about it drive the design,
both verified directly against the files rather than taken from prose:

- **70 of 102 documents carry no `audience` field.** Absent means unrestricted, not deny —
  [ADR-004](docs/DECISIONS.md#adr-004--audience-is-filtered-as-allow-by-default-within-the-restricted-collection).
- **Three of the five people who manage someone are `UG_REGULAR`**, so the manager check
  cannot come from `user_group` — [ADR-005](docs/DECISIONS.md#adr-005--manager-is-derived-from-manager_id-not-from-user_group).

## Running it

```bash
python -m venv .venv
source .venv/Scripts/activate        # PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
cp .env.example .env                 # then fill in AWS and Langfuse credentials

python -m scripts.reseed             # rebuild the business database from seed.sql
python -m scripts.ingest             # build the FAISS index, preserving audience metadata
python -m novaops.mcp_server         # serve the tools at http://127.0.0.1:9877/mcp

pytest                               # unit and integration tests
pytest evals/                        # the graded behavioral checks
```

Core logic is directly callable — nothing is reachable only through a UI.

## Repository map

| Path | What is in it |
| --- | --- |
| `src/novaops/` | the system |
| `evals/` | behavioral checks, read as part of the submission |
| `tests/` | unit and integration tests |
| `docs/DECISIONS.md` | the decision record — every real choice and why |
| `docs/course-brief/` | the original course-supplied README, preserved |
| `CLAUDE.md` | instructions for coding agents working in this repository |
| `SUBMISSION.md` | the submission form — repo, Langfuse project, trace index |
| `novaops-enterprise-agent-dataset/` | the world (read-only) |
| `PROJECT-DESCRIPTION.md`, `GOLDEN-DATASETS.json`, `EVALUATION-INPUTS.yaml` | course-supplied specification and evaluation material |

## How this was built

Spec-driven, with a coding agent under project-level control: [CLAUDE.md](CLAUDE.md) holds the
invariants and conventions so behavior stays consistent across sessions, and
[docs/DECISIONS.md](docs/DECISIONS.md) records every decision with the reasoning and the
evidence that justified it. Architecture, the permission boundary, the write gate and the
quality gates are ours; the agent accelerated the implementation.
