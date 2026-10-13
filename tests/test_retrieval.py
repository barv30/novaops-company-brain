"""Ingest and the permission boundary, checked against the real corpus.

The store tests use a stub embedder rather than Bedrock. What they are checking is that
a restricted chunk cannot be *returned*, and that property belongs to the index and the
selector, not to the embedding model — so paying for 472 Titan calls to assert it would
buy nothing and make the suite unrunnable offline.
"""

from __future__ import annotations

import numpy as np
import pytest

from novaops import config
from novaops.identity import Caller
from novaops.retrieval.documents import (
    MAX_CHUNK_CHARS,
    Chunk,
    chunk_document,
    load_corpus,
    parse_front_matter,
)
from novaops.retrieval.filters import visible, visible_ids
from novaops.retrieval.store import FaissStore

MAYA = Caller("E001", "UG_REGULAR", is_manager=False)   # the S9 permission trap
YAEL = Caller("E018", "UG_REGULAR", is_manager=True)    # a manager inside UG_REGULAR


@pytest.fixture(scope="module")
def corpus() -> list[Chunk]:
    return load_corpus()


# 1. FRONT MATTER SURVIVES INGEST -------------------------------------------

def test_front_matter_is_parsed():
    meta, body = parse_front_matter(
        "---\nlast_updated: 2024-08-05\ncorpus: manager_playbook\n"
        "audience: manager\n---\n\n# Promotion\n\nText."
    )
    assert meta["audience"] == "manager"
    assert body.startswith("# Promotion")


def test_a_document_without_front_matter_is_not_an_error():
    """70 of 102 documents have none. That is the common case, not a failure."""

    meta, body = parse_front_matter("# Access Management Policy\n\nOwner: IT\n")
    assert meta == {}
    assert body.startswith("# Access Management Policy")


def test_every_chunk_keeps_its_audience(corpus):
    """The whole permission boundary depends on this one property."""

    by_corpus = {"handbook": "all", "manager_playbook": "manager"}
    for chunk in corpus:
        if chunk.corpus in by_corpus:
            assert chunk.audience == by_corpus[chunk.corpus], chunk.source
        else:
            assert chunk.audience is None, chunk.source


def test_source_paths_match_the_golden_dataset_form(corpus):
    """`expected_sources` and `forbidden_sources` use 'collection/file.md'."""

    sources = {chunk.source for chunk in corpus}
    assert "handbook/making-a-career.md" in sources
    assert "manager_playbook/promotion.md" in sources
    assert not any(source.startswith("/") or "\\" in source for source in sources)


# 2. CHUNKING (measured, not assumed) ---------------------------------------

def test_short_documents_stay_whole(corpus):
    """A 900-character IT KB article is already the unit a reader would cite."""

    for collection in ("policies", "it_kb", "contracts", "employment", "internal_memos"):
        sources = {c.source for c in corpus if c.corpus == collection}
        chunks = [c for c in corpus if c.corpus == collection]
        assert len(chunks) == len(sources), f"{collection} was split unnecessarily"


def test_long_documents_are_split(corpus):
    """Handbook and manager_playbook median ~9,500 chars; Titan truncates past ~32k."""

    for collection in ("handbook", "manager_playbook"):
        chunks = [c for c in corpus if c.corpus == collection]
        sources = {c.source for c in chunks}
        assert len(chunks) > len(sources) * 3, f"{collection} was barely split"


def test_no_chunk_exceeds_the_embedding_budget(corpus):
    """The failure this prevents is silent truncation inside Titan."""

    # The cap applies per section; overlap and a merged trailing section can push one
    # chunk somewhat over. What matters is that nothing approaches Titan's ~32k limit.
    oversized = [c for c in corpus if len(c.text) > MAX_CHUNK_CHARS * 2]
    assert not oversized, [(c.source, len(c.text)) for c in oversized[:3]]


def test_chunks_carry_their_provenance_into_the_text(corpus):
    """A model cites what it can see. A bare chunk gives it nothing to cite."""

    chunk = next(c for c in corpus if c.source == "handbook/making-a-career.md")
    assert "[source: handbook/making-a-career.md]" in chunk.as_context()


def test_chunk_document_reads_one_file():
    path = config.DOCUMENTS_DIR / "policies" / "access_management_policy.md"
    chunks = chunk_document(path)
    assert len(chunks) == 1
    assert chunks[0].corpus == "policies"
    assert chunks[0].audience is None


# 3. THE PERMISSION BOUNDARY ------------------------------------------------

def test_a_regular_employee_cannot_see_the_manager_playbook(corpus):
    hidden = [c for c in corpus if not visible(c, MAYA)]
    assert hidden, "nothing was withheld — the filter is inert"
    assert {c.corpus for c in hidden} == {"manager_playbook"}


def test_the_withheld_set_is_exactly_the_restricted_collection(corpus):
    """Withhold too little and it leaks; too much and 70 documents vanish."""

    restricted = sum(1 for c in corpus if c.corpus == "manager_playbook")
    assert len(visible_ids(corpus, YAEL)) - len(visible_ids(corpus, MAYA)) == restricted


