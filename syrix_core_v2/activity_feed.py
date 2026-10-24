"""SYRIX Research Activity Feed — real-time visibility into what SYRIX is doing.

Grok-style live event stream:
  - searching the web
  - reading sources
  - routing brains / fallback
  - loading memory
  - synthesising a reply
  - speaking

The feed is a ring buffer of the most recent N events, written to the HUD
state file so every theme (Syrix / Liquid Glass / Eclipse / Aurora) can render
it from the same source of truth.

Design rules:
  * Pure stdlib. Never blocks the spine.
  * No raw text leaks (we surface labels + source URLs only).
  * Sources are clickable: each "source" event carries a URL.
  * Events for the current turn share a `request_id`. Older turns stay in
    the buffer so the user can scroll back through the feed.
  * Thread-safe: a single RLock guards both append and snapshot.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

_RING_CAP = 200
_RING: Deque[Dict[str, Any]] = deque(maxlen=_RING_CAP)
_LOCK = threading.RLock()
_HUD_STATE_PATH: Optional[Path] = None
_LAST_WRITE_TS = 0.0
_MIN_WRITE_INTERVAL_S = 0.1  # 10 Hz cap on HUD writes
_PENDING_FLUSH = False
_LOG = logging.getLogger(__name__)

# 2026-07-10 — feed labels/details are user-visible in the HUD thinking panel
# AND persisted to hud_state.json on disk; they used to bypass syrix_redact
# entirely, so a secret echoed into a label landed in the state file verbatim.
# Kill switch: config.json "activity_feed_redact_enabled" (default true).
_REDACT_FLAG: Optional[bool] = None


def _redact_enabled() -> bool:
    """Read the redaction kill switch once; default ON, ON if config unreadable."""
    global _REDACT_FLAG
    if _REDACT_FLAG is None:
        enabled = True
        try:
            cfg_path = Path(__file__).resolve().parent.parent / "config.json"
            with open(cfg_path, "r", encoding="utf-8") as fh:
                enabled = bool(json.load(fh).get("activity_feed_redact_enabled", True))
        except Exception:
            enabled = True
        _REDACT_FLAG = enabled
    return _REDACT_FLAG


def _redact(text: str) -> str:
    """Scrub secrets from a feed string. Best-effort: input returned unchanged
    if redaction is disabled or syrix_redact is unavailable."""
    if not text or not _redact_enabled():
        return text
    try:
        from syrix_redact import redact_text

        return redact_text(text)
    except Exception:
        return text

# Known event kinds — UI maps each to an icon + colour.
KIND_INTENT = "intent"           # intent classified
KIND_PLAN = "plan"               # strategy chosen
KIND_BRAIN_ROUTE = "brain_route" # brain provider picked
KIND_BRAIN_FALLBACK = "brain_fallback"  # fallback to alternate provider
KIND_BRAIN_RESTORED = "brain_restored"  # primary brain back online
KIND_TOOL_CALL = "tool_call"     # adapter / tool invoked
KIND_SEARCH = "search"           # web search fired
KIND_SOURCE = "source"           # a source was read (carries URL)
KIND_MEMORY = "memory"           # memory read / write
KIND_SYNTHESIS = "synthesis"     # brain composing the answer
KIND_SPEAK = "speak"             # TTS speaking
KIND_DONE = "done"               # turn complete
KIND_ERROR = "error"             # something failed (non-fatal)
KIND_INFO = "info"               # general status note
KIND_ZENITH = "zenith"           # ZENITH sub-agent event (agent_* extras)
KIND_MODE = "mode"               # AUTO resolved to fast/deep (friendly label)
# Atlas pipeline stages (2026-07-02 rebuild) — each fires only when the real
# backend step executes; the HUD ladder renders them in sequence per rid.
KIND_CONTEXT_INIT = "context_init"    # clean per-query context block built
KIND_RETRIEVAL = "retrieval"          # a retrieval provider fired / returned
KIND_FILTER_RANK = "filter_rank"      # dedup + rank over raw results
KIND_VERIFY = "verify"                # cross-source validation verdict
KIND_PACK_BUILT = "pack_built"        # fact pack v1 assembled
KIND_PACK_ENRICHED = "pack_enriched"  # fact pack v2 (page reads) ready
KIND_SPARK_RUN = "spark_run"          # Spark engine executing
KIND_ZENITH_RUN = "zenith_run"        # Zenith deep engine executing
KIND_MERGE_DONE = "merge_done"        # deep layer merged into the chat entry
# Connectors (2026-07-12) — a connector (Gmail, ...) touched the user's own
# data this turn. First-class kind so the HUD renders a distinct, named pill
# ("Using Gmail connector") instead of a generic tool step.
KIND_CONNECTOR = "connector"


def _resolve_hud_state_path() -> Path:
    """Locate hud_state.json. Cached after first lookup."""
    global _HUD_STATE_PATH
    if _HUD_STATE_PATH is not None:
        return _HUD_STATE_PATH
    # Phase 0 (2026-06-10): runtime mailboxes live under %LOCALAPPDATA%\SYRIX
    # (resolved by syrix_paths) — never beside the code in OneDrive.
    try:
        import syrix_paths

        _HUD_STATE_PATH = Path(syrix_paths.state_file("hud_state.json"))
        return _HUD_STATE_PATH
    except Exception:
        pass
    candidates = [
        Path(__file__).resolve().parent.parent / "hud_state.json",
        Path.cwd() / "hud_state.json",
    ]
    for p in candidates:
        if p.exists() or p.parent.exists():
            _HUD_STATE_PATH = p
            return p
    _HUD_STATE_PATH = candidates[0]
    return _HUD_STATE_PATH


def _atomic_write_state(path: Path, payload: Dict[str, Any]) -> None:
    """Best-effort merge-write into hud_state.json. Never raises.

    2026-07-02 — the whole read-merge-replace cycle now runs under the shared
    ``HUD_STATE_WRITE_LOCK`` so a concurrent ``write_hud_state`` cycle can't
    interleave and drop this flush (the trace-steps-vanish TOCTOU).
    """
    try:
        from .state_write_lock import HUD_STATE_WRITE_LOCK as _wlock
    except Exception:
        _wlock = None
    try:
        _cm = _wlock if _wlock is not None else _NULL_LOCK
        with _cm:
            # Read existing state and merge our keys in.
            existing: Dict[str, Any] = {}
            try:
                if path.exists():
                    with open(path, "r", encoding="utf-8") as f:
                        existing = json.load(f) or {}
                        if not isinstance(existing, dict):
                            existing = {}
            except Exception:
                existing = {}
            existing.update(payload)
            # Never share a fixed ``hud_state.json.tmp`` with another writer.
            # Concurrent opens of that one path can leave a shorter JSON
            # document followed by stale bytes from a longer payload.
            tmp = path.with_name(
                f"{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
            )
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    _LOG.debug(
                        "activity-feed state fsync failed", exc_info=True)
            os.replace(tmp, path)
    except Exception:
        # Don't let HUD I/O ever break the spine.
        pass


class _NullLock:
    def __enter__(self):  # pragma: no cover - degenerate fallback
        return self

    def __exit__(self, *a):  # pragma: no cover
        return False


_NULL_LOCK = _NullLock()


def _flush_to_hud_state(force: bool = False) -> None:
    """Write the current feed snapshot to hud_state.json. Rate-limited."""
    global _LAST_WRITE_TS, _PENDING_FLUSH
    now = time.time()
    if not force and (now - _LAST_WRITE_TS) < _MIN_WRITE_INTERVAL_S:
        _PENDING_FLUSH = True
        return
    _LAST_WRITE_TS = now
    _PENDING_FLUSH = False
    with _LOCK:
        events = list(_RING)
        active_rid = events[-1]["request_id"] if events else ""
    payload = {
        "activity_feed": events,
        "activity_feed_active_request_id": active_rid,
        "activity_feed_updated_at": now,
    }
    _atomic_write_state(_resolve_hud_state_path(), payload)


def emit(
    kind: str,
    label: str,
    *,
    detail: str = "",
    source_url: str = "",
    source_title: str = "",
    request_id: str = "",
    provider: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Append one activity event to the feed and flush to HUD state.

    Always best-effort — never raises, never blocks for I/O beyond ~1ms.
    """
    try:
        event = {
            "id": uuid.uuid4().hex[:10],
            "ts": time.time(),
            "kind": str(kind or KIND_INFO),
            # Redact BEFORE truncation — a secret sliced by the length cap
            # would no longer match the redaction regexes.
            "label": _redact(str(label or ""))[:140],
            "detail": _redact(str(detail or ""))[:200],
            "request_id": str(request_id or ""),
        }
        if source_url:
            event["source_url"] = _redact(str(source_url))[:512]
            event["source_title"] = _redact(str(source_title or source_url))[:160]
        if provider:
            event["provider"] = str(provider)[:48]
        if extra and isinstance(extra, dict):
            for k, v in extra.items():
                if k in event:
                    continue
                if isinstance(v, str):
                    v = _redact(v)
                try:
                    json.dumps({k: v})
                    event[k] = v
                except Exception:
                    event[k] = _redact(str(v))[:200]
        with _LOCK:
            _RING.append(event)
        _flush_to_hud_state(force=(kind in (KIND_DONE, KIND_ERROR, KIND_BRAIN_FALLBACK, KIND_BRAIN_RESTORED)))
    except Exception:
        pass


