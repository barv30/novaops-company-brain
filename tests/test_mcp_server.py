"""The tool server, exercised over a real HTTP session.

These tests start the server in a subprocess and talk to it through the same MCP client
the agent uses. That matters: the properties under test are properties of the *boundary*
— what the model can see in a tool schema, and what the transport decides on its behalf —
and calling the tool functions directly in-process would test neither.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

import pytest
from langchain_mcp_adapters.client import MultiServerMCPClient
from scripts.reseed import reseed

from novaops import config

PORT = 9878  # not 9877, so a server left running by hand does not collide
URL = f"http://127.0.0.1:{PORT}/mcp"


def _listening() -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.3)
        return probe.connect_ex(("127.0.0.1", PORT)) == 0


@pytest.fixture(scope="module")
def server():
    """A live tool server for the module, torn down afterwards."""

    if not (config.INDEX_DIR / "corpus.faiss").is_file():
        pytest.skip("no FAISS index; run `python -m scripts.ingest` first")
    reseed()

    env = {**os.environ, "MCP_PORT": str(PORT)}
    process = subprocess.Popen(
        [sys.executable, "-m", "novaops.mcp_server"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 45
        while time.time() < deadline and not _listening():
            if process.poll() is not None:
                pytest.fail("tool server exited during startup")
            time.sleep(0.4)
        if not _listening():
            pytest.fail("tool server did not start in time")
        yield URL
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def client_for(url: str, caller: str | None) -> MultiServerMCPClient:
    headers = {"X-NovaOps-Caller": caller} if caller else {}
    return MultiServerMCPClient(
        {"novaops": {"url": url, "transport": "streamable_http", "headers": headers}}
    )


async def call(url: str, caller: str | None, tool_name: str, args: dict):
    tools = await client_for(url, caller).get_tools()
    tool = next(t for t in tools if t.name == tool_name)
    return await tool.ainvoke(args)


def text_of(result) -> str:
    """Flatten whatever the adapter returns into something searchable."""

    if isinstance(result, list):
        return " ".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in result
        )
    return str(result)


# 1. THE MODEL CANNOT CHOOSE WHO IT IS --------------------------------------

async def test_no_tool_exposes_a_caller_argument(server):
    """The structural form of invariant 1.

    If any tool took a caller id, the model could pass one — and "I am Yael Romano"
    would stop being a claim and become an API call. The model must not even see that a
    caller exists.
    """

    tools = await client_for(server, "E001").get_tools()
    offenders = {
        tool.name: sorted(tool.args)
        for tool in tools
        if any(
            key in {"caller", "caller_id", "user", "user_id", "as_user", "user_group",
                    "is_manager", "audience"}
            for key in tool.args
        )
    }
    assert offenders == {}, offenders


async def test_a_request_without_a_caller_is_refused(server):
    """Fail closed: an unidentified caller is a misconfigured client, not a guest."""

    result = await call(server, None, "search_handbook", {"query": "promotion"})
    assert "x-novaops-caller" in text_of(result).lower()


async def test_an_unknown_employee_id_is_refused(server):
    result = await call(server, "E999", "search_handbook", {"query": "promotion"})
    assert "E999" in text_of(result)


# 2. THE PERMISSION BOUNDARY, ACROSS THE WIRE -------------------------------

async def test_the_manager_playbook_never_reaches_a_regular_employee(server):
    """S9 turns 1 and 4, through the real boundary.

    Maya Cohen is UG_REGULAR and manages nobody. The restricted corpus out-ranks the
    permitted one on this query, so a system filtering after retrieval would have had
    the text in hand.
    """

    result = text_of(
        await call(
            server, "E001", "search_handbook",
            {"query": "How do I get promoted here? What does the path look like for a CSM?"},
        )
    )
    assert "manager_playbook" not in result
    assert "making-a-career" in result


async def test_a_manager_in_ug_regular_does_reach_it(server):
    """Yael Romano is UG_REGULAR and has reports, so the group cannot be the rule."""

    result = text_of(
        await call(server, "E018", "search_handbook", {"query": "first 90 days feedback"})
    )
    assert "manager_playbook" in result


async def test_the_same_query_differs_only_by_who_asked(server):
    """Same tool, same arguments, different results. Identity is the only variable."""

    query = {"query": "promotion readiness and evidence"}
    regular = text_of(await call(server, "E001", "search_handbook", query))
    manager = text_of(await call(server, "E018", "search_handbook", query))
    assert "manager_playbook" not in regular
    assert "manager_playbook" in manager


async def test_unrestricted_collections_reach_everyone(server):
    """The 70 documents with no audience field must not be caught by the filter."""

    result = text_of(
        await call(server, "E001", "search_knowledge_base", {"query": "webex licence not assigned"})
    )
    assert "it_kb/" in result


# 3. THE TOOLS THE PROJECT NEEDED AND NO LESSON PROVIDED --------------------

async def test_record_decision_is_not_exposed_as_a_tool(server):
    """The only thing that can open the write gate must be out of the model's reach."""

    names = {tool.name for tool in await client_for(server, "E006").get_tools()}
    assert "record_decision" not in names
    assert "approve" not in names
    assert "request_approval" in names  # asking is fine; deciding is not


