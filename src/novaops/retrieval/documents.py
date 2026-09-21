"""Turning the corpus into retrievable chunks, with the metadata that governs access.

Lesson 10's `server/rag.py` embeds each document whole and keeps only its filename.
That works for the lesson's corpus and does not work here, for two measured reasons:

**Size.** The NovaOps corpus splits cleanly in two. 70 documents — contracts, employment,
memos, IT KB, policies — have a median length of roughly 1,300 characters and embed whole
comfortably. The other 32 — `handbook/` and `manager_playbook/` — have a median of about
9,500 characters and a maximum of 43,428. Titan Text Embeddings V2 accepts roughly 8,000
tokens, so the largest handbook file would be silently truncated; and a single vector
averaging a 43k-character document is too diffuse to match a specific question anyway.

**Metadata.** The permission boundary is `audience` front matter, and you cannot filter on
metadata you dropped at ingest. Every chunk therefore carries its source path, collection
and audience all the way through to the search result.

So: small documents stay whole, large ones split on their `##` headings, and every chunk
keeps the front matter of the file it came from.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from novaops import config

# A chunk at or below this size is left alone. Chosen so that the five short
# collections — whose median document is ~1,300 characters — stay whole, since a
# policy or an IT KB article is already the unit a reader would cite. Only the two
# long collections split.
MAX_CHUNK_CHARS = 2_000

# Sections shorter than this are merged into the next one rather than embedded alone.
# A 40-character heading stub retrieves badly and dilutes the index.
MIN_CHUNK_CHARS = 200

# Carried between paragraph splits inside an oversized section, so a claim that
# straddles the split still has its context in at least one chunk.
OVERLAP_CHARS = 200


class Chunk(BaseModel):
    """One retrievable unit, and everything needed to decide who may see it.

    `source` is the path relative to `documents/` — the same form the golden dataset's
    `expected_sources` and `forbidden_sources` use, so a scorer can compare them without
    normalising first, and a citation can point at it directly.
    """

    text: str
    source: str = Field(
        description="Path relative to documents/, e.g. 'handbook/making-a-career.md'."
    )
    corpus: str = Field(description="The collection it came from, e.g. 'handbook'.")
    audience: str | None = Field(
        default=None,
        description="From front matter. None means the document carries no audience field, "
                    "which means unrestricted — see ADR-004.",
    )
    heading: str | None = Field(default=None, description="The '##' section this chunk came from.")
    title: str | None = Field(default=None, description="The document's '#' title.")
    chunk_index: int = Field(default=0, description="Position within its source document.")
    last_updated: str | None = Field(default=None, description="From front matter, when present.")

    @property
    def citation_text(self) -> str:
        """How this chunk identifies itself in a citation."""

        return f"{self.source}#{self.heading}" if self.heading else self.source

    def as_context(self) -> str:
        """The chunk as the model should see it, with its own provenance attached.

        The source travels *inside* the text as well as in metadata. A model asked to
        cite its evidence cites what it can see, and a chunk that arrives as bare prose
        gives it nothing to cite.
        """

        header = f"[source: {self.source}]"
        if self.title:
            header += f" {self.title}"
        if self.heading:
            header += f" — {self.heading}"
        return f"{header}\n{self.text}"


def parse_front_matter(raw: str) -> tuple[dict, str]:
    """Split a markdown file into its YAML front matter and its body.

    A file with no front matter returns an empty mapping — which is the common case
    here (70 of 102 documents) and is not an error.
    """

    if not raw.startswith("---"):
        return {}, raw

    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}, raw

    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}, raw
    if not isinstance(meta, dict):
        return {}, raw
    return meta, parts[2].lstrip("\n")


def _split_oversized(section: str) -> Iterator[str]:
    """Split a section that exceeds the cap, on paragraph boundaries, with overlap."""

    paragraphs = [p for p in section.split("\n\n") if p.strip()]
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= MAX_CHUNK_CHARS or not current:
            current = candidate
            continue
        yield current
        current = f"{current[-OVERLAP_CHARS:]}\n\n{paragraph}" if OVERLAP_CHARS else paragraph
    if current.strip():
        yield current


def _sections(body: str) -> Iterator[tuple[str | None, str]]:
    """Yield `(heading, text)` for each `##` section, with any preamble first."""

    heading: str | None = None
    buffer: list[str] = []

    for line in body.splitlines():
        if line.startswith("## "):
            if any(line_.strip() for line_ in buffer):
                yield heading, "\n".join(buffer).strip()
            heading = line[3:].strip()
            buffer = []
        else:
            buffer.append(line)

    if any(line_.strip() for line_ in buffer):
        yield heading, "\n".join(buffer).strip()


def chunk_document(path: Path, documents_dir: Path | None = None) -> list[Chunk]:
    """Read one markdown file and return its chunks, front matter preserved.

    A document small enough to stay whole stays whole: splitting a 900-character IT KB
    article into three pieces would lose the article as a unit and gain nothing.
    """

    documents_dir = documents_dir or config.DOCUMENTS_DIR
    raw = path.read_text(encoding="utf-8")
    meta, body = parse_front_matter(raw)

    source = path.relative_to(documents_dir).as_posix()
    corpus = path.parent.name
    audience = meta.get("audience")
    last_updated = str(meta["last_updated"]) if meta.get("last_updated") else None

    title = next(
        (line[2:].strip() for line in body.splitlines() if line.startswith("# ")),
        None,
    )

    def build(text: str, heading: str | None, index: int) -> Chunk:
        return Chunk(
            text=text,
            source=source,
            corpus=corpus,
            audience=audience,
            heading=heading,
            title=title,
            chunk_index=index,
            last_updated=last_updated,
        )

    if len(body) <= MAX_CHUNK_CHARS:
        return [build(body.strip(), None, 0)] if body.strip() else []

    chunks: list[Chunk] = []
    pending_heading: str | None = None
    pending_text = ""

    for heading, text in _sections(body):
        if pending_text:
            text = f"{pending_text}\n\n{text}"
            heading = pending_heading or heading
            pending_text, pending_heading = "", None

        # Too small to stand alone: hold it and prepend it to the next section.
        if len(text) < MIN_CHUNK_CHARS:
            pending_text, pending_heading = text, heading
            continue

        pieces = [text] if len(text) <= MAX_CHUNK_CHARS else list(_split_oversized(text))
        for piece in pieces:
            chunks.append(build(piece, heading, len(chunks)))

    if pending_text.strip():
        if chunks:
            # Nothing left to merge forward into; attach it to the last chunk.
            chunks[-1] = chunks[-1].model_copy(
                update={"text": f"{chunks[-1].text}\n\n{pending_text}"}
            )
        else:
            chunks.append(build(pending_text, pending_heading, 0))

    return chunks


def load_corpus(documents_dir: Path | None = None) -> list[Chunk]:
    """Chunk every markdown file under `documents/`, sorted for a reproducible index."""

    documents_dir = documents_dir or config.DOCUMENTS_DIR
    chunks: list[Chunk] = []
    for path in sorted(documents_dir.rglob("*.md")):
        chunks.extend(chunk_document(path, documents_dir))
    return chunks
