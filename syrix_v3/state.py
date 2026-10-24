"""Small atomic JSON store under the runtime state directory.

Every durable thing v3 writes — threads, facts, counters — goes through here,
so there is one answer to where state lives, one write that cannot leave a
half-file behind, and one place to look when something on disk is wrong.

Paths resolve through ``syrix_paths`` so v3 writes beside the rest of the
runtime rather than inventing a second location, and so a test pointing
``SYRIX_STATE_DIR`` at a temporary directory moves v3's files with everything
else.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from typing import Any, Dict, Optional
import syrix_obs

_locks: Dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

_SUBDIR = "v3"


def _lock_for(path: str) -> threading.Lock:
    key = os.path.normcase(os.path.abspath(path))
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


def directory(*parts: str) -> str:
    """A directory under the runtime state root, created if absent."""
    try:
        import syrix_paths
        base = syrix_paths.state_dir()
    except Exception:
        base = os.path.join(os.path.expanduser("~"), ".syrix", "state")
    path = os.path.join(base, _SUBDIR, *parts)
    os.makedirs(path, exist_ok=True)
    return path


def path_for(name: str, *parts: str) -> str:
    return os.path.join(directory(*parts), name)


def read(name: str, *parts: str, default: Optional[Any] = None) -> Any:
    target = path_for(name, *parts)
    try:
        with open(target, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {} if default is None else default


def write(payload: Any, name: str, *parts: str) -> bool:
    """Replace a file atomically. Never raises; returns whether it landed.

    A partially written state file is worse than a stale one — the reader
    cannot tell the difference and fails on parse — so the content is written
    beside the target and moved into place.
    """
    target = path_for(name, *parts)
    lock = _lock_for(target)
    tmp_name = ""
    with lock:
        try:
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{name}.", suffix=".tmp", dir=os.path.dirname(target)
            )
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp_name, target)
            return True
        except Exception:
            if tmp_name and os.path.exists(tmp_name):
                try:
                    os.unlink(tmp_name)
                except OSError as exc:
                    syrix_obs.report("v3.state.write", exc, level="debug")
            return False
