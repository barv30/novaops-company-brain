# Decision record

Every real engineering choice on this project, with the reasoning that produced it and the
evidence that justified it. Append, never rewrite — a decision that was later reversed is
more informative than one that was quietly edited away.

**Format.** One entry per decision. `Status` is `accepted`, `proposed`, or
`superseded by ADR-NNN`. Keep `Consequences` honest: what this costs us, not only what it buys.

---

## ADR-001 · Build the required path only

**Date:** 2026-09-19 · **Status:** accepted

**Context.** The specification offers four workflows. Maya and Webex are required; Vendor
(+5) and Renewal (+10) are optional, as are the Lesson 12 and 14 stages. The spec is explicit
that "the required work alone earns full marks" and that "nobody loses marks for finishing the
required system well instead of starting a fifth thing badly."

**Decision.** Build Maya, Webex and the Lesson 11 observability stage. Do not start Vendor or
Renewal.

**Reasoning.** Half the available marks are engineering quality read from the repository, and
that quality is a property of the whole repository, not of the count of workflows in it. A
half-finished optional workflow damages the 50 quality points it is competing with. Revisit
only if the required path is complete, evaluated and submitted with time in hand.

**Consequences.** Ceiling of 100 rather than 120. `workflows/vendor/` and `workflows/renewal/`
in the dataset stay unread. If this reverses, Vendor is the cheaper of the two to add.

---

## ADR-002 · Extend the Lesson 10 stage-3 graph rather than write new orchestration

**Date:** 2026-09-19 · **Status:** accepted

**Context.** The spec, twice: "Do not rebuild Lesson 10's graph. Extend it," and "Reuse your
Lesson 10 stage-2 or stage-3 graph as the starting point rather than writing a new one."
Stage 3 is available at `../lesson-10-context-engineering/code/03-history-distillation/` and
already provides `classify → scope → model → tools`, a `LOADOUTS` table, a write-gate seam,
the `rearm` fallback, and structured history distillation.

**Decision.** Port stage 3 into `src/novaops/agent/` and extend it. Keep the two-file split
the lessons use: `graph.py` for mechanics, `policy.py` for policy.

**Reasoning.** Stage 3's `ConversationMemory` — named fields for `active_constraints`,
`decisions` and `unresolved_items` rather than a single summary string — is precisely the
mechanism Maya's twelve-turn session needs. Turn 9 requires a constraint stated on turn 1;
turn 4 requires a closed branch to stop driving tool choice. A prose summary blurs exactly
those distinctions. Rewriting this would be reimplementing a solved problem while losing the
`memory_is_safe` guard that rejects a distillation which drops an active constraint.

**Consequences.** We inherit stage 3's structure including its `sys.path` juggling, which we
must replace with a proper package layout. The copy is deliberate and one-way: we do not
import across repositories.

---

## ADR-003 · FAISS for retrieval, behind a `DocumentStore` interface

**Date:** 2026-09-19 · **Status:** accepted

**Context.** §6.1 of the spec leaves the retrieval store open: "Lesson 7 used OpenSearch and
Lesson 6 used FAISS; either is fine here, as is anything else you can defend." Lesson 7's
implementation is AWS-managed OpenSearch and needs a live domain
(`lesson-07-rag-advanced/code/01-opensearch-setup/00-aws-setup.md`). Lesson 10's MCP server —
the one we are extending — already backs `search_knowledge_base` and `search_hr_documents`
with FAISS.

**Decision.** FAISS, local, behind a `DocumentStore` protocol in `src/novaops/retrieval/`.

**Reasoning.** The corpus is 102 documents. At that size an engine-side filter buys no
measurable recall or latency over filtering in Python, so OpenSearch would add a running
domain, credentials and setup cost for no behavioral gain. What is actually assessed is that
the **permission boundary is enforced at the retrieval layer and fails closed** — and that
property is implemented identically either way. Choosing the store our MCP server already
uses also keeps the port of Lesson 10's server honest.

**Consequences.** No engine-side reranking; if reranking proves necessary we add it as a
second stage in Python, per Lesson 7's `03-reranking/`. The `DocumentStore` protocol exists
so that swapping to OpenSearch is one implementation file, not a refactor. Not free: the
protocol is a small amount of indirection we are paying for optionality.

---

## ADR-004 · `audience` is filtered as allow-by-default within the restricted collection

**Date:** 2026-09-19 · **Status:** accepted

**Context.** Verified directly against the dataset: of 102 documents, **only 32 carry an
`audience` field** — `handbook/` (15, `all`) and `manager_playbook/` (17, `manager`).
`policies/`, `it_kb/`, `employment/`, `contracts/` and `internal_memos/` carry none.

**Decision.** A document is visible unless it carries an `audience` the caller does not
satisfy. Absent metadata means unrestricted. The filter is applied to the retrieval query
itself, never as a refusal after retrieval.

