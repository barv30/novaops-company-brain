"""Rebuild the business database from the dataset's schema and seed.

Reseeding is not a convenience here, it is part of how the evaluation set is run. The
specification is explicit: "Reseed the database before each independent case or session."
Several measured cases deliberately touch the same records, so running them back to back
on one database makes the first case's write suppress the second — and the resulting
trace shows a failure that belongs to the harness, not to the system.

Usage:
    python -m scripts.reseed              # rebuild var/novaops.db
    python -m scripts.reseed --checkpoints  # also drop the LangGraph checkpointer
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

from novaops import config


def reseed(*, drop_checkpoints: bool = False) -> None:
    """Recreate the business database from `schema.sql` and `seed.sql`.

    The file is deleted rather than truncated: dropping tables leaves sequences and
    stale indexes behind, and a partial reseed is harder to notice than a missing file.
    """

    config.ensure_var_dir()
    config.BUSINESS_DB.unlink(missing_ok=True)

    # `with sqlite3.connect(...)` manages the TRANSACTION, not the connection — it
    # commits and leaves the handle open. On Windows that open handle blocks the next
    # reseed from deleting the file, so the close is explicit.
    connection = sqlite3.connect(config.BUSINESS_DB)
    try:
        connection.executescript(config.SCHEMA_SQL.read_text(encoding="utf-8"))
        connection.executescript(config.SEED_SQL.read_text(encoding="utf-8"))
        connection.commit()
    finally:
        connection.close()

    if drop_checkpoints:
        # Only for a genuinely fresh start. Dropping this mid-session destroys a pending
        # approval, which is exactly what the restart test is meant to prove survives.
        config.CHECKPOINT_DB.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--checkpoints",
        action="store_true",
        help="also delete the LangGraph checkpointer database",
    )
    args = parser.parse_args(argv)

    reseed(drop_checkpoints=args.checkpoints)

    connection = sqlite3.connect(config.BUSINESS_DB)
    try:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        counts = {
            name: connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0]  # noqa: S608
            for (name,) in tables
        }
    finally:
        connection.close()

    print(f"reseeded {config.BUSINESS_DB}")
    for name, count in counts.items():
        print(f"  {name:<24} {count:>4}")
    if args.checkpoints:
        print("checkpointer dropped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
