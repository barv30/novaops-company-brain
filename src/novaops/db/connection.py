"""One place that opens the business database.

Everything that touches SQLite goes through here, so connection settings — row
factory, foreign keys, isolation — are decided once rather than per caller.

Foreign keys are enabled explicitly. SQLite leaves them off by default, which means a
write referencing a non-existent employee or system succeeds silently and the damage
surfaces later as a join that returns nothing. Here, it fails at the write.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from novaops import config


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open the business database with the settings every caller needs."""

    path = path or config.BUSINESS_DB
    if not path.exists():
        raise FileNotFoundError(
            f"No database at {path}. Run `python -m scripts.reseed` first."
        )
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def session(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """A connection that commits on success and rolls back on any exception.

    Writes in this system are keyed and checked before they are applied, so a partial
    write is the one failure mode that would leave the database saying something untrue
    — a ledger entry with no row, or a row with no ledger entry. This makes that
    impossible.
    """

    connection = connect(path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def next_id(connection: sqlite3.Connection, table: str, column: str, prefix: str) -> str:
    """Generate the next id in the dataset's own format, e.g. AR004 -> AR005.

    The seeded data uses short prefixed ids (`T001`, `AR001`, `AP001`). New records
    follow the same convention rather than switching to UUIDs, so a reviewer reading a
    trace sees ids that look like the ones already in the database.

    Reads the current maximum rather than counting rows: counting breaks the moment a
    row is deleted, and produces a duplicate id the next time.
    """

    rows = connection.execute(f"SELECT {column} FROM {table}").fetchall()  # noqa: S608
    numbers = [
        int(value[len(prefix):])
        for (value,) in ((row[column],) for row in rows)
        if isinstance(value, str) and value.startswith(prefix) and value[len(prefix):].isdigit()
    ]
    return f"{prefix}{max(numbers, default=0) + 1:03d}"
