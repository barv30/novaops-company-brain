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

`EVALUATION-INPUTS.yaml` supplies `caller: {employee_id, name, group}` per case, so an
application-supplied identity is the shape the evaluation set already assumes; the header
is simply where we put it.

**An unidentified caller is anonymous, not rejected.** See ADR-015. The boundary's job is
that restricted content never reaches someone who may not read it, and an anonymous
caller satisfies that by managing nobody. Refusing the request outright would also
withhold the 70 unrestricted documents and make an ordinary MCP client — one connecting
the way every lesson connects — see a server where every tool errors.
"""

from __future__ import annotations

import sqlite3

from mcp.server.fastmcp import Context

from novaops.db import repositories
from novaops.identity import ANONYMOUS, Caller, build_caller

CALLER_HEADER = "x-novaops-caller"


def caller_id_from(ctx: Context | None) -> str | None:
    """Read the caller id out of the transport, never out of the tool arguments.

    Returns None when no identity is present — over stdio, from a client that sends no
    header, or from a health check. The caller decides what that means; here it is
    simply absence, not an error.
    """

    if ctx is None:
        return None
    try:
        request = getattr(ctx.request_context, "request", None)
    except (ValueError, LookupError):
        # No request context at all — outside a request, or a non-HTTP transport.
        return None
    if request is None:
        return None
    return request.headers.get(CALLER_HEADER, "").strip() or None


def resolve_caller(ctx: Context | None, connection: sqlite3.Connection) -> Caller:
    """Build the `Caller` for this request, deriving manager status from the data.

    Manager status is derived here rather than trusted from the header, for the same
    reason the id is not read from tool arguments: a client that could assert
    `is_manager` would be a client that could grant itself the restricted corpus. The
    header says *who*; the database says what that means.

    An absent or unrecognised id resolves to `ANONYMOUS`, which manages nobody and so
    reaches nothing restricted.
    """

    employee_id = caller_id_from(ctx)
    if employee_id is None:
        return ANONYMOUS

    try:
        return build_caller(employee_id, repositories.all_employees(connection))
    except KeyError:
        # An id nobody recognises is a claim we cannot verify, so it buys nothing.
        return ANONYMOUS
