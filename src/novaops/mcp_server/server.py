"""The NovaOps tool server.

Lesson 10's `server/server.py`, repointed at the NovaOps dataset and extended. The
specification requires tools to be "described capabilities behind a server boundary, not
functions stitched into the graph", and says that boundary is assessed — so the graph
imports nothing from `novaops.db` or `novaops.retrieval`; it reaches them only through
here.

**What is new relative to the eleven inherited tools.** §6.3 says the inventory is not
everything the project needs. Four capabilities are added, each argued in ADR-012:

  `request_approval`      nothing wrote to the `approvals` table, yet the write gate
                          must read a *recorded* approval before releasing anything
  `seat_assignment_gap`   `W-I-02` has no precise answer; this reports the gap rather
                          than letting the model substitute role entitlement for access
  `get_contract`          "42 of 40" without the agreement it comes from is an
                          assertion, not evidence
  `list_direct_reports`   who reports to whom — also how manager status is derived

**What is deliberately NOT a tool.** `record_decision` moves an approval to `approved`
and so is the only thing that can ever open the write gate. Exposing it here would put
that switch inside the model's reach, which is precisely the attack ADR-006 exists to
close. It stays in `novaops.db.writes`, callable only by application code holding a
human's decision.

**Permission filtering.** Every retrieval tool resolves the caller from the transport
(see `context.py`) and passes that caller's allowed chunk ids into the search. A
restricted chunk is never a candidate — it is not retrieved and then withheld.
"""

from __future__ import annotations

from contextlib import closing
from functools import lru_cache
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from novaops import config
from novaops.db import repositories, writes
from novaops.db.connection import connect
from novaops.identity import Caller
from novaops.mcp_server.context import request_id_from, resolve_caller
from novaops.retrieval.filters import visible_ids
from novaops.retrieval.store import FaissStore, SearchHit
from novaops.workflows.access import diagnose_access as diagnose_access_from_records
from novaops.workflows.access import team_system_access as team_system_access_from_records
from novaops.workflows.onboarding import build_checklist

mcp = FastMCP("novaops-company-brain", host=config.MCP_HOST, port=config.MCP_PORT)


@lru_cache(maxsize=1)
def _store() -> FaissStore:
    """The document index, loaded once per process."""

    return FaissStore.load()


def _db():
    """A connection that is actually closed when the block ends.

    `with sqlite3.connect(...)` manages the TRANSACTION, not the connection: it commits
    and leaves the handle open. A server doing that leaks one handle per tool call, and
    on Windows those handles block `scripts/reseed.py` from deleting the database — so
    the symptom is a replay that cannot reseed rather than an error here. ADR-019.
    """

    return closing(connect())


def _search(
    ctx: Context | None,
    query: str,
    corpora: list[str] | None,
    k: int | None = None,
) -> list[dict[str, Any]]:
    """One retrieval path for every search tool, so the filter cannot be forgotten.

    Where a collection was excluded because this caller may not read it, the result says
    so. That is not a leak, and the distinction matters: the *contents* of
    `manager_playbook/` are restricted, the *fact that it exists* is not. Without the
    notice the model cannot tell "there is nothing on this" from "there is something and
    it is not yours", so it goes quiet and answers from somewhere else — measured on
    `S9` turn 4, where Maya names the manager's guide directly and is owed a plain
    answer that it is not available to her.
    """

    with _db() as connection:
        caller = resolve_caller(ctx, connection)

    store = _store()
    allowed = visible_ids(store.chunks, caller)
    hits: list[SearchHit] = store.search(
        query, k=k or config.RETRIEVAL_TOP_K, allowed_ids=allowed, corpora=corpora
    )

    results = [
        {
            "source": hit.chunk.source,
            "heading": hit.chunk.heading,
            "score": hit.score,
            "last_updated": hit.chunk.last_updated,
            "text": hit.chunk.text,
        }
        for hit in hits
    ]

    for collection in _excluded_collections(store, caller, corpora):
        results.append(
            {
                "source": f"(withheld: {collection})",
                "heading": None,
                "score": None,
                "last_updated": None,
                # Deliberately terse and non-imperative. A longer, instruction-shaped
                # notice hijacked turns where the caller had not asked for restricted
                # material at all: it fires on every search of a corpus with a
                # restricted collection in it, so most of the time the right response
                # is to ignore it and answer the question that was asked.
                "text": (
                    f"Note: the '{collection}' collection is restricted and was not "
                    f"searched. Mention this only if the user specifically asked for "
                    f"that material, in which case say it is not available to them."
                ),
            }
        )
    return results


