"""Scoring retrieval against `retrieval_expectations` in `GOLDEN-DATASETS.json`.

§14 names these as part of the deterministic scoring: five queries, each with the
documents a good retrieval should surface. They test the store, not the agent — so the
search runs as a caller who may read every document, across the whole corpus, with no
model involved beyond the embedding.

**The baseline, measured before anything was tuned (2026-10-07):**

| case            | recall @ 8 chunks | recall @ 15 chunks |
| --------------- | ----------------- | ------------------ |
| SIMPLE_002      | 2/2               | 2/2                |
| SIMPLE_004      | 2/2               | 2/2                |
| SIMPLE_006      | 2/3               | 3/3                |
| WF_WEBEX_001    | 4/5               | 4/5                |
| WF_ONBOARD_001  | 2/5               | 4/5                |
| **total**       | **12/17**         | **15/17**          |

Read with two caveats. The agent never runs one search over everything — each tool
searches its own collections, so a policy competes with fewer documents than it does
here. And most misses are policies, which `get_policy` fetches by name rather than by
similarity. So this is a floor on what the store alone does, not the agent's recall.

**What is asserted, and why only that.** Every case must surface at least one expected
document: a query that finds none of its evidence is broken retrieval. Total recall must
not fall below the measured baseline: a change that loses documents fails here. The test
does not demand 17/17 — tuning chunking until a fixed list of five queries passes is
overfitting to the scorer, which the dataset's own `_readme` warns against.
"""

from __future__ import annotations

import json
import os

import pytest

from novaops import config
from novaops.identity import Caller

#: Measured baselines. Lower these only with an entry in docs/DECISIONS.md saying why.
BASELINE_RECALL_AT_8 = 12
BASELINE_RECALL_AT_15 = 15


@pytest.fixture(scope="module")
def store():
    if not (config.INDEX_DIR / "corpus.faiss").is_file():
        pytest.skip("no FAISS index; run `python -m scripts.ingest` first")
    if not (os.environ.get("AWS_ACCESS_KEY_ID")
            or os.path.exists(os.path.expanduser("~/.aws/credentials"))):
        pytest.skip("no AWS credentials; queries are embedded with Bedrock")
    from novaops.retrieval.store import FaissStore

    return FaissStore.load()


@pytest.fixture(scope="module")
def expectations() -> list[dict]:
    raw = json.loads(config.GOLDEN_DATASETS.read_text(encoding="utf-8"))
    return raw["retrieval_expectations"]


def retrieved_documents(store, query: str, k: int) -> list[str]:
    """Distinct source documents in the top-k chunks, in rank order."""

    from novaops.retrieval.filters import visible_ids

    reader = Caller("E006", "UG_IT", is_manager=True)  # may read the whole corpus
    seen: list[str] = []
    for hit in store.search(query, k=k, allowed_ids=visible_ids(store.chunks, reader)):
        if hit.chunk.source not in seen:
            seen.append(hit.chunk.source)
    return seen


def expected(case: dict) -> set[str]:
    """The golden data writes `documents/it_kb/x.md`; our sources omit `documents/`."""

    return {path.removeprefix("documents/") for path in case["expected_documents"]}


def recall(store, cases: list[dict], k: int) -> tuple[int, int, dict[str, set[str]]]:
    found, total, misses = 0, 0, {}
    for case in cases:
        want = expected(case)
        got = set(retrieved_documents(store, case["query"], k))
        found += len(want & got)
        total += len(want)
        if want - got:
            misses[case["case_id"]] = want - got
    return found, total, misses


def test_every_case_surfaces_some_of_its_evidence(store, expectations):
    for case in expectations:
        got = set(retrieved_documents(store, case["query"], k=config.RETRIEVAL_TOP_K))
        assert expected(case) & got, f"{case['case_id']}: none of its documents retrieved"


def test_recall_at_the_configured_k_does_not_regress(store, expectations):
    found, total, misses = recall(store, expectations, k=8)
    assert found >= BASELINE_RECALL_AT_8, (
        f"recall@8 fell to {found}/{total} from {BASELINE_RECALL_AT_8}; missing {misses}"
    )


def test_recall_at_15_does_not_regress(store, expectations):
    found, total, misses = recall(store, expectations, k=15)
    assert found >= BASELINE_RECALL_AT_15, (
        f"recall@15 fell to {found}/{total} from {BASELINE_RECALL_AT_15}; missing {misses}"
    )


def test_the_expected_paths_exist_in_the_corpus(store, expectations):
    """A guard on the scorer itself: if a path did not match any chunk source, every
    "miss" for it would be the scorer's fault rather than the store's."""

    sources = {chunk.source for chunk in store.chunks}
    for case in expectations:
        for path in expected(case):
            assert path in sources, f"{case['case_id']}: {path} is not in the index"
