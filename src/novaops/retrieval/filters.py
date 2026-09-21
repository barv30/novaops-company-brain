"""The permission boundary, expressed as a filter over the index.

The specification is unusually direct about this one: enforce it "as a **retrieval
filter**, not as a refusal after the fact. A restricted chunk that reaches the model has
already leaked, whatever the model then says about it."

So this module answers one question — *which chunk ids may this caller's query even be
compared against?* — and the store takes that answer as a constraint on the search
itself. A chunk the caller may not read is never a candidate, so it cannot be returned,
cannot be reranked into view, and cannot appear in context because a later filter had an
off-by-one. There is no second code path where a restricted chunk exists and is then
discarded.

See docs/DECISIONS.md ADR-004 (absent audience is unrestricted) and ADR-005 (manager is
derived from `manager_id`).
"""

from __future__ import annotations

from collections.abc import Sequence

from novaops.identity import Caller
from novaops.retrieval.documents import Chunk


def visible(chunk: Chunk, caller: Caller) -> bool:
    """Whether this caller may read this chunk.

    Delegates to `Caller.may_read`, so the rule lives in exactly one place and the
    retrieval layer cannot drift from whatever identity says.
    """

    return caller.may_read(chunk.audience)


def visible_ids(chunks: Sequence[Chunk], caller: Caller) -> list[int]:
    """The index positions this caller's query may be compared against.

    Returned as positions rather than as a filtered list because that is what the FAISS
    index needs in order to exclude the rest *during* the search. Filtering the results
    afterwards would produce the same list on a good day and a leak on a bad one.
    """

    return [i for i, chunk in enumerate(chunks) if visible(chunk, caller)]


def audit(chunks: Sequence[Chunk], caller: Caller) -> dict[str, int]:
    """How many chunks this caller can and cannot see, by audience value.

    Used by `scripts/ingest.py` to report the boundary after a build, and by the tests
    that assert it has not inverted. A silent permission filter is one nobody checks.
    """

    counts: dict[str, int] = {}
    for chunk in chunks:
        key = chunk.audience or "(unrestricted)"
        allowed = "visible" if visible(chunk, caller) else "hidden"
        counts[f"{key}:{allowed}"] = counts.get(f"{key}:{allowed}", 0) + 1
    return dict(sorted(counts.items()))
