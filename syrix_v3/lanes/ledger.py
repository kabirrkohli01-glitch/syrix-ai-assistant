"""What is known about each lane right now, and whether it can take a request.

Admission reads the provider's own reported allowance where it publishes one
(Groq's ``x-ratelimit-remaining-tokens``), refilled at the provider's own
rate, and falls back to charging the reservation locally where it does not.
Day counters and dead keys survive a restart; cooldowns do not need to.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .. import config, state
from .catalogue import Lane, LaneError, Result
import syrix_obs

_CHARS_PER_TOKEN = 3.3

# HTTP statuses that mean this key cannot use this model at all.
_DEAD_STATUSES = frozenset({401, 402, 403, 404, 410})
_DEAD_FOR_S = 6 * 3600.0

_LEDGER_FILE = "lanes.json"



@dataclass
class _Live:
    """What is known about a lane right now."""

    dead_until: float = 0.0
    dead_reason: str = ""
    cooldown_until: float = 0.0
    cooldown_reason: str = ""
    failures: int = 0
    # Per-minute token bucket as last reported by the provider.
    tpm_remaining: float = -1.0
    tpm_seen_at: float = 0.0
    tools_broken: bool = False
    day: str = ""
    requests: int = 0
    tokens: int = 0
    last_ms: float = 0.0


_live: Dict[str, _Live] = {}
_live_lock = threading.Lock()
_loaded = False


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _load() -> None:
    """Restore day counters and dead lanes across restarts."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    saved = state.read(_LEDGER_FILE, "runtime", default={})
    now_wall = time.time()
    for key, row in (saved.get("lanes") or {}).items():
        if not isinstance(row, dict):
            continue
        live = _live.setdefault(key, _Live())
        if row.get("day") == _today():
            live.day = row["day"]
            live.requests = int(row.get("requests") or 0)
            live.tokens = int(row.get("tokens") or 0)
        dead_left = float(row.get("dead_until_wall") or 0) - now_wall
        if dead_left > 0:
            live.dead_until = time.monotonic() + dead_left
            live.dead_reason = str(row.get("dead_reason") or "")


def _save() -> None:
    now = time.monotonic()
    now_wall = time.time()
    rows = {}
    for key, live in _live.items():
        rows[key] = {
            "day": live.day, "requests": live.requests, "tokens": live.tokens,
            "dead_until_wall": (now_wall + (live.dead_until - now)) if live.dead_until > now else 0,
            "dead_reason": live.dead_reason if live.dead_until > now else "",
        }
    state.write({"saved_at": now_wall, "lanes": rows}, _LEDGER_FILE, "runtime")


def state_of(key: str) -> _Live:
    _load()
    live = _live.setdefault(key, _Live())
    if live.day != _today():
        live.day, live.requests, live.tokens = _today(), 0, 0
    return live


def api_key(lane: Lane) -> str:
    return config.secret(lane.key_env) if lane.key_env else ""


def configured(lane: Lane) -> bool:
    if lane.local:
        return True
    if not api_key(lane):
        return False
    disabled = config.get("v3_lanes_disabled", []) or []
    return lane.key not in disabled and lane.provider not in disabled


def estimate_tokens(messages: List[Dict[str, Any]]) -> int:
    chars = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            chars += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    chars += len(part["text"])
        if m.get("tool_calls"):
            chars += len(json.dumps(m["tool_calls"]))
    # Chat templates add a few tokens per message.
    return int(chars / _CHARS_PER_TOKEN) + 8 * len(messages)


def tpm_now(lane: Lane, live: _Live, now: float) -> float:
    """The lane's per-minute token bucket, refilled since it was last read."""
    if not lane.tpm or live.tpm_remaining < 0:
        return float("inf")
    refill = (now - live.tpm_seen_at) * (lane.tpm / 60.0)
    return min(float(lane.tpm), live.tpm_remaining + refill)