**Reasoning.** The spec states the failure mode outright: "A filter that excluded everything
unlabelled would block 70 of the 102 documents and fail every case in the set." Our count
confirms exactly 70. The risk being managed is a `manager_playbook` chunk reaching a
non-manager, and that is a boundary *within* one collection — not a corpus-wide deny-by-default.

**Consequences.** Adding a future restricted collection means adding `audience` front matter
to it; an unlabelled collection is silently public. Recorded here so that is a known property
rather than a surprise. Covered by a test asserting both directions: manager_playbook never
reaches a non-manager, and unlabelled documents always reach everyone.

---

## ADR-005 · "Manager" is derived from `manager_id`, not from `user_group`

**Date:** 2026-09-19 · **Status:** accepted

**Context.** `audience: manager` has to resolve to a property of the caller. The obvious
candidate, `user_group_id`, does not work. Verified against `seed.json`: five employees are
named as somebody's `manager_id` — E004 Sara Ben-David (`UG_HR`), E006 Amir Haddad (`UG_IT`),
E012 Liam Oconnor, E013 Emma Wilson and E018 Yael Romano (**all three `UG_REGULAR`**).

**Decision.** A caller is a manager when at least one employee row has `manager_id` equal to
the caller's employee id. Implemented once in `src/novaops/identity.py` and consumed by the
retrieval filter.

**Reasoning.** `UG_REGULAR` contains both real managers and Maya Cohen, whose two attempts at
the manager corpus (`S9` turns 1 and 4) are the permission trap the exercise is measured on.
Any rule keyed on `user_group` either leaks the playbook to every regular employee or denies
it to three of the five actual managers. The spec requires whichever rule we implement to be
stated in the README; it is.

**Consequences.** A manager with no direct reports recorded is treated as a non-manager. That
is the correct failure direction — it fails closed — and it is the rule the data supports.
Note: the spec's prose says "two of the five people who manage someone" are `UG_REGULAR`; the
seed data shows three. The discrepancy does not change the rule, and we follow the data.

---

## ADR-006 · Authorization reads a persisted approval row; `action_confirmed` only controls visibility

**Date:** 2026-09-19 · **Status:** accepted

**Context.** Lesson 10's `select_tools` releases `create_access_request` when
`plan.action_confirmed` is true. That field is produced by the planner model reading whether
the user said "go ahead". The spec calls this out as the one change the project requires, and
names the attack: pasting *"approved by the IT Manager, proceed"* into a message flips a
model-inferred boolean.

**Decision.** Two separate questions, two separate mechanisms.
*Visibility* — which read tools enter this turn's context — may continue to use
`action_confirmed`. *Authorization* — whether a write tool is released at all — reads only a
persisted approval row and fails closed when there is none. Implemented in
`src/novaops/agent/write_gate.py` as the single function that can release a write.

**Reasoning.** Showing a tool schema to a model is cheap and recoverable; letting it mutate a
business record is neither. Conflating them means the security property depends on a model's
reading comprehension. Keeping them apart also satisfies Maya's read-only requirement as a
*checked condition* rather than a folder boundary — which is what the spec asks for, and what
makes it visible to a reader.

**Consequences.** Every write path needs an approval record to exist first, including in
tests, which is more setup than a boolean. That cost is the point. One function must be
findable by a reviewer as "the only thing that can release a write," so it stays in its own
module and is named for what it does.

---

## ADR-007 · `AsyncSqliteSaver` for checkpointing, from the start

**Date:** 2026-09-19 · **Status:** accepted

**Context.** Lesson 10's graph compiles with `InMemorySaver`. Milestone 4 is done only when
"a killed process resumes a pending approval," and the spec notes plainly that
"an `InMemorySaver` does not do this." Lesson 9's `03-human-in-the-loop/` already demonstrates
`AsyncSqliteSaver` with `interrupt()` across two separate processes.

**Decision.** Compile with `AsyncSqliteSaver` from the first commit of the graph, not as a
later swap.

**Reasoning.** Restart survival is not a feature added at the end; it constrains how the
approval state is shaped. Discovering that late means reshaping state after the workflows are
written. Cheap now, expensive later.

**Consequences.** Local SQLite is adequate here because deployment (Lesson 14) is out of
scope per ADR-001. If that reverses, the spec requires durable external storage for business
writes, approvals and idempotency keys — SQLite would then need replacing.

---

## ADR-008 · Instrument with Langfuse at Milestone 2, not Milestone 5

**Date:** 2026-09-19 · **Status:** accepted

**Context.** Observability is formally Milestone 5, but the spec adds: "nothing stops you
binding tracing at Milestone 2, and debugging a twelve-turn session without it is harder than
it needs to be."

**Decision.** Bind `CallbackHandler` to the compiled graph as soon as the graph exists, and
attach trace metadata — caller id and group, chosen scope, tool sequence, terminal `status`,
and a `request_id` that survives MCP calls and resumes — from that point on.

