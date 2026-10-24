"""Shared in-process lock for hud_state.json read-merge-write cycles.

2026-07-02 — hud_state.json had (at least) two concurrent read-merge-replace
writers inside the backend process: ``activity_feed._atomic_write_state`` and
the live ``write_hud_state`` in syrix_host (which re-reads feed keys from
disk and merges them back). A feed flush landing between the other writer's
read and replace was silently lost — the classic TOCTOU that made trace
steps drop or arrive in batches. Both writers now hold this lock across
their whole read-merge-write cycle.

(The Electron main process also occasionally merges hud-app acks into the
file from another OS process; a threading.Lock can't cover that, but those
writes are rare and small — the hot race was in-process.)
"""

from __future__ import annotations

import threading

HUD_STATE_WRITE_LOCK = threading.RLock()

__all__ = ["HUD_STATE_WRITE_LOCK"]