def emit_sources(
    sources: List[Dict[str, Any]],
    *,
    request_id: str = "",
) -> None:
    """Convenience: emit one KIND_SOURCE event per source dict.

    Each source dict can carry ``url``, ``name`` / ``title``, and optional
    ``snippet`` / ``score``.
    """
    if not sources:
        return
    for s in sources:
        if not isinstance(s, dict):
            continue
        url = str(s.get("url") or "").strip()
        title = str(s.get("title") or s.get("name") or url)[:160]
        snippet = str(s.get("snippet") or "")[:200]
        if not url:
            continue
        emit(
            KIND_SOURCE,
            f"Read source",
            detail=title,
            source_url=url,
            source_title=title,
            request_id=request_id,
            extra={"snippet": snippet} if snippet else None,
        )


def publish_hud_flags(flags: Dict[str, Any]) -> None:
    """Publish presentation flags to hud_state.json under ``hud_flags``.

    The HUD reads these to pick clean vs raw (dev) rendering of the thinking
    panel. Merge-written under the shared write lock like every feed flush
    (``_atomic_write_state`` itself never raises); callers guard the rest.
    """
    if not isinstance(flags, dict) or not flags:
        return
    _atomic_write_state(_resolve_hud_state_path(), {"hud_flags": dict(flags)})


