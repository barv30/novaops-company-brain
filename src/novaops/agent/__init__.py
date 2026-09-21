"""The agent: one graph, two scopes.

`runner.answer()` is the entry point — the same function tests, scripts and any future
interface call. `write_gate.release_writes()` is the only thing that can release a write.
"""

from novaops.agent.runner import TurnResult, answer, load_tools
from novaops.agent.scopes import MAYA, WEBEX, get_scope

__all__ = ["MAYA", "WEBEX", "TurnResult", "answer", "get_scope", "load_tools"]
