"""Append-only audit trail for every gateway fetch.

Enough to debug a bad answer ("which tier read that page, how long did it take,
why did it stop"), and deliberately not enough to leak anything. What is
recorded is **shape, not content**: host, tier, milliseconds, byte count,
outcome. No page bodies, no cookies, no tokens, no query strings — a URL's
query is where session ids and API keys hide, so only scheme+host+path survive.

Every free-text field still passes through :func:`syrix_redact.redact_text` on
the way out, because "we only log the path" is one careless caller away from
being false.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

import syrix_obs as obs

_LOCK = threading.Lock()
_FILENAME = "web_gateway.jsonl"
_MAX_BYTES = 4 * 1024 * 1024


def safe_url(url: str) -> str:
    """scheme://host/path — query and fragment dropped, not redacted.

    Redacting a query string is guesswork; dropping it is not.
    """
    try:
        parts = urlsplit(str(url or ""))
    except Exception:
        return ""
    if not parts.hostname:
        return ""
    path = (parts.path or "")[:200]
    return f"{parts.scheme}://{parts.hostname}{path}"


def _redact(text: str) -> str:
    try:
        import syrix_redact

        return syrix_redact.redact_text(str(text or ""))
    except Exception as exc:
        obs.report("web.audit.redact", exc, level="debug")
        return ""


def _path() -> str:
    import syrix_paths

    return os.path.join(syrix_paths.logs_dir(), _FILENAME)


def _rotate_if_large(path: str) -> None:
    try:
        if os.path.getsize(path) < _MAX_BYTES:
            return
        old = path + ".1"
        if os.path.exists(old):
            os.remove(old)
        os.replace(path, old)
    except FileNotFoundError:
        return
    except Exception as exc:
        obs.report("web.audit.rotate", exc, level="debug")


def record(
    event: str,
    *,
    url: str = "",
    tier: str = "",
    mode: str = "",
    ok: bool = True,
    reason: str = "",
    ms: float = 0.0,
    chars: int = 0,
    request_id: str = "",
    **extra: Any,
) -> None:
    """Write one line. Never raises — an audit failure must not fail a fetch."""
    entry: Dict[str, Any] = {
        "ts": round(time.time(), 3),
        "event": str(event)[:40],
        "ok": bool(ok),
    }
    if url:
        entry["url"] = safe_url(url)
    if tier:
        entry["tier"] = str(tier)[:24]
    if mode:
        entry["mode"] = str(mode)[:24]
    if reason:
        entry["reason"] = _redact(reason)[:160]
    if ms:
        entry["ms"] = round(float(ms), 1)
    if chars:
        entry["chars"] = int(chars)
    if request_id:
        entry["rid"] = str(request_id)[:40]
    for key, value in extra.items():
        if value is None:
            continue
        entry[str(key)[:24]] = (
            _redact(value)[:160] if isinstance(value, str) else value
        )

    try:
        path = _path()
        with _LOCK:
            _rotate_if_large(path)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        obs.report("web.audit.write", exc, level="debug", event=event)


def feed(
    label: str,
    *,
    detail: str = "",
    kind: str = "info",
    request_id: str = "",
    source_url: str = "",
    source_title: str = "",
) -> None:
    """Mirror an event into the existing HUD activity feed.

    Uses the feed SYRIX already has (``syrix_core_v2.activity_feed``) rather
    than a second status channel — the source pills and thinking ladder in the
    HUD read from there and need no change to show browser reads.
    """
    try:
        from syrix_core_v2 import activity_feed

        activity_feed.emit(
            kind,
            label,
            detail=detail,
            request_id=request_id,
            source_url=source_url,
            source_title=source_title,
        )
    except Exception as exc:
        obs.report("web.audit.feed", exc, level="debug", label=label)


def searching(url: str, *, request_id: str = "") -> None:
    """Announce the domain SYRIX is about to open, before it opens it.

    The HUD shows one small grey line under the thinking animation while this
    is the newest such event — "Searching openai.com". It has to be emitted
    BEFORE the fetch, not after: a line that appears once the page has already
    been read tells you about the past.

    The domain is carried in ``extra.web_domain`` as well as the detail text so
    the renderer can find it exactly, rather than parsing a label.
    """
    host = ""
    try:
        from . import policy

        host = policy.extract_host(url)
    except Exception as exc:
        obs.report("web.audit.searching_host", exc, level="debug")
    if not host:
        return
    try:
        from syrix_core_v2 import activity_feed

        activity_feed.emit(
            activity_feed.KIND_SEARCH,
            "Searching",
            detail=host,
            request_id=request_id,
            extra={"web_domain": host},
        )
    except Exception as exc:
        obs.report("web.audit.searching", exc, level="debug", host=host)


def log_path() -> str:
    """Where the trail lives — for the report, and for tests."""
    try:
        return _path()
    except Exception:
        return ""


__all__ = ["feed", "log_path", "record", "safe_url", "searching"]