def snapshot() -> List[Dict[str, Any]]:
    """Return a copy of the current ring buffer."""
    with _LOCK:
        return list(_RING)


def clear() -> None:
    """Drop all events. Used on shutdown or HUD restart."""
    with _LOCK:
        _RING.clear()
    _flush_to_hud_state(force=True)


def flush_pending() -> None:
    """Force a write if a rate-limited flush was deferred."""
    if _PENDING_FLUSH:
        _flush_to_hud_state(force=True)


__all__ = [
    "emit", "emit_sources", "snapshot", "clear", "flush_pending",
    "publish_hud_flags",
    # Event kinds:
    "KIND_INTENT", "KIND_PLAN", "KIND_BRAIN_ROUTE", "KIND_BRAIN_FALLBACK",
    "KIND_BRAIN_RESTORED", "KIND_TOOL_CALL", "KIND_SEARCH", "KIND_SOURCE",
    "KIND_MEMORY", "KIND_SYNTHESIS", "KIND_SPEAK",
    "KIND_DONE", "KIND_ERROR", "KIND_INFO", "KIND_ZENITH", "KIND_MODE",
    "KIND_CONTEXT_INIT", "KIND_RETRIEVAL", "KIND_FILTER_RANK",
    "KIND_PACK_BUILT", "KIND_PACK_ENRICHED", "KIND_SPARK_RUN",
    "KIND_ZENITH_RUN", "KIND_MERGE_DONE",
]