def admission(lane: Lane, cost: int, now: Optional[float] = None) -> Tuple[bool, float, str]:
    """Can this lane take a request costing ``cost`` tokens?

    Returns ``(possible, wait_s, reason)``. ``possible`` is False when waiting
    would not help within the day; ``wait_s`` is how long until it would be
    admitted when it is possible but not yet.
    """
    now = time.monotonic() if now is None else now
    if not configured(lane):
        return False, 0.0, "no key"
    live = state_of(lane.key)
    if live.dead_until > now:
        return False, 0.0, f"unavailable ({live.dead_reason})"
    if lane.rpd and live.requests >= int(lane.rpd * 0.97):
        return False, 0.0, "daily requests spent"
    if lane.tpd and live.tokens + cost >= int(lane.tpd * 0.97):
        return False, 0.0, "daily tokens spent"
    if lane.tpm and cost > lane.tpm * 0.95:
        return False, 0.0, "request larger than the lane's minute"
    wait = 0.0
    if live.cooldown_until > now:
        wait = live.cooldown_until - now
    if lane.tpm:
        have = tpm_now(lane, live, now)
        if have < cost:
            wait = max(wait, (cost - have) / (lane.tpm / 60.0))
    return True, wait, (live.cooldown_reason if live.cooldown_until > now else "minute allowance")


def note_success(lane: Lane, result: Result, headers: Dict[str, str], cost: int) -> None:
    with _live_lock:
        live = state_of(lane.key)
        live.failures = 0
        live.cooldown_until = 0.0
        live.requests += 1
        used = (result.prompt_tokens + result.completion_tokens) or cost
        live.tokens += used
        live.last_ms = result.latency_ms
        remaining = headers.get("x-ratelimit-remaining-tokens")
        if remaining is not None:
            try:
                live.tpm_remaining = float(remaining)
                live.tpm_seen_at = time.monotonic()
            except ValueError as exc:
                syrix_obs.report("v3.lanes.ledger.note_success", exc, level="debug")
        elif lane.tpm:
            # No header this time: charge the reservation ourselves.
            have = tpm_now(lane, live, time.monotonic())
            live.tpm_remaining = max(0.0, (have if have != float("inf") else lane.tpm) - cost)
            live.tpm_seen_at = time.monotonic()
        _save()


def note_admitted(lane: Lane, cost: int) -> None:
    """Charge the minute at send time, as the provider does.

    Without this, two calls started in the same second both see a full
    bucket, and the second is refused by the provider instead of being
    routed elsewhere here.
    """
    if not lane.tpm:
        return
    with _live_lock:
        live = state_of(lane.key)
        now = time.monotonic()
        have = tpm_now(lane, live, now)
        base = lane.tpm if have == float("inf") else have
        live.tpm_remaining = max(0.0, base - cost)
        live.tpm_seen_at = now


_NOT_A_FAULT = frozenset({"empty reply", "the reply allowance went on thinking"})


_OVERLOAD_PAUSE_S = 2.5


def overloaded(err: LaneError) -> bool:
    """A provider shedding load for a moment rather than refusing the lane."""
    reason = str(getattr(err, "reason", "") or "").lower()
    return getattr(err, "status", 0) in (502, 503, 504) or "overloaded" in reason


def note_failure(lane: Lane, err: LaneError) -> None:
    with _live_lock:
        live = state_of(lane.key)
        now = time.monotonic()
        if err.tools_broken:
            live.tools_broken = True
            return
        if err.too_large:
            return  # this request, not this lane
        if err.status in _DEAD_STATUSES:
            live.dead_until = now + _DEAD_FOR_S
            live.dead_reason = f"HTTP {err.status}"
            _save()
            return
        if err.status == 429:
            wait = err.retry_after_s or 20.0
            live.cooldown_until = now + wait
            live.cooldown_reason = "rate limited"
            if lane.tpm:
                live.tpm_remaining = 0.0
                live.tpm_seen_at = now
            return
        if overloaded(err):
            # "Service temporarily overloaded" is the provider shedding load
            # for a moment, not the lane failing. Counted as a fault, it
            # cooled NVIDIA's main lane for 20s and then 40s while Groq was
            # also out, and the turns in between had nothing left to answer
            # them; asked again seconds later, the same lane answered in 1.5s.
            live.cooldown_until = now + _OVERLOAD_PAUSE_S
            live.cooldown_reason = "briefly overloaded"
            return
        if err.reason in _NOT_A_FAULT:
            # The model chose to say nothing this once — measured on Ultra,
            # which with tools offered sometimes streams a newline and stops.
            # That is about the request, not the lane's health; cooling the
            # lane for it took healthy lanes out one after another until a
            # turn had none left.
            return
        live.failures += 1
        live.cooldown_until = now + min(300.0, 20.0 * (2 ** (live.failures - 1)))
        live.cooldown_reason = err.reason[:60]


def reset() -> None:
    """Forget everything known about every lane. For tests."""
    global _loaded
    with _live_lock:
        _live.clear()
        _loaded = True
