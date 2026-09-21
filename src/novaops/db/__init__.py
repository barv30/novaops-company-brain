"""Business data: connections, reads, keyed writes, and the idempotency ledger.

Reads live in `repositories`, writes in `writes`. Every write reuses before it creates
and is keyed so a replay is a no-op — see ADR-011.
"""

from novaops.db.connection import connect, next_id, session

__all__ = ["connect", "next_id", "session"]
