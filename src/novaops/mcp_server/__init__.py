"""The MCP tool server.

Phase 1. Every tool lives behind this boundary; the graph never calls a tool function
directly. Ported from ../lesson-10-context-engineering/code/server/ and repointed at
the NovaOps dataset, then extended to close the tool-inventory gap (see the open
decisions in docs/DECISIONS.md).
"""
