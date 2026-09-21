"""The document index: a protocol, and a FAISS implementation of it.

Two things here are deliberate departures from Lesson 10's `server/rag.py`.

**The index is persisted.** Lesson 10 rebuilds it in memory on first search, behind an
`lru_cache`. At 102 documents that is roughly 150 embedding calls every time the server
restarts — slow, billable, and a different index each time you change chunking without
noticing. We build once to disk and load thereafter.

**Filtering happens inside the search.** `search()` takes the ids the caller is allowed
to see and hands them to FAISS as an `IDSelector`, so restricted vectors are not
candidates at all. This is the difference between a permission boundary and a filter
applied to results — see ADR-004 and `filters.py`.

The `DocumentStore` protocol exists so that swapping FAISS for OpenSearch is one
implementation, not a refactor (ADR-003).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from novaops import config
from novaops.llm.client import EMBED_DIM, embed_text
from novaops.retrieval.documents import Chunk


class SearchHit(BaseModel):
    """One retrieved chunk and how well it matched."""

    chunk: Chunk
    score: float


class DocumentStore(Protocol):
    """What the rest of the system needs from a retrieval store."""

    def search(
        self,
        query: str,
        *,
        k: int = ...,
        allowed_ids: Sequence[int] | None = ...,
        corpora: Sequence[str] | None = ...,
    ) -> list[SearchHit]:
        """Return the best `k` chunks for `query`, restricted to `allowed_ids`."""
        ...

    @property
    def chunks(self) -> list[Chunk]:
        """Every chunk in the store, in index order."""
        ...


class FaissStore:
    """A flat inner-product FAISS index over the corpus, with its metadata alongside.

    Flat means exact search. At a few hundred chunks an approximate index would trade
    accuracy for a speed-up nobody can measure, so we take the accuracy.
    """

    def __init__(self, index, chunk_list: list[Chunk]) -> None:
        self._index = index
        self._chunks = chunk_list

    # --- construction -------------------------------------------------------

    @classmethod
    def build(cls, chunk_list: list[Chunk], *, progress=None) -> FaissStore:
        """Embed every chunk and build the index. One Bedrock call per chunk."""

        import faiss
        import numpy as np

        vectors = []
        for position, chunk in enumerate(chunk_list):
            # Embed the chunk as the model will see it, provenance header included, so
            # that a query naming a document by name can match on it.
            vectors.append(embed_text(chunk.as_context()))
            if progress:
                progress(position + 1, len(chunk_list))

        matrix = np.array(vectors, dtype="float32")
        index = faiss.IndexFlatIP(EMBED_DIM)
        index.add(matrix)
        return cls(index, chunk_list)

    # --- persistence --------------------------------------------------------

    @classmethod
    def load(cls, directory: Path | None = None) -> FaissStore:
        """Load a previously built index. Raises if it has not been built yet."""

        import faiss

        directory = directory or config.INDEX_DIR
        index_path = directory / "corpus.faiss"
        meta_path = directory / "chunks.json"
        if not index_path.is_file() or not meta_path.is_file():
            raise FileNotFoundError(
                f"No index at {directory}. Run `python -m scripts.ingest` first."
            )

        payload = json.loads(meta_path.read_text(encoding="utf-8"))
        chunk_list = [Chunk(**row) for row in payload["chunks"]]
        return cls(faiss.read_index(str(index_path)), chunk_list)

    def save(self, directory: Path | None = None) -> Path:
        """Write the index and its metadata side by side."""

        import faiss

        directory = directory or config.INDEX_DIR
        directory.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._index, str(directory / "corpus.faiss"))
        (directory / "chunks.json").write_text(
            json.dumps(
                {
                    "embed_model": config.BEDROCK_EMBED_MODEL_ID,
                    "dim": EMBED_DIM,
                    "chunks": [chunk.model_dump() for chunk in self._chunks],
                },
                indent=1,
            ),
            encoding="utf-8",
        )
        return directory

    # --- search -------------------------------------------------------------

    @property
    def chunks(self) -> list[Chunk]:
        return self._chunks

    def search(
        self,
        query: str,
        *,
        k: int = config.RETRIEVAL_TOP_K,
        allowed_ids: Sequence[int] | None = None,
        corpora: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        """Search, restricted to the chunks this caller may see.

        `allowed_ids` is the permission boundary and is not optional in practice — every
        caller-facing path passes it. `corpora` is a relevance narrowing on top, used
        when a tool knows it only wants IT KB articles or only employment documents.

        Passing an empty `allowed_ids` returns nothing, which is the correct behavior:
        a caller permitted to see no documents retrieves no documents. It is never
        interpreted as "no restriction".
        """

        import faiss
        import numpy as np

        selected = allowed_ids
        if corpora is not None:
            wanted = set(corpora)
            by_corpus = {i for i, c in enumerate(self._chunks) if c.corpus in wanted}
            selected = (
                sorted(by_corpus)
                if selected is None
                else sorted(by_corpus.intersection(selected))
            )

        if selected is not None and len(selected) == 0:
            return []

        vector = np.array([embed_text(query)], dtype="float32")

        if selected is None:
            scores, ids = self._index.search(vector, k)
        else:
            # IDSelectorBatch restricts the search itself: the excluded vectors are
            # never compared against, so a restricted chunk cannot be returned by a
            # later bug in result handling. This is what "filter at the retrieval
            # layer" means concretely.
            selector = faiss.IDSelectorBatch(np.array(selected, dtype="int64"))
            params = faiss.SearchParameters()
            params.sel = selector
            # Keep the selector alive for the duration of the call: FAISS holds a
            # borrowed pointer, and letting Python collect it mid-search is a crash.
            params.own_fields = False
            self._selector_guard = selector
            scores, ids = self._index.search(vector, min(k, len(selected)), params=params)

        return [
            SearchHit(chunk=self._chunks[int(i)], score=round(float(s), 4))
            for s, i in zip(scores[0], ids[0], strict=True)
            if i != -1
        ]
