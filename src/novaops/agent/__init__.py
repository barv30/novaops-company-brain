"""The agent: one graph, two scopes.

Phase 2. Planned modules:
  state.py       AgentState
  policy.py      classify prompt, LOADOUTS, context projections
  scopes.py      Maya and Webex scope definitions
  write_gate.py  THE only function that can release a write (ADR-006)
  graph.py       wiring; compiled with AsyncSqliteSaver (ADR-007)
"""
