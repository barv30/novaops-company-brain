"""Build the document index.

Milestone 1's first task: "Ingest `documents/` into a retrieval store, **preserving the
`audience` front matter** — you cannot filter on metadata you dropped at ingest."

The report this prints at the end is not decoration. It states, per collection, how many
chunks were produced and what audience they carry, then shows the permission boundary
from two sides: what a regular employee can reach and what a manager can reach. A
permission filter nobody looks at is a permission filter nobody notices is inverted.

Usage:
    python -m scripts.ingest              # build and save the index
    python -m scripts.ingest --dry-run    # chunk and report, without embedding anything
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from novaops import config
from novaops.identity import Caller
from novaops.retrieval.documents import load_corpus
from novaops.retrieval.filters import visible_ids
from novaops.retrieval.store import FaissStore


def _report(chunks) -> None:
    per_corpus: dict[str, Counter] = {}
    for chunk in chunks:
        per_corpus.setdefault(chunk.corpus, Counter())[chunk.audience or "(none)"] += 1

    docs = {chunk.source for chunk in chunks}
    print(f"\n{len(docs)} documents -> {len(chunks)} chunks\n")
    print(f"  {'collection':<20}{'chunks':>8}  audience")
    for corpus in sorted(per_corpus):
        counts = per_corpus[corpus]
        total = sum(counts.values())
        labels = ", ".join(f"{name} x{n}" for name, n in sorted(counts.items()))
        print(f"  {corpus:<20}{total:>8}  {labels}")

    # The boundary, from both sides. These two numbers must differ, and the difference
    # must be exactly the manager_playbook chunks.
    maya = Caller("E001", "UG_REGULAR", is_manager=False)      # the S9 permission trap
    yael = Caller("E018", "UG_REGULAR", is_manager=True)       # a manager in UG_REGULAR

    regular = len(visible_ids(chunks, maya))
    manager = len(visible_ids(chunks, yael))
    restricted = sum(1 for c in chunks if c.corpus == "manager_playbook")

    print("\n  permission boundary")
    print(f"    regular employee (E001 Maya Cohen)     {regular:>5} of {len(chunks)} chunks")
    print(f"    manager          (E018 Yael Romano)    {manager:>5} of {len(chunks)} chunks")
    print(f"    withheld from a regular employee       {manager - regular:>5} "
          f"(manager_playbook has {restricted})")

    if manager - regular != restricted:
        print("\n  WARNING: the withheld count does not equal manager_playbook. "
              "The filter is not doing what it claims.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="chunk and report without calling Bedrock or writing an index",
    )
    args = parser.parse_args(argv)

    chunks = load_corpus()
    _report(chunks)

    if args.dry_run:
        print("\ndry run — nothing embedded, nothing written")
        return 0

    print(f"\nembedding {len(chunks)} chunks with {config.BEDROCK_EMBED_MODEL_ID} ...")

    def progress(done: int, total: int) -> None:
        if done % 25 == 0 or done == total:
            print(f"  {done}/{total}")

    config.ensure_var_dir()
    store = FaissStore.build(chunks, progress=progress)
    target = store.save()
    print(f"\nindex written to {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