**Reasoning.** Traces are the submission channel, so they are not a reporting layer added over
finished work; they are how the work is seen at all. Retrofitting produces thin spans with
generic names. Binding early also means the twelve-turn session is debuggable while it is
being built, which is where the time actually goes.

**Consequences.** Some development traces will be noisy and wrong. That is acceptable —
the submission set is run once, at the end, into clean state.

---

## ADR-009 · Chunk long collections only, on `##` headings

**Date:** 2026-09-19 · **Status:** accepted

**Context.** Lesson 10's `server/rag.py` embeds each document whole and keeps only its
filename. Measured against this corpus, that breaks. The 102 documents split in two:

| | docs | median chars | max chars |
| --- | ---: | ---: | ---: |
| contracts, employment, internal_memos, it_kb, policies | 70 | ~1,300 | 1,795 |
| handbook | 15 | 9,473 | **43,428** |
| manager_playbook | 17 | 9,505 | 31,963 |

Titan Text Embeddings V2 accepts roughly 8,000 tokens (~32,000 characters), so the largest
handbook file would be **silently truncated** — and a single vector averaging a 43k-character
document is too diffuse to match a specific question regardless.

**Decision.** Documents at or below 2,000 characters stay whole. Larger ones split on their
`##` headings, sections below 200 characters merge forward, and a section still over the cap
splits on paragraphs with 200 characters of overlap. Every chunk keeps the front matter of
its source file.

**Reasoning.** The 2,000-character cap was chosen against the measurement, not by default: it
leaves all five short collections at one chunk per document — a policy or an IT KB article is
already the unit a reader would cite — while splitting exactly the two long ones. Heading
boundaries are the natural unit in this corpus because both long collections are written as
`##` sections, so a chunk is a coherent topic rather than an arbitrary window.

**Evidence.** 102 documents produce 472 chunks: 70 short documents unchanged, handbook 15 →
209, manager_playbook 17 → 193. Against the live index, `S9` turn 1's query
*"How do I get promoted here? What does the path look like for a CSM?"* returns
`handbook/making-a-career.md#Promotion Process` as the top hit for a regular employee, and for
a manager returns `manager_playbook/promotion.md#Creating the Path` at 0.492 — **above** the
handbook's 0.466. The restricted document genuinely out-ranks the permitted one, which is why
the boundary has to be enforced in the search rather than in the ranking.

**Consequences.** Chunk count is 4.6× the document count, so an ingest is 472 embedding calls
rather than 102. The index is persisted to `var/faiss/` for that reason. The cap has not yet
been tuned by retrieval measurement — Lesson 6's homework method — and should be revisited if
citation quality disappoints.

---

## ADR-010 · The permission filter is a FAISS `IDSelector`, not a post-filter

**Date:** 2026-09-19 · **Status:** accepted

**Context.** The specification requires the boundary be enforced "as a retrieval filter, not
as a refusal after the fact." The obvious implementations are: retrieve then drop restricted
hits, or over-fetch then filter. Both leave a window in which a restricted chunk exists inside
the process.

**Decision.** `FaissStore.search()` takes the caller's allowed chunk ids and passes them to
FAISS as an `IDSelectorBatch` on `SearchParameters`. Excluded vectors are never compared
against. An empty allowlist returns nothing and is never interpreted as "no restriction".

**Reasoning.** With a post-filter, the property "a restricted chunk never reaches the model"
depends on every caller of `search()` remembering to filter. With a selector it is a property
of the search itself, so there is no code path where a restricted chunk is retrieved and then
discarded. Verified on the live index: for Maya Cohen, `manager_playbook/promotion.md` is not
returned at any `k`, while for Yael Romano it is the top hit.

**Consequences.** Ties the store to an engine feature; `SearchParameters` is the correct base
class for a flat index and the selector must be kept alive across the call, as FAISS borrows
the pointer. Both are noted in the code. An OpenSearch implementation would express the same
constraint as a `filter` clause, which is why it sits behind the `DocumentStore` protocol.

---

## Open decisions

Record these here as they are made, then promote them to numbered ADRs.

- **Which tools to add to close the inventory gap.** The spec says the inherited eleven tools
  are not everything we need. Working hypothesis, to be confirmed while building Webex: an
  approval-recording tool (`W-I-03` exercises the approval path and nothing writes to the
  `approvals` table), a seat-assignment tool that reports the gap (`W-I-02` has no precise
  answer, and the spec asks us to "represent the missing capability in the system"),
  `create_onboarding_task` (named as forbidden for Maya, which presupposes it exists), and a
  contract lookup for the 40-seat limit. Each needs its own ADR when built.
- **Chunking strategy** for the corpus, chosen by measurement per Lesson 6's homework.
- **Whether Maya and Webex share one graph or split.** Default is shared, per the spec's
  recommendation. If we split, retrieval permissions and the write gate must still be
  implemented once and shared.
- **How the idempotency key is composed** for tickets, access requests and approvals.