def _excluded_collections(
    store: FaissStore, caller: Caller, corpora: list[str] | None
) -> list[str]:
    """Collections this search would have covered but the caller may not read.

    Reports the collection name only — never a document path, a heading or a line of
    text — so the notice cannot become the leak it exists to prevent.
    """

    wanted = set(corpora) if corpora else None
    withheld: set[str] = set()
    for chunk in store.chunks:
        if wanted is not None and chunk.corpus not in wanted:
            continue
        if not caller.may_read(chunk.audience):
            withheld.add(chunk.corpus)
    return sorted(withheld)


def _employee_view(row: dict) -> dict:
    """An employee row as the model should see it.

    `systems_allowed_by_role` is renamed, not removed. It is a real fact — what the role
    permits — and it is exactly the column the agent read as "has a Webex seat" on the
    first run of `W-I-02`, reporting access for two people whose access is blocked. A
    name that says what it is costs nothing and removes the misreading at the source.
    """

    view = dict(row)
    if "systems_allowed_by_role" in view:
        view["systems_entitled_by_role_NOT_access"] = view.pop("systems_allowed_by_role")
    return view


def _caller(ctx: Context | None) -> Caller:
    """The caller behind this request. Anonymous when the session carries no identity."""

    with _db() as connection:
        return resolve_caller(ctx, connection)


# 1. DOCUMENTS ---------------------------------------------------------------

@mcp.tool()
def search_knowledge_base(query: str, ctx: Context | None = None) -> list[dict]:
    """Search the NovaOps IT knowledge base for how-to and troubleshooting articles.

    Use for provisioning, login, VPN, MFA, password and licence questions — how a
    system is meant to work and what to do when it does not.

    Args:
        query: A natural-language description of the problem or question.
    """
    return _search(ctx, query, ["it_kb"])


@mcp.tool()
def search_policies(query: str, ctx: Context | None = None) -> list[dict]:
    """Search NovaOps policies and internal memos for what the rules are.

    Searches both together on purpose: a memo may have changed a threshold a policy
    still states, and newer or more specific guidance overrides older or more general
    guidance. Compare the `last_updated` values before concluding what governs.

    Args:
        query: What rule, threshold or approval requirement you need.
    """
    return _search(ctx, query, ["policies", "internal_memos"])


@mcp.tool()
def search_hr_documents(query: str, ctx: Context | None = None) -> list[dict]:
    """Search employment documents — offer letters, onboarding summaries, addenda.

    Use for what was agreed about a specific person: the systems and equipment an offer
    letter entitles them to, a remote-work addendum, a contractor's statement of work.

    Args:
        query: A natural-language description of what you need to find.
    """
    return _search(ctx, query, ["employment", "internal_memos"])


@mcp.tool()
def search_handbook(query: str, ctx: Context | None = None) -> list[dict]:
    """Search the employee handbook and, for managers only, the manager playbook.

    Which of those two this searches is decided by who is asking, not by this argument.
    The manager playbook is `audience: manager` and is excluded from the search itself
    for anyone who does not manage someone — so a non-manager's results simply do not
    contain it.

    Args:
        query: A career, benefits, ways-of-working or people-management question.
    """
    return _search(ctx, query, ["handbook", "manager_playbook"])


@mcp.tool()
def search_contracts(query: str, ctx: Context | None = None) -> list[dict]:
    """Search vendor contracts for seat limits, renewal terms and obligations.

    Use when a limit or a renewal date needs its source. A seat limit reported without
    the agreement that sets it is an assertion rather than evidence.

    Args:
        query: The vendor, system or contractual term you need.
    """
    return _search(ctx, query, ["contracts"])


@mcp.tool()
def list_policies(ctx: Context | None = None) -> list[str]:
    """List the names of all NovaOps policies. Call before fetching one by name."""

    _caller(ctx)  # refuse unidentified callers consistently, even for a listing
    return sorted(
        path.stem for path in (config.DOCUMENTS_DIR / "policies").glob("*.md")
    )


@mcp.tool()
def get_policy(name: str, ctx: Context | None = None) -> str:
    """Return the full text of one NovaOps policy.

    Args:
        name: A policy name as returned by list_policies, e.g. 'equipment_policy'.
    """
    _caller(ctx)
    path = config.DOCUMENTS_DIR / "policies" / f"{name}.md"
    if not path.is_file():
        raise ValueError(f"No policy named {name!r}. Call list_policies for valid names.")
    return path.read_text(encoding="utf-8")


