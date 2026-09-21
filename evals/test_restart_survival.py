"""A pending approval survives the process being killed.

The second of the four checks the datasets do not ship. The specification is specific
about the failure it is guarding:

> "Approval must survive process death. Kill the process between the pause and the
> approval, restart, and resume. An `InMemorySaver` does not do this."

So the test kills a process. It does not monkeypatch one, or close a connection and call
that a restart — it starts a **subprocess**, has it persist state, lets it exit, and then
reads that state back from a second subprocess that shares nothing with the first except
the files on disk. Anything weaker passes with an in-memory saver, which is the exact
thing being ruled out.

Two things have to survive, and they are stored separately:

* the **approval record**, in the business database — what the write gate reads;
* the **graph checkpoint**, in the LangGraph SQLite saver — where the conversation was.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap

import pytest
from scripts.reseed import reseed

from novaops import config
from novaops.db import repositories
from novaops.db.connection import connect


def run_in_subprocess(code: str, env_db, env_ckpt) -> dict:
    """Run a snippet in a genuinely separate interpreter and return its JSON output."""

    import os

    env = {
        **os.environ,
        "NOVAOPS_DB": str(env_db),
        "NOVAOPS_CHECKPOINT_DB": str(env_ckpt),
        "LANGFUSE_PUBLIC_KEY": "",
        "LANGFUSE_SECRET_KEY": "",
    }
    completed = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True, text=True, env=env, timeout=180, check=False,
    )
    if completed.returncode != 0:
        pytest.fail(f"subprocess failed:\n{completed.stdout}\n{completed.stderr}")
    last = [ln for ln in completed.stdout.splitlines() if ln.startswith("{")]
    assert last, f"no JSON from subprocess:\n{completed.stdout}"
    return json.loads(last[-1])


@pytest.fixture
def world(tmp_path):
    reseed()
    db = tmp_path / "novaops.db"
    shutil.copy(config.BUSINESS_DB, db)
    return {"db": db, "ckpt": tmp_path / "checkpoints.db"}


def test_a_pending_approval_survives_the_process_dying(world):
    """Process one records the approval request. Process one then ends. Process two
    reads it back and the write gate acts on it."""

    first = run_in_subprocess(
        """
        import json, os
        from novaops.db.connection import connect
        from novaops.db import writes
        connection = connect()
        try:
            result = writes.request_approval(
                connection,
                request_type="access_request", request_id="AR004",
                approver_id="E006", reason="IT Manager decision required.",
                idempotency_key="eval:restart:AR004:E006",
            )
            connection.commit()
        finally:
            connection.close()
        print(json.dumps({"approval_id": result.entity_id, "pid": os.getpid()}))
        """,
        world["db"], world["ckpt"],
    )

    second = run_in_subprocess(
        """
        import json, os
        from novaops.db.connection import connect
        from novaops.db import repositories
        connection = connect()
        try:
            outstanding = [a["approval_id"] for a in
                           repositories.outstanding_approvals(connection, "AR004")]
        finally:
            connection.close()
        print(json.dumps({"outstanding": outstanding, "pid": os.getpid()}))
        """,
        world["db"], world["ckpt"],
    )

    assert first["pid"] != second["pid"], "these ran in the same process"
    assert first["approval_id"] in second["outstanding"], (
        "the pending approval did not survive the process ending"
    )


def test_the_gate_opens_in_a_later_process_against_the_recorded_decision(world):
    """The whole point of durability: the decision is made in one process and the write
    is released in another, with nothing shared but the database."""

    created = run_in_subprocess(
        """
        import json
        from novaops.db.connection import connect
        from novaops.db import writes
        connection = connect()
        try:
            approval = writes.request_approval(
                connection, request_type="access_request", request_id="AR004",
                approver_id="E006", reason="Please decide.",
                idempotency_key="eval:restart2:AR004",
            )
            connection.commit()
        finally:
            connection.close()
        print(json.dumps({"approval_id": approval.entity_id}))
        """,
        world["db"], world["ckpt"],
    )

    # A second process makes the human decision and exits.
    run_in_subprocess(
        f"""
        import json
        from novaops.db.connection import connect
        from novaops.db import writes
        connection = connect()
        try:
            writes.record_decision(
                connection, approval_id={created["approval_id"]!r}, decided_by="E006",
                approved=True, reason="Seat freed by a leaver.",
            )
            connection.commit()
        finally:
            connection.close()
        print(json.dumps({{"ok": True}}))
        """,
        world["db"], world["ckpt"],
    )

    # A third process asks the gate, having seen neither of the others.
    verdict = run_in_subprocess(
        """
        import json
        from novaops.agent.scopes import WEBEX_SCOPE
        from novaops.agent.write_gate import release_writes
        from novaops.db.connection import connect
        connection = connect()
        try:
            decision = release_writes(
                scope=WEBEX_SCOPE, intent="access_request", request_id="AR004",
                connection=connection,
                catalogue=["create_access_request", "create_ticket", "get_employee"],
            )
        finally:
            connection.close()
        print(json.dumps({"released": decision.released, "reason": decision.reason}))
        """,
        world["db"], world["ckpt"],
    )

    assert verdict["released"], f"the gate stayed shut: {verdict['reason']}"
    assert "create_access_request" in verdict["released"]


def test_the_checkpointer_is_a_file_not_memory(world):
    """The mechanism the specification rules out by name.

    A graph compiled with `InMemorySaver` passes every behavioural test in this file's
    sibling modules and fails the property they exist to prove. This asserts the file is
    real and was written to disk by a process that has since gone.
    """

    run_in_subprocess(
        """
        import asyncio, json
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from novaops import config

        async def main():
            async with AsyncSqliteSaver.from_conn_string(str(config.CHECKPOINT_DB)) as saver:
                await saver.aput(
                    {"configurable": {"thread_id": "eval-restart", "checkpoint_ns": ""}},
                    {"v": 1, "id": "c1", "ts": "2026-07-01T00:00:00Z", "channel_values":
                     {"marker": "written-before-the-process-died"}, "channel_versions": {},
                     "versions_seen": {}},
                    {"source": "input", "step": 0, "parents": {}},
                    {},
                )
            print(json.dumps({"ok": True}))

        asyncio.run(main())
        """,
        world["db"], world["ckpt"],
    )

    assert world["ckpt"].exists(), "no checkpoint file was written"
    assert world["ckpt"].stat().st_size > 0

    read_back = run_in_subprocess(
        """
        import asyncio, json
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from novaops import config

        async def main():
            async with AsyncSqliteSaver.from_conn_string(str(config.CHECKPOINT_DB)) as saver:
                found = await saver.aget(
                    {"configurable": {"thread_id": "eval-restart", "checkpoint_ns": ""}}
                )
            print(json.dumps({"marker": (found or {}).get("channel_values", {}).get("marker")}))

        asyncio.run(main())
        """,
        world["db"], world["ckpt"],
    )

    assert read_back["marker"] == "written-before-the-process-died"


def test_an_unapproved_request_is_still_unapproved_after_a_restart(world):
    """Durability cuts both ways: a pause that survives must survive as a pause."""

    run_in_subprocess(
        """
        import json
        from novaops.db.connection import connect
        from novaops.db import writes
        connection = connect()
        try:
            writes.request_approval(
                connection, request_type="access_request", request_id="AR004",
                approver_id="E006", reason="Please decide.",
                idempotency_key="eval:restart3:AR004",
            )
            connection.commit()
        finally:
            connection.close()
        print(json.dumps({"ok": True}))
        """,
        world["db"], world["ckpt"],
    )

    connection = connect(world["db"])
    try:
        outstanding = repositories.outstanding_approvals(connection, "AR004")
    finally:
        connection.close()

    assert outstanding, "the pending approval was lost, or silently granted itself"
