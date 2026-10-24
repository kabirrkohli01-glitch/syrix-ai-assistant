"""Optional semantic rerank over chunks (sentence-transformers MiniLM).

Lexical retrieval (retrieve.py) is the always-available baseline; this adds a
semantic pass for larger documents so a question like "find the risks" matches
paragraphs about "exposure / uncertainty" that share no keywords. Reuses the
shared MiniLM encoder (``syrix_embeddings``) already used by mem0/LightRAG — no
new model. Gated by ``docintel_use_embeddings`` (default True) and bounded so
query latency stays predictable. Returns ``None`` on any failure → caller keeps
the lexical ranking.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import syrix_obs

from .types import ChunkObject

_EMBED_CAP = 400   # never embed more than this many chunks per query


def enabled() -> bool:
    try:
        import core_boot  # type: ignore
        return bool(getattr(core_boot, "CONFIG", {}).get("docintel_use_embeddings", True))
    except Exception:
        return True


def rerank(query: str, chunks: List[ChunkObject], k: int) -> Optional[List[Tuple[int, float]]]:
    """Return ``[(chunk_index, similarity), ...]`` top-k, or None if unavailable."""
    if not enabled() or not chunks or not (query or "").strip():
        return None
    try:
        import numpy as np  # type: ignore
        import syrix_embeddings  # type: ignore
        model = syrix_embeddings.get_model()
        if model is None:
            return None
        subset = chunks[:_EMBED_CAP]
        texts = [c.text[:1000] for c in subset]
        qv = model.encode([query], normalize_embeddings=True)
        cv = model.encode(texts, normalize_embeddings=True)
        qv = np.asarray(qv)[0]
        cv = np.asarray(cv)
        sims = cv @ qv
        order = np.argsort(-sims)[: max(1, k)]
        return [(int(i), float(sims[int(i)])) for i in order]
    except Exception as exc:
        syrix_obs.report("docintel.index.rerank", exc, level="info")
        return None