# 2. PEOPLE ------------------------------------------------------------------

@mcp.tool()
def get_employee(
    employee_id: str = "", name: str = "", ctx: Context | None = None
) -> list[dict]:
    """Look an employee up by id, or find candidates by name.

    Always returns a list, even for a name that looks unambiguous. The directory
    contains two people called Maya — E001 Maya Cohen and E020 Maya Rosen — so a name
    does not identify a person. If this returns more than one row, ask which is meant
    rather than choosing; reporting the wrong person's onboarding is a silent error.

    Args:
        employee_id: An exact id such as 'E010'. Preferred when known.
        name: A full or partial name. May return several people.
    """
    with _db() as connection:
        resolve_caller(ctx, connection)
        if employee_id:
            row = repositories.get_employee(connection, employee_id)
            return [_employee_view(row)] if row else []
        if name:
            return [_employee_view(r) for r in repositories.find_employees(connection, name)]
        return [_employee_view(r) for r in repositories.all_employees(connection)]


@mcp.tool()
def list_direct_reports(manager_id: str, ctx: Context | None = None) -> list[dict]:
    """List the employees who report to a given manager.

    Args:
        manager_id: The manager's employee id, e.g. 'E018'.
    """
    with _db() as connection:
        resolve_caller(ctx, connection)
        return [_employee_view(r) for r in repositories.direct_reports(connection, manager_id)]


# 3. SYSTEMS, SEATS, CONTRACTS -----------------------------------------------

@mcp.tool()
def check_software_subscription(software: str, ctx: Context | None = None) -> dict | None:
    """Seats, limit and renewal for one system, with the contract behind them.

    Reports `active_seats` against `seat_limit` and whether the subscription is at or
    over its contractual limit. Being over the limit is an organizational blocker, not a
    problem with any individual's account.

    Args:
        software: A system name, e.g. 'Webex'.
    """
    with _db() as connection:
        resolve_caller(ctx, connection)
        return repositories.seat_position(connection, software)


@mcp.tool()
def get_contract(contract_id: str, ctx: Context | None = None) -> dict | None:
    """Return one contract record — term, renewal notice, cost, owner department.

    Args:
        contract_id: A contract id, e.g. 'C001'.
    """
    with _db() as connection:
        resolve_caller(ctx, connection)
        return repositories.get_contract(connection, contract_id)


@mcp.tool()
def list_seat_assignments(software: str, ctx: Context | None = None) -> dict:
    """Who holds a seat on a system — and why that is not recorded.

    Call this before answering any question of the form "which of my team has access to
    X". This database records seat *counts* only; there is no employee-to-seat table, so
    which individuals hold a seat cannot be determined from it.

    The result deliberately includes `entitled_by_role` alongside a warning: role
    entitlement is what someone is permitted to have, not what they hold. Reporting it
    as access states that blocked employees have access. Use the access requests and
    onboarding tasks in the result to say what is actually known.

    Args:
        software: A system name, e.g. 'Webex'.
    """
    with _db() as connection:
        resolve_caller(ctx, connection)
        return repositories.seat_assignment_gap(connection, software)


# 4. TICKETS, REQUESTS, APPROVALS --------------------------------------------

@mcp.tool()
def diagnose_access(employee_id: str, system: str, ctx: Context | None = None) -> dict:
    """Why an employee can or cannot use a system, assembled from the records.

    Call this FIRST for any "I cannot use X" or "I need access to X" request. It answers
    the whole question in one call and keeps three things apart that prose blurs:
    what the records SHOW, what has actually been DONE, and what should happen NEXT.

    It reads only. It never files anything, and it never reports access as granted.
    Where a ticket or access request already exists it reports them as *referenced*,
    which is what reusing a record looks like — do not then open a second one.

    Returns `outcome` (blocked / refused / awaiting_approval / recommended),
    `blocking_reason`, `observed_facts` with citations, `actions_taken` with the record
    ids that prove them, and `recommended_next_steps` with their owners.

    Args:
        employee_id: The employee's id, e.g. 'E010'. Not a name.
        system: The system name, e.g. 'Webex'.
    """
    with _db() as connection:
        resolve_caller(ctx, connection)
        decision = diagnose_access_from_records(connection, employee_id, system)
    return decision.model_dump(mode="json")


