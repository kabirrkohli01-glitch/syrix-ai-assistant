"""Exact-match reply cache (sqlite, TTL) — repeated questions cost zero quota.

Keyed on sha256(tier + canonical messages). Skipped for tool calls and
streams. Lives in %LOCALAPPDATA%\\SYRIX\\cache\\brain_cache.sqlite.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from typing import Any, List, Optional

import syrix_paths

_DEFAULT_TTL_S = 600.0
_MAX_ROWS = 500


class ReplyCache:
    def __init__(self, path: Optional[str] = None, ttl_s: float = _DEFAULT_TTL_S):
        self._path = path or os.path.join(syrix_paths.cache_dir(), "brain_cache.sqlite")
        self._ttl = float(ttl_s)
        self._lock = threading.Lock()
        self._ok = True
        try:
            self._init_db()
        except Exception:
            self._ok = False

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=2.0)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS replies ("
                " key TEXT PRIMARY KEY, reply TEXT NOT NULL,"
                " provider TEXT, model TEXT, ts REAL NOT NULL)"
            )

    @staticmethod
    def key_for(tier: str, messages: List[Any]) -> str:
        try:
            canon = json.dumps(
                [{"r": m.get("role"), "c": m.get("content")} for m in messages or []],
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
        except Exception:
            canon = str(messages)
        return hashlib.sha256(f"{tier}|{canon}".encode("utf-8", errors="replace")).hexdigest()

    def get(self, key: str) -> Optional[str]:
        if not self._ok:
            return None
        try:
            with self._lock, self._conn() as conn:
                row = conn.execute("SELECT reply, ts FROM replies WHERE key=?", (key,)).fetchone()
            if not row:
                return None
            reply, ts = row
            if (time.time() - float(ts)) > self._ttl:
                return None
            return str(reply)
        except Exception:
            return None

    def put(self, key: str, reply: str, provider: str = "", model: str = "") -> None:
        if not self._ok or not (reply or "").strip():
            return
        try:
            with self._lock, self._conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO replies (key, reply, provider, model, ts) VALUES (?,?,?,?,?)",
                    (key, reply, provider, model, time.time()),
                )
                # Bound growth: keep the newest _MAX_ROWS rows.
                conn.execute(
                    "DELETE FROM replies WHERE key NOT IN "
                    "(SELECT key FROM replies ORDER BY ts DESC LIMIT ?)",
                    (_MAX_ROWS,),
                )
        except Exception:
            pass
