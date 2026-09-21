"""The agent: one graph, two scopes.

`runner.answer()` is the entry point — the same function tests, scripts and any future
interface call. `write_gate.release_writes()` is the only thing that can release a write,
and `runner.approve()` is the only thing that can satisfy it.
"""

from novaops.agent.runner import (
    ThreadNotFreshError,
    TurnResult,
    answer,
    approve,
    load_tools,
    new_thread_id,
)
from novaops.agent.scopes import MAYA, WEBEX, get_scope

__all__ = [
    "MAYA",
    "WEBEX",
    "ThreadNotFreshError",
    "TurnResult",
    "answer",
    "approve",
    "get_scope",
    "load_tools",
    "new_thread_id",
]