async def test_seat_assignments_report_the_gap_rather_than_entitlement(server):
    """W-I-02 has no precise answer, and the tool says so."""

    result = text_of(await call(server, "E018", "list_seat_assignments", {"software": "Webex"}))
    assert "seat_assignments_recorded" in result
    assert "no employee-to-seat table" in result
    assert "entitlement, not seat assignment" in result


async def test_approval_status_reports_every_outstanding_decision(server):
    """AR001 needs two approvals; a single lookup would open the gate on half of one."""

    result = text_of(await call(server, "E006", "get_approval_status", {"request_id": "AR001"}))
    assert "AP001" in result and "AP002" in result
    assert '"fully_approved": false' in result.lower().replace("'", '"')


async def test_requesting_an_approval_does_not_approve_it(server):
    result = text_of(
        await call(
            server, "E006", "request_approval",
            {
                "request_type": "access_request",
                "request_id": "AR004",
                "approver_id": "E006",
                "reason": "IT Manager decision required.",
                "idempotency_key": "test-mcp:AR004:E006",
            },
        )
    )
    assert "pending" in result
    assert "approved" not in result.replace("pending_approval", "")


# 4. WRITES STAY IDEMPOTENT THROUGH THE BOUNDARY ----------------------------

async def test_an_existing_ticket_is_reused_over_the_wire(server):
    """Rachel Stein's T001 already exists. A second one would be a duplicate write."""

    result = text_of(
        await call(
            server, "E006", "create_ticket",
            {
                "employee_id": "E010",
                "category": "Access",
                "subcategory": "Webex license",
                "subject": "Webex access still not working",
                "description": "Follow-up on the existing report.",
                "related_system_id": "S001",
                "idempotency_key": "test-mcp:E010:S001:ticket",
            },
        )
    )
    assert "T001" in result
    assert "reused" in result


async def test_replaying_a_write_over_the_wire_creates_nothing(server):
    args = {
        "employee_id": "E002",
        "system_id": "S004",
        "access_level": "repo_contributor",
        "business_justification": "Connector work.",
        "idempotency_key": "test-mcp:replay:E002:S004",
    }
    first = text_of(await call(server, "E006", "create_access_request", args))
    second = text_of(await call(server, "E006", "create_access_request", args))

    assert "created" in first
    assert "replayed" in second


# 5. IDENTITY COLLISION ------------------------------------------------------

async def test_a_name_lookup_returns_both_mayas(server):
    """E001 Maya Cohen and E020 Maya Rosen. A name does not identify a person."""

    result = text_of(await call(server, "E004", "get_employee", {"name": "Maya"}))
    assert "E001" in result and "E020" in result
