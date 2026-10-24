"""Connector audit log — every interaction, append-only JSONL.

One line per event at ``%LOCALAPPDATA%\\SYRIX\\connectors\\audit.jsonl``:

.. code-block:: json

    {"ts": 1770000000.0, "connector": "gmail", "action": "gmail_search",
     "actor": "brain", "result": "ok", "reason": "find invoices",
     "latency_ms": 412, "count": 3, "request_id": "abc123"}

Rules:

* ``reason`` passes through :mod:`syrix_redact` before it is written and is
  truncated — a secret echoed into a query can never land here verbatim.
* Message bodies / file contents / tokens are **never** written; callers pass
  counts and short labels only.
* Rotation: when the file exceeds ~2 MB it is renamed to ``audit.jsonl.1``
  (single generation — enough history for the HUD, bounded disk).
* ``recent()`` feeds the HUD Activity panel via the state snapshot.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

import syrix_obs as obs

from . import paths

_LOCK = threading.RLock()
_MAX_BYTES = 2 * 1024 * 1024
_TAIL_READ_BYTES = 256 * 1024

ACTOR_BRAIN = "brain"        # tool call issued by the AI during a chat/voice turn
ACTOR_HUD = "hud"            # manual action from the Connectors panel
ACTOR_SCHEDULER = "scheduler"  # background task

RESULT_OK = "ok"
RESULT_DENIED = "permission_denied"
RESULT_BLOCKED = "not_implemented"
RESULT_NOT_CONNECTED = "not_connected"
RESULT_ERROR = "error"
RESULT_TIMEOUT = "timeout"
RESULT_CONFIRM_PENDING = "confirmation_requested"
RESULT_CONFIRM_DENIED = "confirmation_denied"
RESULT_CONFIRM_EXPIRED = "confirmation_expired"


def _redact(text: str) -> str:
    if not text:
        return ""
    try:
        from syrix_redact import redact_text  # type: ignore

        return redact_text(str(text))
    except Exception:
        return str(text)


def record(
    connector: str,
    action: str,
    *,
    actor: str,
    result: str,
    reason: str = "",
    detail: str = "",
    latency_ms: float = 0.0,
    count: Optional[int] = None,
    request_id: str = "",
) -> None:
    """Append one audit line. Best-effort — never raises into the caller."""
    entry: Dict[str, Any] = {
        "ts": round(time.time(), 3),
        "connector": str(connector or "")[:40],
        "action": str(action or "")[:60],
        "actor": str(actor or "")[:16],
        "result": str(result or "")[:32],
    }
    if reason:
        entry["reason"] = _redact(reason)[:160]
    if detail:
        entry["detail"] = _redact(detail)[:200]
    if latency_ms:
        entry["latency_ms"] = int(latency_ms)
    if count is not None:
        entry["count"] = int(count)
    if request_id:
        entry["request_id"] = str(request_id)[:40]
    with _LOCK:
        try:
            p = paths.audit_path()
            _rotate_if_needed(p)
            with open(p, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            obs.report("connectors.audit.record", exc, connector=connector, action=action)


def _rotate_if_needed(p: str) -> None:
    try:
        if os.path.isfile(p) and os.path.getsize(p) > _MAX_BYTES:
            bak = p + ".1"
            if os.path.isfile(bak):
                os.remove(bak)
            os.replace(p, bak)
    except Exception as exc:
        obs.report("connectors.audit.rotate", exc)


def recent(limit: int = 50, connector: str = "") -> List[Dict[str, Any]]:
    """Return the newest ``limit`` entries (newest first), optionally filtered."""
    limit = max(1, min(int(limit or 50), 200))
    cid = str(connector or "").strip().lower()
    with _LOCK:
        try:
            p = paths.audit_path()
            if not os.path.isfile(p):
                return []
            size = os.path.getsize(p)
            with open(p, "rb") as fh:
                if size > _TAIL_READ_BYTES:
                    fh.seek(size - _TAIL_READ_BYTES)
                    fh.readline()  # drop the partial first line
                raw = fh.read().decode("utf-8", errors="replace")
        except Exception as exc:
            obs.report("connectors.audit.recent", exc)
            return []
    out: List[Dict[str, Any]] = []
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        if not isinstance(e, dict):
            continue
        if cid and str(e.get("connector", "")).lower() != cid:
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out


__all__ = [
    "record", "recent",
    "ACTOR_BRAIN", "ACTOR_HUD", "ACTOR_SCHEDULER",
    "RESULT_OK", "RESULT_DENIED", "RESULT_BLOCKED", "RESULT_NOT_CONNECTED",
    "RESULT_ERROR", "RESULT_TIMEOUT",
    "RESULT_CONFIRM_PENDING", "RESULT_CONFIRM_DENIED", "RESULT_CONFIRM_EXPIRED",
]
