"""SYRIX Document Intelligence — universal file understanding.

Public API (used by the backend):

    analyze_upload(name, blob, declared_mime="", request_id="", vision_fn=None)
        -> DocumentObject          # detect → extract → chunk → cache → register

    get_document(doc_id) -> Optional[DocumentObject]

    build_grounding(doc_ids, query, vision_fn=None, request_id="")
        -> (context_block, citations)

Everything is best-effort and honest: on any failure the returned
DocumentObject carries ``status="failed"`` + an ``error``/``notes`` explaining
what happened — SYRIX never pretends a file was processed. Heavy work runs in
the caller's background thread; nothing here blocks the WS loop.
"""
from __future__ import annotations

import re
import time
from typing import Callable, List, Optional, Tuple

import syrix_obs

from . import cache, detect as _detect, extract as _extract, registry, thinking, types as T
from .grounding import build_grounding  # re-export
from .safety import Deadline, ProcessingError, check_size
from .types import DocumentObject

VisionFn = Callable[[bytes, str], str]

__all__ = ["analyze_upload", "get_document", "build_grounding", "DocumentObject"]


def get_document(doc_id: str) -> Optional[DocumentObject]:
    return registry.get(doc_id)


def _summarize(doc: DocumentObject) -> str:
    """Cheap extractive summary set at parse time (the brain writes the real
    one at question time). Just enough for the card / a quick glance."""
    if doc.summary:
        return doc.summary
    if doc.kind == T.KIND_SPREADSHEET and doc.metadata.get("sheet_names"):
        names = ", ".join(doc.metadata["sheet_names"][:8])
        return f"Spreadsheet with {doc.sheets} sheet(s): {names}."
    if doc.kind == T.KIND_ARCHIVE:
        langs = doc.metadata.get("languages") or {}
        lg = ", ".join(langs.keys()) if langs else "mixed"
        return f"Archive of {doc.metadata.get('entry_count', 0)} files ({lg})."
    text = doc.full_text().strip()
    if not text:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", text[:1200])
    return " ".join(sentences[:3])[:400]


def _emit_extraction_stages(doc: DocumentObject, request_id: str) -> None:
    if doc.metadata.get("ocr_used"):
        thinking.stage("OCR enabled", detail=str(doc.metadata.get("ocr_method", "")), request_id=request_id)
    if doc.tables:
        thinking.stage(f"Found {len(doc.tables)} table(s)", request_id=request_id)
    if doc.chunks:
        thinking.stage(f"Split into {len(doc.chunks)} section(s)", request_id=request_id)


def analyze_upload(name: str, blob: bytes, *, declared_mime: str = "",
                   request_id: str = "", vision_fn: Optional[VisionFn] = None) -> DocumentObject:
    t0 = time.perf_counter()
    name = str(name or "upload.bin")
    blob = blob or b""
    sha = cache.sha256_bytes(blob)
    doc_id = cache.doc_id_for(sha)

    # --- size guard (graceful) ---
    try:
        check_size(len(blob))
    except ProcessingError as exc:
        doc = DocumentObject(id=doc_id, name=name, size=len(blob), sha256=sha,
                             status=T.STATUS_FAILED, error=str(exc))
        registry.put(doc)
        thinking.error("Upload refused", detail=str(exc), request_id=request_id)
        return doc

    # --- cache reuse ---
    cached = cache.load(doc_id)
    if cached and cached.get("chunks") and cached.get("status") != T.STATUS_FAILED:
        try:
            doc = DocumentObject.from_dict(cached)
            registry.put(doc)
            if doc.kind == T.KIND_IMAGE:
                registry.put_blob(doc_id, blob)
            thinking.reading(f"Reusing cached analysis of {name}", request_id=request_id)
            thinking.done(f"Ready · {doc.card_line()}", request_id=request_id)
            return doc
        except Exception as exc:
            syrix_obs.report("docintel.analyze.cache_rehydrate", exc, doc_id=doc_id)

    # --- detect + extract ---
    det = _detect.detect(name, blob, declared_mime)
    doc = DocumentObject(id=doc_id, name=name, kind=det.kind, mime=det.mime,
                         subtype=det.subtype, size=len(blob), sha256=sha)
    thinking.reading(f"Reading {name}", detail=doc.card_line(), request_id=request_id)

    deadline = Deadline()
    try:
        _extract.run(det.kind, blob, doc, deadline)
    except ProcessingError as exc:
        doc.status = T.STATUS_FAILED
        doc.error = str(exc)
        thinking.error(f"Could not read {name}", detail=str(exc), request_id=request_id)
    except Exception as exc:
        doc.status = T.STATUS_FAILED
        doc.error = f"{type(exc).__name__}: {exc}"
        syrix_obs.report("docintel.analyze.extract", exc, doc_id=doc_id, kind=det.kind)
        thinking.error(f"Could not read {name}", detail=str(exc)[:120], request_id=request_id)

    doc.processing_time = round(time.perf_counter() - t0, 3)
    if doc.status != T.STATUS_FAILED:
        _emit_extraction_stages(doc, request_id)
        doc.summary = _summarize(doc)

    # Keep image bytes in RAM for vision at question time.
    if doc.kind == T.KIND_IMAGE:
        registry.put_blob(doc_id, blob)

    registry.put(doc)

    if doc.status == T.STATUS_FAILED:
        thinking.error(f"Failed · {name}", detail=doc.error, request_id=request_id)
    else:
        label = "Partial" if doc.status == T.STATUS_PARTIAL else "Ready"
        thinking.done(f"{label} · {doc.card_line()}", request_id=request_id)
    return doc
