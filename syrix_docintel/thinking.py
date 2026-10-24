"""Thinking-Panel event helpers for document processing.

Thin wrappers over ``syrix_core_v2.activity_feed.emit`` so the extraction
pipeline surfaces *real* stages (Reading PDF, OCR enabled, Found 16 tables,
Chunking, Creating embeddings, Cross-referencing, Confidence 94%) in the same
live feed the HUD already renders. We reuse existing event KINDs, so no
renderer change is required.

Best-effort and non-blocking: if the feed module isn't importable (headless
tests) every call is a quiet no-op. Failures route through ``syrix_obs.swallow``
so they stay searchable without ever breaking the pipeline.
"""
from __future__ import annotations

import syrix_obs


def _feed():
    try:
        from syrix_core_v2 import activity_feed as af  # type: ignore
        return af
    except Exception:
        return None


def _emit(kind_attr: str, label: str, detail: str = "", request_id: str = "") -> None:
    with syrix_obs.swallow("docintel.thinking.emit"):
        af = _feed()
        if af is None:
            return
        kind = getattr(af, kind_attr, af.KIND_INFO)
        af.emit(kind, str(label), detail=str(detail or ""), request_id=str(request_id or ""))


def stage(label: str, *, detail: str = "", request_id: str = "", kind: str = "") -> None:
    _emit(kind or "KIND_TOOL_CALL", label, detail, request_id)


def reading(label: str, *, detail: str = "", request_id: str = "") -> None:
    _emit("KIND_TOOL_CALL", label, detail, request_id)


def retrieval(label: str, *, detail: str = "", request_id: str = "") -> None:
    _emit("KIND_RETRIEVAL", label, detail, request_id)


def rank(label: str, *, detail: str = "", request_id: str = "") -> None:
    _emit("KIND_FILTER_RANK", label, detail, request_id)


def synthesis(label: str, *, detail: str = "", request_id: str = "") -> None:
    _emit("KIND_SYNTHESIS", label, detail, request_id)


def done(label: str, *, detail: str = "", request_id: str = "") -> None:
    _emit("KIND_DONE", label, detail, request_id)


def error(label: str, *, detail: str = "", request_id: str = "") -> None:
    _emit("KIND_ERROR", label, detail, request_id)
