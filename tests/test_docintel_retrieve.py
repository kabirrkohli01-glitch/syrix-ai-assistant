"""Retrieval: relevance ranking + per-document coverage floor."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture(autouse=True)
def _env():
    import core_boot
    core_boot.CONFIG["docintel_use_embeddings"] = False   # deterministic lexical
    yield


def _doc(doc_id, name, paragraphs):
    from syrix_docintel.types import DocumentObject
    from syrix_docintel.chunk import add_page, finalize
    d = DocumentObject(id=doc_id, name=name, kind="text")
    for i, p in enumerate(paragraphs, start=1):
        add_page(d, i, "", p, kind="text")
    finalize(d)
    return d


def test_relevant_chunk_ranked_first():
    from syrix_docintel import retrieve
    d = _doc("doc_a", "a.txt", [
        "The weather today is sunny and warm.",
        "Our quarterly revenue reached 4.2 million dollars this year.",
        "The office cafeteria serves lunch at noon.",
    ])
    hits = retrieve.retrieve([d], "what was the revenue?", k_total=1)
    assert hits and "revenue" in hits[0].chunk.text


def test_coverage_floor_across_docs():
    from syrix_docintel import retrieve
    a = _doc("doc_a", "a.txt", ["revenue grew by twelve percent"])
    b = _doc("doc_b", "b.txt", ["the mascot is a friendly otter"])
    hits = retrieve.retrieve([a, b], "revenue", k_total=6, per_doc_floor=1)
    doc_ids = {h.doc.id for h in hits}
    assert doc_ids == {"doc_a", "doc_b"}   # both docs represented


def test_generic_query_returns_lead_chunks():
    from syrix_docintel import retrieve
    d = _doc("doc_a", "a.txt", ["First lead paragraph.", "Second.", "Third."])
    hits = retrieve.retrieve([d], "summarize this", k_total=3)
    assert hits and hits[0].chunk.text.startswith("First")


def test_empty_docs():
    from syrix_docintel import retrieve
    assert retrieve.retrieve([], "anything") == []
