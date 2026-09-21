"""Retrieval: ingest, store, and the permission filter.

Phase 1. Planned modules:
  ingest.py   parse front matter, chunk, embed, index. Must preserve `audience`.
  store.py    the DocumentStore protocol and the FAISS implementation (ADR-003).
  filters.py  the audience filter, applied to the query and failing closed (ADR-004).
  rerank.py   optional second stage, only if measurement shows it is needed.
"""
