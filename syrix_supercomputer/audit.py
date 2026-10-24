"""Append-only audit of every gated action and permission decision.

Local only, never sent anywhere. Records what was attempted, what was decided
and why — never message bodies, page content, typed text, or credentials.

Redaction goes through ``syrix_redact`` where available so the same secret
patterns the logs and HUD scrub are scrubbed here too.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

import syrix_obs as obs

from . import paths

RESULT_ALLOWED = "allowed"
RESULT_DENIED = "denied"
RESULT_BLOCKED = "blocked"
RESULT_DECLINED = "declined"
RESULT_DISCUSS = "discuss"
RESULT_PENDING = "confirm_pending"
RESULT_EXPIRED = "confirm_expired"
RESULT_ERROR = "error"
RESULT_TIMEOUT = "timeout"

_LOCK = threading.Lock()
_MAX_BYTES = 2 * 1024 * 1024


def _redact(text: str) -> str:
    s = str(text or "")
    try:
        import syrix_redact

        fn = getattr(syrix_redact, "redact_text", None)
        if callable(fn):
            return str(fn(s))
    except Exception as exc:
        obs.report("supercomputer.audit.redact", exc, level="debug")
    return s


def record(
    action: str,
    result: str,
    *,
    task_id: str = "",
    scope: str = "",
    reason: str = "",
    actor: str = "agent",
    request_id: str = "",
    **extra: Any,
) -> None:
    """Append one entry. Never raises — auditing must not break the action."""
    entry: Dict[str, Any] = {
        "ts": time.time(),
        "action": str(action or "")[:80],
        "result": str(result or "")[:40],
        "task_id": str(task_id or "")[:32],
        # Scope is a domain / path prefix / verb, never content.
        "scope": _redact(scope)[:200],
        "reason": _redact(reason)[:200],
        "actor": str(actor or "")[:24],
        "request_id": str(request_id or "")[:40],
    }
    for k, v in extra.items():
        if v is not None and k not in entry:
            entry[k] = v if isinstance(v, (int, float, bool)) else _redact(str(v))[:160]
    try:
        path = paths.audit_path()
        with _LOCK:
            _rotate_if_needed(path)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        obs.report("supercomputer.audit.write", exc, action=str(action))


def _rotate_if_needed(path: str) -> None:
    try:
        if os.path.isfile(path) and os.path.getsize(path) > _MAX_BYTES:
            os.replace(path, path + ".1")
    except Exception as exc:
        obs.report("supercomputer.audit.rotate", exc)


def recent(limit: int = 50, *, task_id: str = "") -> List[Dict[str, Any]]:
    """Tail of the audit, newest last. Used by the workspace's activity view."""
    path = paths.audit_path()
    out: List[Dict[str, Any]] = []
    try:
        if not os.path.isfile(path):
            return out
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            # 256 KB tail is plenty and keeps this cheap on a rotated file.
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 262144))
            lines = fh.read().splitlines()
        for line in lines:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            if task_id and str(item.get("task_id") or "") != str(task_id):
                continue
            out.append(item)
    except Exception as exc:
        obs.report("supercomputer.audit.read", exc)
    return out[-max(1, int(limit)):]


__all__ = [
    "record", "recent",
    "RESULT_ALLOWED", "RESULT_DENIED", "RESULT_BLOCKED", "RESULT_DECLINED",
    "RESULT_DISCUSS", "RESULT_PENDING", "RESULT_EXPIRED", "RESULT_ERROR",
    "RESULT_TIMEOUT",
]