def test_unlabelled_collections_stay_readable_by_everyone(corpus):
    """The trap: treating absent metadata as deny blocks 70 of 102 documents."""

    unlabelled = [c for c in corpus if c.audience is None]
    assert len(unlabelled) == 70
    assert all(visible(c, MAYA) for c in unlabelled)


# 4. FILTERING HAPPENS INSIDE THE SEARCH ------------------------------------

@pytest.fixture
def tiny_store(monkeypatch) -> FaissStore:
    """A four-chunk index with a deterministic stub embedder.

    Every vector is identical, so ranking cannot explain the result: whatever comes
    back came back because the selector allowed it.
    """

    import novaops.retrieval.store as store_module

    monkeypatch.setattr(
        store_module, "embed_text", lambda text: list(np.ones(1024, dtype="float32") / 32)
    )

    chunks = [
        Chunk(text="career growth", source="handbook/making-a-career.md",
              corpus="handbook", audience="all"),
        Chunk(text="promotion cycle", source="manager_playbook/promotion.md",
              corpus="manager_playbook", audience="manager"),
        Chunk(text="performance reviews", source="manager_playbook/performance-reviews.md",
              corpus="manager_playbook", audience="manager"),
        Chunk(text="access policy", source="policies/access_management_policy.md",
              corpus="policies", audience=None),
    ]
    return FaissStore.build(chunks)


def test_search_never_returns_a_restricted_chunk(tiny_store):
    """The S9 trap, at the layer where it has to be stopped."""

    hits = tiny_store.search("how do I get promoted", k=4,
                             allowed_ids=visible_ids(tiny_store.chunks, MAYA))
    assert {hit.chunk.corpus for hit in hits} == {"handbook", "policies"}
    assert all("manager_playbook" not in hit.chunk.source for hit in hits)


def test_a_manager_reaches_the_playbook(tiny_store):
    hits = tiny_store.search("how do I get promoted", k=4,
                             allowed_ids=visible_ids(tiny_store.chunks, YAEL))
    assert any(hit.chunk.corpus == "manager_playbook" for hit in hits)


def test_an_empty_allowlist_returns_nothing_rather_than_everything(tiny_store):
    """Empty must never be read as 'no restriction'. This is the fail-closed test."""

    assert tiny_store.search("anything", k=4, allowed_ids=[]) == []


def test_corpus_narrowing_composes_with_the_permission_filter(tiny_store):
    """A relevance narrowing must not widen the permission boundary."""

    hits = tiny_store.search(
        "promotion", k=4,
        allowed_ids=visible_ids(tiny_store.chunks, MAYA),
        corpora=["manager_playbook"],
    )
    assert hits == []


# 5. PERSISTENCE ------------------------------------------------------------

def test_an_index_round_trips(tiny_store, tmp_path, monkeypatch):
    import novaops.retrieval.store as store_module

    monkeypatch.setattr(
        store_module, "embed_text", lambda text: list(np.ones(1024, dtype="float32") / 32)
    )
    tiny_store.save(tmp_path)
    reloaded = FaissStore.load(tmp_path)

    assert len(reloaded.chunks) == len(tiny_store.chunks)
    assert reloaded.chunks[0].audience == "all"
    assert reloaded.chunks[1].source == "manager_playbook/promotion.md"


def test_loading_a_missing_index_says_what_to_do(tmp_path):
    with pytest.raises(FileNotFoundError, match="scripts.ingest"):
        FaissStore.load(tmp_path / "nothing")


# --- KB steps (retrieval/kb.py) -------------------------------------------------------

def _kb(name: str) -> str:
    return (config.DOCUMENTS_DIR / "it_kb" / f"{name}.md").read_text(encoding="utf-8")


def test_the_vpn_article_yields_its_steps_including_mfa_and_device_trust():
    """S8 turn 1 needs 'MFA' and 'device trust'; they are in the first step."""

    from novaops.retrieval.kb import troubleshooting_steps

    steps = troubleshooting_steps(_kb("vpn_access"))
    assert steps[0].startswith("Confirm target actually requires VPN")
    assert "MFA and device trust" in steps[0]


def test_steps_are_split_by_sentence_never_inside_one():
    """Splitting on commas turned 'by manager, HR, or IT video check' into three steps."""

    from novaops.retrieval.kb import troubleshooting_steps

    steps = troubleshooting_steps(_kb("mfa_reset"))
    assert steps[0] == "Verify identity by manager, HR, or IT video check."
    assert len(steps) == 4


def test_every_kb_article_yields_steps():
    from novaops.retrieval.kb import troubleshooting_steps

    for path in (config.DOCUMENTS_DIR / "it_kb").glob("*.md"):
        assert troubleshooting_steps(path.read_text(encoding="utf-8")), path.name


def test_text_without_the_section_yields_nothing():
    from novaops.retrieval.kb import troubleshooting_steps

    assert troubleshooting_steps("## Escalation\n\nEscalate to IT Manager.") == []
