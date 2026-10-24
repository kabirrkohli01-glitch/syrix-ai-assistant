"""Quota ledger — local accounting of every free-tier budget.

Sliding 60-second windows for RPM/TPM, UTC-midnight-reset counters for
RPD/TPD, persisted atomically to %LOCALAPPDATA%\\SYRIX\\state\\brain_ledger.json
so restarts can't double-spend a day's budget. The pool consults
``headroom()`` BEFORE each call — rotation happens pre-emptively, a 429 is
the backstop, never the plan.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import syrix_paths

from .providers import Caps

_WINDOW_S = 60.0
_PERSIST_DEBOUNCE_S = 2.0
# Rotate before the budget is truly gone: leave this fraction unspent.
_SAFETY_MARGIN = 0.10


def _utc_day() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


class QuotaLedger:
    def __init__(self, path: Optional[str] = None):
        self._path = path or os.path.join(syrix_paths.state_dir(), "brain_ledger.json")
        self._lock = threading.Lock()
        # key -> list[(monotonic_ts, tokens)] for the sliding window
        self._events: Dict[str, List[Tuple[float, int]]] = {}
        # key -> {"day": "...", "requests": n, "tokens": n}
        self._daily: Dict[str, Dict[str, Any]] = {}
        self._last_persist = 0.0
        self._load()

    # ------------------------------------------------------------ persistence
    def _load(self) -> None:
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            daily = data.get("daily") or {}
            today = _utc_day()
            for key, entry in daily.items():
                if isinstance(entry, dict) and entry.get("day") == today:
                    self._daily[key] = {
                        "day": today,
                        "requests": int(entry.get("requests") or 0),
                        "tokens": int(entry.get("tokens") or 0),
                    }
        except Exception:
            pass

    def _persist(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_persist) < _PERSIST_DEBOUNCE_S:
            return
        self._last_persist = now
        tmp = f"{self._path}.{os.getpid()}.tmp"
        try:
            payload = {"daily": self._daily, "saved_at": time.time()}
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, self._path)
        except Exception:
            try:
                if os.path.isfile(tmp):
                    os.unlink(tmp)
            except Exception:
                pass

    # ------------------------------------------------------------ accounting
    def _trim(self, key: str, now: float) -> None:
        events = self._events.get(key)
        if events:
            cutoff = now - _WINDOW_S
            self._events[key] = [e for e in events if e[0] >= cutoff]

    def _daily_entry(self, key: str) -> Dict[str, Any]:
        today = _utc_day()
        entry = self._daily.get(key)
        if not isinstance(entry, dict) or entry.get("day") != today:
            entry = {"day": today, "requests": 0, "tokens": 0}
            self._daily[key] = entry
        return entry

    def record(self, key: str, tokens: int) -> None:
        with self._lock:
            now = time.monotonic()
            self._events.setdefault(key, []).append((now, max(0, int(tokens))))
            self._trim(key, now)
            entry = self._daily_entry(key)
            entry["requests"] += 1
            entry["tokens"] += max(0, int(tokens))
            self._persist()

    def headroom(self, key: str, caps: Caps, est_tokens: int,
                 reserve: float = 0.0) -> Tuple[bool, str]:
        """Would one more call of ``est_tokens`` stay inside the budget
        (keeping the safety margin unspent)? Returns (ok, reason).

        ``reserve`` is a fraction of the DAILY budget this caller must leave
        untouched — a courtesy limit, not a hard cap. Supercomputer passes one
        so a long agentic task cannot spend the whole day's free tokens and
        leave ordinary chat on a local 7B model: the mode stops using a lane
        with a quarter of the day left in it, and normal chat, which passes no
        reserve, can still use every last token. Per-minute limits are not
        reserved — those refill in sixty seconds and reserving them would
        throttle for no benefit.
        """
        with self._lock:
            now = time.monotonic()
            self._trim(key, now)
            events = self._events.get(key, [])
            window_requests = len(events)
            window_tokens = sum(t for _, t in events)
            entry = self._daily_entry(key)

            def _limit(cap: int) -> float:
                return cap * (1.0 - _SAFETY_MARGIN)

            def _daily_limit(cap: int) -> float:
                # Clamped so a mis-set reserve can never take the lane away
                # entirely: with the 10% safety margin on top, 0.8 still leaves
                # a tenth of the day usable. A courtesy limit that locks the
                # caller out completely is a bug, not a stricter courtesy.
                held = min(max(float(reserve or 0.0), 0.0), 0.8)
                return cap * (1.0 - _SAFETY_MARGIN - held)

            if caps.rpm and (window_requests + 1) > _limit(caps.rpm):
                return False, "rpm"
            if caps.tpm and (window_tokens + est_tokens) > _limit(caps.tpm):
                return False, "tpm"
            if caps.rpd and (entry["requests"] + 1) > _daily_limit(caps.rpd):
                return False, "rpd"
            if caps.tpd and (entry["tokens"] + est_tokens) > _daily_limit(caps.tpd):
                return False, "tpd"
            return True, ""

    def snapshot(self, key: str, caps: Caps) -> Dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            self._trim(key, now)
            events = self._events.get(key, [])
            entry = self._daily_entry(key)
            out: Dict[str, Any] = {
                "rpm_used": len(events),
                "tpm_used": sum(t for _, t in events),
                "rpd_used": entry["requests"],
                "tpd_used": entry["tokens"],
            }
            for name, cap in (("rpm", caps.rpm), ("tpm", caps.tpm), ("rpd", caps.rpd), ("tpd", caps.tpd)):
                if cap:
                    used = out[f"{name}_used"]
                    out[f"{name}_cap"] = cap
                    out[f"{name}_pct"] = round(100.0 * used / cap, 1)
            return out

    def flush(self) -> None:
        with self._lock:
            self._persist(force=True)


def estimate_tokens(messages: Any, max_tokens: int) -> int:
    """Conservative request-cost estimate: prompt chars/3.5 + the reply budget."""
    chars = 0
    try:
        for m in messages or []:
            content = m.get("content")
            if isinstance(content, str):
                chars += len(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        chars += len(str(block.get("text") or ""))
    except Exception:
        chars = 2000
    return int(chars / 3.5) + max(0, int(max_tokens))