@mcp.tool()
def team_system_access(system: str, ctx: Context | None = None) -> dict:
    """What can be said about YOUR team and one system — call this for any question like
    "which of my team has a Webex seat / licence / access".

    The team is the caller's direct reports, taken from the caller's identity — there is
    no argument for whose team, so it cannot be anyone else's.

    Read the result carefully: `seat_assignments_recorded` is False because no record says
    which person holds a seat. Report that first. Then report, per person, what the
    records DO show — a blocked access request, a blocked onboarding task, or no
    entitlement. `entitled_by_role` is NOT a seat and must never be reported as one.

    Args:
        system: The system name, e.g. 'Webex'.
    """
    with _db() as connection:
        caller = resolve_caller(ctx, connection)
        if not caller.is_manager:
            return {
                "system": system,
                "team": [],
                "note": f"{caller.employee_id} manages nobody, so there is no team to report.",
            }
        return team_system_access_from_records(connection, caller.employee_id, system)


@mcp.tool()
def list_employee_tickets(
    employee_id: str, status: str = "", ctx: Context | None = None
) -> list[dict]:
    """List an employee's IT tickets, optionally filtered by status.

    Check this before opening a ticket: an open ticket for the same problem should be
    referenced, not duplicated.

    Args:
        employee_id: The employee's id, e.g. 'E010'.
        status: Optional exact status, e.g. 'open'.
    """
    with _db() as connection:
        resolve_caller(ctx, connection)
        return repositories.list_employee_tickets(connection, employee_id, status or None)


@mcp.tool()
def list_access_requests(employee_id: str, ctx: Context | None = None) -> list[dict]:
    """List an employee's access requests, with status and decision reason.

    An existing request often already carries the answer: a `blocked` request records
    why it is blocked.

    Args:
        employee_id: The employee's id, e.g. 'E010'.
    """
    with _db() as connection:
        resolve_caller(ctx, connection)
        return repositories.list_access_requests(connection, employee_id)


@mcp.tool()
def get_approval_status(request_id: str, ctx: Context | None = None) -> dict:
    """Every approval attached to a request, and which are still outstanding.

    A request may require more than one decision — AR001 needs both IT Manager and
    Finance — so "is it approved" is not a single lookup. `fully_approved` is true only
    when nothing remains outstanding.

    Args:
        request_id: The request the approvals hang off, e.g. 'AR001'.
    """
    with _db() as connection:
        resolve_caller(ctx, connection)
        approvals = repositories.approvals_for(connection, request_id)
        outstanding = repositories.outstanding_approvals(connection, request_id)
    return {
        "request_id": request_id,
        "approvals": approvals,
        "outstanding": outstanding,
        "fully_approved": bool(approvals) and not outstanding,
    }


# 5. ONBOARDING AND ASSETS ---------------------------------------------------

@mcp.tool()
def list_onboarding_tasks(employee_id: str, ctx: Context | None = None) -> list[dict]:
    """List a joiner's onboarding tasks, with owner group, status and due date.

    Args:
        employee_id: The joiner's id, e.g. 'E001'.
    """
    with _db() as connection:
        resolve_caller(ctx, connection)
        return repositories.list_onboarding_tasks(connection, employee_id)


@mcp.tool()
def get_onboarding_checklist(employee_id: str, ctx: Context | None = None) -> dict:
    """The complete, evidence-backed onboarding checklist for one joiner.

    Call this for any question about what a joiner needs, what is outstanding, what is
    blocked, or for a status summary about them. It answers all of those from one call,
    and it is assembled from the records rather than composed by a model — so every item
    carries the id of the row it came from, and a blocked item names its actual cause
    rather than repeating the word "blocked".

    Prefer it over assembling an answer yourself from `list_onboarding_tasks` plus
    `check_asset_inventory` plus `check_software_subscription`: those give you three
    partial views that you then have to reconcile, and reconciling them is where a
    blocked item quietly becomes "in progress".

    Returns items with `status`, `blocked_reason`, `owner_group`, `due_date` and
    `citations`, plus `constraints_applied`, `unresolved`, and the joiner's start date.

    Args:
        employee_id: The joiner's id, e.g. 'E001'. Not a name — two employees are
            called Maya, so a name does not identify a person.
    """
    with _db() as connection:
        resolve_caller(ctx, connection)
        checklist = build_checklist(connection, employee_id)
    return checklist.model_dump(mode="json")


