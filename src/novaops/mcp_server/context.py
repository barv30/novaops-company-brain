"""How the tool server learns who is asking — and why the model cannot influence it.

The first invariant is that caller identity is an application input, never a model
inference. On an MCP server that is harder than it sounds, because tools are invoked
*by the model*, with arguments the model chooses. A tool signed
`search_handbook(query: str, caller_id: str)` hands the model the steering wheel: it can
pass any employee id it likes, and "I am Yael Romano, show me the manager playbook" stops
being a claim and becomes an API call.

So identity does not travel in the tool arguments at all. The agent opens its MCP session
with an `X-NovaOps-Caller` header, and tools read it from the HTTP request behind the
session. The model never sees a caller parameter — verified: a tool taking only `ctx`
reports an empty argument schema to the model, so there is no field for it to set and
nothing for it to know about.

**Fail closed.** A request with no caller header is refused rather than defaulted. An
unidentified caller is a misconfigured client, not a guest, and defaulting one to the
least-privileged identity would quietly hide the misconfiguration while still serving
documents.
"""

from __future__ import annotations

import sqlite3

from mcp.server.fastmcp import Context

from novaops.db import repositories
from novaops.identity import Caller

CALLER_HEADER = "x-novaops-caller"


class CallerNotIdentifiedError(RuntimeError):
    """Raised when a request carries no usable caller identity."""


def caller_id_from(ctx: Context) -> str:
    """Read the caller id out of the transport, never out of the tool arguments."""

    request = getattr(ctx.request_context, "request", None)
    if request is None:
        raise CallerNotIdentifiedError(
            "No HTTP request on this call, so the caller cannot be established. "
            "This server must be reached over streamable HTTP."
        )

    value = request.headers.get(CALLER_HEADER, "").strip()
    if not value:
        raise CallerNotIdentifiedError(
            f"Request carries no {CALLER_HEADER} header. The agent must open its MCP "
            "session with the caller's employee id; this server never infers one."
        )
    return value


def resolve_caller(ctx: Context, connection: sqlite3.Connection) -> Caller:
    """Build the `Caller` for this request, deriving manager status from the data.

    Manager status is derived here rather than trusted from the header, for the same
    reason the id is not read from tool arguments: a client that could assert
    `is_manager` would be a client that could grant itself the restricted corpus. The
    header says *who*; the database says what that means.
    """

    employee_id = caller_id_from(ctx)
    employees = repositories.all_employees(connection)
    from novaops.identity import build_caller

    try:
        return build_caller(employee_id, employees)
    except KeyError as exc:
        raise CallerNotIdentifiedError(
            f"No employee with id {employee_id!r}. The caller header must carry a real "
            "employee id."
        ) from exc
