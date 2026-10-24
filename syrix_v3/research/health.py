"""Which keyed search lanes are out of service, and until when.

A keyed lane that has spent its free allowance fails every request until the
allowance renews. Racing it anyway costs nothing in credit but a round trip
and a slot in the search window on every turn, and a lane rejecting its key
will not recover by being asked again. So a failure that says the lane is out
— quota spent, key refused, rate limited — takes it out of the race for a
while, and the turn is searched with what remains. Timeouts and server errors
are not recorded: those are one slow request, not a lane that is gone.

The record lives in the runtime state directory so a restart does not spend
the first turn of every session rediscovering it.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Dict

from .. import state

_FILE = "search_lanes.json"
_lock = threading.Lock()
_cache: Dict[str, Dict[str, Any]] = {}
_loaded = False

# How long each kind of outage keeps a lane out. A spent monthly allowance is
# rechecked a few times a day rather than assumed to last the month: plans
# get upgraded and allowances get topped up.
_QUOTA_S = 6 * 3600
_KEY_S = 6 * 3600
_RATE_S = 90

_QUOTA_WORDS = re.compile(r"usage limit|quota|credits?|exceeded|plan limit|insufficient", re.I)


def _load() -> None:
    global _loaded
    if not _loaded:
        stored = state.read(_FILE, "runtime", default={})
        if isinstance(stored, dict):
            _cache.update({k: v for k, v in stored.items() if isinstance(v, dict)})
        _loaded = True


def classify(exc: BaseException) -> str:
    """"quota", "key", "rate", or "" for a failure that says nothing lasting."""
    response = getattr(exc, "response", None)
    status = int(getattr(response, "status_code", 0) or 0)
    try:
        body = str(getattr(response, "text", "") or "")[:600]
    except Exception:
        body = ""
    if status in (402, 432, 433) or (status in (400, 403, 429) and _QUOTA_WORDS.search(body)):
        return "quota"
    if status in (401, 403):
        return "key"
    if status == 429:
        return "rate"
    return ""


def note_failure(lane: str, exc: BaseException) -> str:
    """Record a lasting failure. Returns its kind, or "" when nothing was recorded."""
    kind = classify(exc)
    if not kind:
        return ""
    hold = {"quota": _QUOTA_S, "key": _KEY_S, "rate": _RATE_S}[kind]
    with _lock:
        _load()
        _cache[lane] = {"until": time.time() + hold, "why": kind,
                        "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        state.write(dict(_cache), _FILE, "runtime")
    return kind


def down(lane: str) -> bool:
    with _lock:
        _load()
        row = _cache.get(lane)
        return bool(row) and float(row.get("until", 0) or 0) > time.time()


def status() -> Dict[str, Dict[str, Any]]:
    """The lanes currently out, with why and until when."""
    with _lock:
        _load()
        now = time.time()
        return {k: dict(v) for k, v in _cache.items() if float(v.get("until", 0) or 0) > now}


def reset() -> None:
    with _lock:
        _cache.clear()
        state.write({}, _FILE, "runtime")


__all__ = ["classify", "note_failure", "down", "status", "reset"]
