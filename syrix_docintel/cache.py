"""Content-addressed disk cache for parsed documents.

Keyed by the SHA-256 of the raw file bytes, so re-uploading the same file (or
re-opening a past session) reuses the extraction, OCR and summary instead of
re-processing — the spec's "reuse embeddings / OCR / summaries" requirement.

Lives under ``%LOCALAPPDATA%\\SYRIX\\cache\\docintel`` (never OneDrive — same
rule as every other hot runtime file, via ``syrix_paths``). Honors the
``docintel_persist`` config flag: when false, this is a no-op RAM-only mode.

Never raises — a cache failure just means a re-parse.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, Optional

import syrix_obs


def sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob or b"").hexdigest()


def doc_id_for(sha256: str) -> str:
    """Stable, content-addressed doc id (drives dedupe + cache filename)."""
    return "doc_" + str(sha256 or "")[:16]


def persist_enabled() -> bool:
    try:
        import core_boot  # type: ignore
        return bool(getattr(core_boot, "CONFIG", {}).get("docintel_persist", True))
    except Exception:
        return True


def _dir() -> str:
    try:
        import syrix_paths  # type: ignore
        d = os.path.join(syrix_paths.cache_dir(), "docintel")
    except Exception:
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "docintel_cache")
    with syrix_obs.swallow("docintel.cache.mkdir"):
        os.makedirs(d, exist_ok=True)
    return d


def _path(doc_id: str) -> str:
    safe = "".join(c for c in str(doc_id) if c.isalnum() or c in "_-")
    return os.path.join(_dir(), f"{safe}.json")


def load(doc_id: str) -> Optional[Dict[str, Any]]:
    if not persist_enabled():
        return None
    p = _path(doc_id)
    try:
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                return d
    except Exception as exc:
        syrix_obs.report("docintel.cache.load", exc, doc_id=doc_id)
    return None


def save(doc_id: str, doc_dict: Dict[str, Any]) -> None:
    if not persist_enabled():
        return
    p = _path(doc_id)
    try:
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc_dict, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception as exc:
        syrix_obs.report("docintel.cache.save", exc, doc_id=doc_id)


def _blob_path(doc_id: str) -> str:
    return _path(doc_id)[:-len(".json")] + ".img"


def save_blob(doc_id: str, blob: bytes) -> None:
    """Keep an image's bytes beside its analysis, so it can still be looked
    at after a restart — the in-memory copy was the only one, and a chat
    reopened in a new session had an image nobody could see."""
    if not persist_enabled() or not blob:
        return
    try:
        path = _blob_path(doc_id)
        if os.path.isfile(path):
            return
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(blob)
        os.replace(tmp, path)
    except Exception as exc:
        syrix_obs.report("docintel.cache.save_blob", exc, level="debug")


def load_blob(doc_id: str) -> Optional[bytes]:
    try:
        path = _blob_path(doc_id)
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                return fh.read()
    except Exception as exc:
        syrix_obs.report("docintel.cache.load_blob", exc, level="debug")
    return None


def delete(doc_id: str) -> None:
    for p in (_path(doc_id), _blob_path(doc_id)):
        try:
            if os.path.isfile(p):
                os.unlink(p)
        except Exception as exc:
            syrix_obs.report("docintel.cache.delete", exc, doc_id=doc_id)
