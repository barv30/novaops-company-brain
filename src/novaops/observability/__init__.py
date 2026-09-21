"""Langfuse instrumentation.

Phase 2, per ADR-008. Span names must let a reader follow classify -> scope -> tools ->
answer. Trace metadata carries caller id and group, chosen scope, tool sequence,
terminal status, and a request id that survives MCP calls and resumes.
"""
