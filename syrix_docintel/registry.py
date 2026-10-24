"""In-memory document registry (disk-backed when persistence is on).

The backend registers each parsed :class:`DocumentObject` here keyed by its
content-addressed ``doc_id``; the typed-chat grounding step looks them up by
the ``doc_ids`` the composer sends. On a cold lookup (e.g. after a restart,
re-opening a past session) the registry rehydrates from the disk cache.

Thread-safe. Never raises.
"""
from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional

import syrix_obs

from . import cache
from .types import DocumentObject

_LOCK = threading.RLock()
_DOCS: Dict[str, DocumentObject] = {}
_BLOBS: Dict[str, bytes] = {}     # raw bytes for image docs (vision at question time)
_MAX_LIVE = 64   # cap the RAM working set; disk cache is the durable store
_MAX_BLOBS = 24
_MAX_BLOB_BYTES = 12 * 1024 * 1024


def put(doc: DocumentObject) -> None:
    if not doc or not doc.id:
        return
    with _LOCK:
        _DOCS[doc.id] = doc
        if len(_DOCS) > _MAX_LIVE:
            # Drop the oldest by created_ts.
            oldest = min(_DOCS.values(), key=lambda d: d.created_ts)
            _DOCS.pop(oldest.id, None)
    try:
        cache.save(doc.id, doc.to_dict())
    except Exception as exc:
        syrix_obs.report("docintel.registry.put_cache", exc, doc_id=doc.id)


def get(doc_id: str) -> Optional[DocumentObject]:
    if not doc_id:
        return None
    with _LOCK:
        d = _DOCS.get(doc_id)
    if d is not None:
        return d
    # Cold path: rehydrate from disk cache.
    raw = cache.load(doc_id)
    if raw:
        try:
            doc = DocumentObject.from_dict(raw)
            with _LOCK:
                _DOCS[doc.id] = doc
            return doc
        except Exception as exc:
            syrix_obs.report("docintel.registry.rehydrate", exc, doc_id=doc_id)
    return None


def get_many(doc_ids: List[str]) -> List[DocumentObject]:
    out: List[DocumentObject] = []
    for did in doc_ids or []:
        d = get(str(did))
        if d is not None:
            out.append(d)
    return out


def recent_docs(limit: int = 3, max_age_s: float = 1800.0) -> List[DocumentObject]:
    """Most recently parsed, non-failed docs (newest first) — lets a follow-up
    turn ("learn the document I just uploaded") find its target without the
    composer resending doc_ids. Read-only; RAM working set only."""
    now = time.time()
    with _LOCK:
        live = [d for d in _DOCS.values()
                if d.status != "failed" and (now - d.created_ts) <= max_age_s]
    live.sort(key=lambda d: d.created_ts, reverse=True)
    return live[:max(0, int(limit))]


def has(doc_id: str) -> bool:
    with _LOCK:
        if doc_id in _DOCS:
            return True
    return cache.load(doc_id) is not None


def drop(doc_id: str) -> None:
    with _LOCK:
        _DOCS.pop(doc_id, None)
    cache.delete(doc_id)


def put_blob(doc_id: str, blob: bytes) -> None:
    """Keep an image doc's raw bytes so the vision model can read the actual
    pixels at question time: in RAM (capped, oldest evicted) and on disk
    beside the analysis, so a restart does not leave it unseeable."""
    if not doc_id or not blob or len(blob) > _MAX_BLOB_BYTES:
        return
    with _LOCK:
        _BLOBS[doc_id] = bytes(blob)
        if len(_BLOBS) > _MAX_BLOBS:
            _BLOBS.pop(next(iter(_BLOBS)), None)
    cache.save_blob(doc_id, blob)


def get_blob(doc_id: str) -> Optional[bytes]:
    with _LOCK:
        blob = _BLOBS.get(str(doc_id))
    if blob is None:
        blob = cache.load_blob(str(doc_id))
        if blob:
            with _LOCK:
                _BLOBS[str(doc_id)] = blob
    return blob


def _reset_for_tests() -> None:
    with _LOCK:
        _DOCS.clear()
        _BLOBS.clear()
