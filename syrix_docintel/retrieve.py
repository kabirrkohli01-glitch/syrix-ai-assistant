"""Retrieval — pick the chunks most relevant to a query across one or more docs.

Lexical scoring (tf across the chunk, weighted by how rare the term is in the
document set) is the deterministic baseline and needs no model; an optional
semantic rerank (index.py) blends in for larger documents. Guarantees a floor
of coverage per document so multi-document comparison ("does the spreadsheet
support the report?") always has material from every attached file. For generic
asks ("summarize this") with no keyword signal, falls back to each document's
lead chunks (the thesis usually lives up front).
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from . import index
from .types import ChunkObject, DocumentObject

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'\-]+")
_STOPWORDS = frozenset(
    "the a an of in on at to for with about from by and or is are was were be been "
    "do does did have has had this that these those there what which when where who "
    "how why please tell me you it we they i my your our their can could would should "
    "show give find list explain summarize compare".split())


@dataclass
class Retrieved:
    doc: DocumentObject
    chunk: ChunkObject
    score: float


def _tokens(text: str) -> List[str]:
    return [w for w in _WORD_RE.findall((text or "").lower()) if w not in _STOPWORDS and len(w) > 1]


def _lexical(query_terms: List[str], chunks: List[ChunkObject]) -> List[float]:
    if not query_terms or not chunks:
        return [0.0] * len(chunks)
    # Document frequency of each query term across chunks → idf weight.
    df: Counter = Counter()
    chunk_tokens: List[Counter] = []
    for c in chunks:
        toks = Counter(_tokens(c.text))
        chunk_tokens.append(toks)
        for t in set(query_terms):
            if toks.get(t):
                df[t] += 1
    n = len(chunks)
    idf = {t: math.log(1 + n / (1 + df.get(t, 0))) for t in set(query_terms)}
    scores: List[float] = []
    for toks in chunk_tokens:
        length = sum(toks.values()) or 1
        s = 0.0
        for t in query_terms:
            tf = toks.get(t, 0)
            if tf:
                s += (tf / length) * idf.get(t, 1.0)
        scores.append(s)
    return scores


def _normalize(xs: List[float]) -> List[float]:
    m = max(xs) if xs else 0.0
    if m <= 0:
        return [0.0] * len(xs)
    return [x / m for x in xs]


def retrieve(docs: List[DocumentObject], query: str, k_total: int = 12,
             per_doc_floor: int = 1) -> List[Retrieved]:
    """Return up to ``k_total`` Retrieved items ranked by relevance."""
    pairs: List[Tuple[DocumentObject, ChunkObject]] = []
    for d in docs:
        for c in d.chunks:
            if c.text.strip():
                pairs.append((d, c))
    if not pairs:
        return []

    chunks = [c for (_, c) in pairs]
    q_terms = _tokens(query)
    lex = _normalize(_lexical(q_terms, chunks))

    # Optional semantic blend for larger sets.
    combined = list(lex)
    if len(chunks) > 24:
        rr = index.rerank(query, chunks, k=min(len(chunks), 60))
        if rr:
            sem = [0.0] * len(chunks)
            for i, sim in rr:
                if 0 <= i < len(sem):
                    sem[i] = sim
            sem = _normalize(sem)
            combined = [0.5 * l + 0.5 * s for l, s in zip(lex, sem)]

    ranked = sorted(range(len(pairs)), key=lambda i: combined[i], reverse=True)

    # Generic ask with no signal → lead chunks per doc.
    if not q_terms or max(combined) <= 0.0:
        return _lead_chunks(docs, k_total)

    picked: List[Retrieved] = []
    seen_docs: Counter = Counter()
    for i in ranked:
        d, c = pairs[i]
        picked.append(Retrieved(d, c, combined[i]))
        seen_docs[d.id] += 1
        if len(picked) >= k_total:
            break

    # Coverage floor: make sure every doc contributes at least per_doc_floor.
    for d in docs:
        if seen_docs[d.id] >= per_doc_floor:
            continue
        for c in d.chunks:
            if c.text.strip() and not any(r.chunk.id == c.id for r in picked):
                picked.append(Retrieved(d, c, 0.0))
                seen_docs[d.id] += 1
                if seen_docs[d.id] >= per_doc_floor:
                    break
    return picked


def _lead_chunks(docs: List[DocumentObject], k_total: int) -> List[Retrieved]:
    out: List[Retrieved] = []
    per = max(1, k_total // max(1, len(docs)))
    for d in docs:
        for c in d.chunks[:per]:
            if c.text.strip():
                out.append(Retrieved(d, c, 0.0))
    return out[:k_total]
