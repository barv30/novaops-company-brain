"""The NovaOps tool server.

Every tool lives behind this boundary; the graph never calls a repository or the
retrieval store directly. Caller identity arrives in the transport, not in tool
arguments — see context.py.
"""

from novaops.mcp_server.server import main, mcp

__all__ = ["main", "mcp"]
