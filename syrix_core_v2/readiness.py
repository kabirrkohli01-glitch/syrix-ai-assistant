"""Backend readiness gate.

The spine refuses to accept new requests until ``mark_ready()`` has been
called. While the gate is closed it returns a structured ``starting`` reply
so the HUD never silently swallows user input or fakes "online" state.

This is intentionally lighter than a generic state machine — it has three
states and one timestamp.
"""

from __future__ import annotations

import threading
import time
from typing import Dict


_LOCK = threading.RLock()
_STATUS = "starting"
_REASON = ""
_TS = 0.0
_READY_AT = 0.0


def mark_starting(reason: str = "") -> None:
    global _STATUS, _REASON, _TS
    with _LOCK:
        _STATUS = "starting"
        _REASON = str(reason or "")
        _TS = time.time()


def mark_ready() -> None:
    global _STATUS, _REASON, _TS, _READY_AT
    with _LOCK:
        _STATUS = "ready"
        _REASON = ""
        _TS = time.time()
        if _READY_AT == 0.0:
            _READY_AT = _TS


def mark_degraded(reason: str) -> None:
    global _STATUS, _REASON, _TS
    with _LOCK:
        _STATUS = "degraded"
        _REASON = str(reason or "unknown")
        _TS = time.time()


def is_ready() -> bool:
    with _LOCK:
        return _STATUS in ("ready", "degraded")


def status() -> Dict[str, str]:
    with _LOCK:
        return {
            "status": _STATUS,
            "reason": _REASON,
            "ts": str(_TS),
            "ready_at": str(_READY_AT),
        }


__all__ = ["mark_starting", "mark_ready", "mark_degraded", "is_ready", "status"]