@mcp.tool()
def check_asset_inventory(
    asset_type: str = "", location: str = "", ctx: Context | None = None
) -> list[dict]:
    """List hardware assets, optionally narrowed by type and location.

    Args:
        asset_type: e.g. 'laptop', 'monitor'.
        location: e.g. 'Israel', 'Remote'.
    """
    with _db() as connection:
        resolve_caller(ctx, connection)
        return repositories.check_asset_inventory(connection, asset_type, location)


# 6. WRITES ------------------------------------------------------------------
#
# Every write reuses before it creates and is keyed, so a replay is a no-op. Each
# returns `disposition` — created, reused or replayed — because finding a record is not
# the same as filing one, and a report that blurs them claims credit for work not done.

@mcp.tool()
def create_ticket(
    employee_id: str,
    category: str,
    subcategory: str,
    subject: str,
    description: str,
    idempotency_key: str,
    related_system_id: str = "",
    priority: str = "medium",
    ctx: Context | None = None,
) -> dict:
    """Open an IT ticket, reusing an open ticket for the same employee and system.

    If a live ticket already exists for this employee and system, that ticket is
    returned with `disposition: reused` and nothing is written.

    Args:
        employee_id: Who the ticket is for.
        category: e.g. 'Access', 'Hardware'.
        subcategory: e.g. 'Webex license'.
        subject: One-line summary.
        description: What is wrong and what has been established.
        idempotency_key: A stable key for this request, including the conversation id.
            The same key must never produce two rows.
        related_system_id: The system id this concerns, e.g. 'S001'.
        priority: 'low', 'medium' or 'high'.
    """
    actor = _caller(ctx).employee_id if ctx is not None else None
    with closing(connect()) as connection:
        result = writes.create_ticket(
            connection,
            employee_id=employee_id,
            category=category,
            subcategory=subcategory,
            subject=subject,
            description=description,
            related_system_id=related_system_id or None,
            priority=priority,
            idempotency_key=idempotency_key,
            actor=actor,
            agent_request_id=request_id_from(ctx),
        )
        connection.commit()
    return {"disposition": result.disposition, "ticket_id": result.entity_id, **result.row}


@mcp.tool()
def create_access_request(
    employee_id: str,
    system_id: str,
    access_level: str,
    business_justification: str,
    idempotency_key: str,
    approver_id: str = "",
    ctx: Context | None = None,
) -> dict:
    """File an access request, reusing a live request for the same employee and system.

    Filing a request is not granting access. The request is created as
    `pending_approval` and a human still has to decide.

    Args:
        employee_id: Who the access is for.
        system_id: The system id, e.g. 'S001'.
        access_level: e.g. 'standard_user'.
        business_justification: Why the access is needed, in the requester's terms.
        idempotency_key: A stable key including the conversation id.
        approver_id: Who should decide, when known.
    """
    actor = _caller(ctx).employee_id if ctx is not None else None
    with closing(connect()) as connection:
        result = writes.create_access_request(
            connection,
            employee_id=employee_id,
            system_id=system_id,
            access_level=access_level,
            business_justification=business_justification,
            approver_id=approver_id or None,
            idempotency_key=idempotency_key,
            actor=actor,
            agent_request_id=request_id_from(ctx),
        )
        connection.commit()
    return {"disposition": result.disposition, "request_id": result.entity_id, **result.row}


@mcp.tool()
def request_approval(
    request_type: str,
    request_id: str,
    approver_id: str,
    reason: str,
    idempotency_key: str,
    ctx: Context | None = None,
) -> dict:
    """Ask a named person to decide on a request, and persist the asking.

    This records a *request for a decision*, always as `pending`. It never approves
    anything, and there is no tool on this server that does: an approval becomes
    `approved` only through application code holding a human's response. Calling this
    does not unblock a write.

    Args:
        request_type: 'access_request' or 'subscription_expansion'.
        request_id: The request needing a decision, e.g. 'AR001'.
        approver_id: Who must decide, e.g. 'E006' for the IT Manager.
        reason: Why their decision is required.
        idempotency_key: A stable key including the conversation id.
    """
    actor = _caller(ctx).employee_id if ctx is not None else None
    with closing(connect()) as connection:
        result = writes.request_approval(
            connection,
            request_type=request_type,
            request_id=request_id,
            approver_id=approver_id,
            reason=reason,
            idempotency_key=idempotency_key,
            actor=actor,
            agent_request_id=request_id_from(ctx),
        )
        connection.commit()
    return {"disposition": result.disposition, "approval_id": result.entity_id, **result.row}


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
