"""The idempotency ledger: what this system has already done, and what it produced.

The supplied `schema.sql` has no idempotency column anywhere, so replay-safety needs
somewhere to live. This module creates one table alongside the dataset's own — the
dataset is never modified, and `schema.sql` is never edited. See ADR-011.

**Why a ledger and not only natural-key matching.** The two catch different duplicates:

*Natural-key matching* — "is there already an open access request for E010 on S001?" —
catches records that existed before this system ever ran. `AR001` was seeded by someone
else, so nothing we wrote could know about it, and matching is the only way to find and
reuse it.

*The ledger* — "have I handled request-key `thread-1:E010:Webex:standard` before?" —
catches an exact replay of our own work. It is precise where matching is a judgement
call: two requests with slightly different justification text are the same request, and
no field comparison can be trusted to say so.

Neither alone is enough, so writes consult both.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from novaops import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS idempotency_keys (
    key            TEXT PRIMARY KEY,
    entity_type    TEXT NOT NULL,
    entity_id      TEXT NOT NULL,
    reused         INTEGER NOT NULL DEFAULT 0,
    conversation_id TEXT,
    created_at     TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class LedgerEntry:
    """A write this system has already performed, and the row it produced."""

    key: str
    entity_type: str
    entity_id: str
    reused: bool
    created_at: str


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Create the ledger if it is not there yet. Safe to call on every connection."""

    connection.executescript(SCHEMA)


def lookup(connection: sqlite3.Connection, key: str) -> LedgerEntry | None:
    """What this key produced last time, or None if it is new."""

    ensure_schema(connection)
    row = connection.execute(
        "SELECT key, entity_type, entity_id, reused, created_at "
        "FROM idempotency_keys WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    return LedgerEntry(
        key=row["key"],
        entity_type=row["entity_type"],
        entity_id=row["entity_id"],
        reused=bool(row["reused"]),
        created_at=row["created_at"],
    )


def record(
    connection: sqlite3.Connection,
    *,
    key: str,
    entity_type: str,
    entity_id: str,
    reused: bool = False,
    conversation_id: str | None = None,
) -> LedgerEntry:
    """Record that this key has been handled and which row it resolved to.

    `INSERT OR IGNORE` rather than `INSERT`: two concurrent attempts at the same key
    must leave one entry and neither an error nor a second row. The entry is then read
    back, so the return value is always what the ledger actually holds — never what this
    call hoped to write.
    """

    ensure_schema(connection)
    connection.execute(
        "INSERT OR IGNORE INTO idempotency_keys "
        "(key, entity_type, entity_id, reused, conversation_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            key,
            entity_type,
            entity_id,
            int(reused),
            conversation_id,
            config.AS_OF.isoformat(),
        ),
    )
    entry = lookup(connection, key)
    assert entry is not None  # noqa: S101 — just inserted or already present
    return entry
