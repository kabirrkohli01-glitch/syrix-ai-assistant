"""Granular per-action permission + task-toggle store (non-secret state).

One JSON document at ``%LOCALAPPDATA%\\SYRIX\\connectors\\connectors_state.json``:

.. code-block:: json

    {
      "schema_version": 1,
      "connectors": {
        "gmail": {
          "connected": true,
          "account": "kab…@gmail.com",
          "connected_at": 1770000000.0,
          "last_sync": 1770000123.0,
          "health_override": "",
          "last_error": "",
          "permissions": {"gmail_list_unread": "allowed", "gmail_send": "blocked"},
          "tasks": {"inbox_digest": false}
        }
      }
    }

Rules enforced here:

* **Deny unknown** — an action key with no explicit entry is blocked.
* **Conservative defaults on connect** — safe verbs (read/search/summarize)
  seed as ``allowed``; destructive verbs and ``not_implemented`` actions seed
  as ``blocked``.
* ``not_implemented`` actions can never be flipped to allowed.
* Writes are atomic (tmp + ``os.replace``) under a process-wide lock; the
  file carries ``schema_version`` — a file written by a NEWER framework is
  treated read-only-defaults instead of being clobbered.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, Optional

import syrix_obs as obs

from . import paths
from .base import (
    RISK_NOT_IMPLEMENTED,
    SAFE_VERBS,
    SCHEMA_VERSION,
    ConnectorAction,
    ConnectorBase,
)

_LOCK = threading.RLock()

ALLOWED = "allowed"
BLOCKED = "blocked"


def _empty_state() -> Dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "connectors": {}}


def load_state() -> Dict[str, Any]:
    """Read the state file. Malformed/newer-version files → in-memory defaults."""
    with _LOCK:
        try:
            p = paths.state_path()
            if not os.path.isfile(p):
                return _empty_state()
            with open(p, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
            if not isinstance(doc, dict) or not isinstance(doc.get("connectors"), dict):
                return _empty_state()
            if int(doc.get("schema_version") or 0) > SCHEMA_VERSION:
                # Written by a newer SYRIX — read what we understand, never write back.
                doc["_read_only"] = True
            return doc
        except Exception as exc:
            obs.report("connectors.permissions.load", exc)
            return _empty_state()


def _write_state(doc: Dict[str, Any]) -> bool:
    if doc.get("_read_only"):
        return False
    doc["schema_version"] = SCHEMA_VERSION
    try:
        p = paths.state_path()
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        return True
    except Exception as exc:
        obs.report("connectors.permissions.write", exc)
        return False


def _mutate(connector_id: str, fn) -> bool:
    """Load → mutate the per-connector record via ``fn(rec)`` → atomic write."""
    cid = str(connector_id or "").strip().lower()
    if not cid:
        return False
    with _LOCK:
        doc = load_state()
        rec = doc["connectors"].setdefault(cid, {})
        if not isinstance(rec, dict):
            rec = {}
            doc["connectors"][cid] = rec
        fn(rec)
        return _write_state(doc)


def connector_record(connector_id: str) -> Dict[str, Any]:
    cid = str(connector_id or "").strip().lower()
    rec = load_state()["connectors"].get(cid)
    return rec if isinstance(rec, dict) else {}


def is_connected(connector_id: str) -> bool:
    return bool(connector_record(connector_id).get("connected"))


def default_permission_state(action: ConnectorAction) -> str:
    """Seed state for one action: safe verbs allowed, everything else blocked
    (unless the action carries an explicit ``default_allowed`` product
    decision — e.g. Gmail draft creation, which never sends anything)."""
    if action.risk == RISK_NOT_IMPLEMENTED:
        return BLOCKED
    if action.verb in SAFE_VERBS or action.default_allowed:
        return ALLOWED
    return BLOCKED


def set_connected(connector: ConnectorBase, account_label: str) -> bool:
    """Mark connected and seed permission/task defaults for missing keys only.

    Re-connecting preserves any toggles the user already customised.
    """
    now = time.time()
    actions = connector.actions()
    tasks = connector.background_tasks

    def _apply(rec: Dict[str, Any]) -> None:
        rec["connected"] = True
        rec["account"] = str(account_label or "")[:120]
        rec["connected_at"] = rec.get("connected_at") or now
        rec["last_error"] = ""
        rec["health_override"] = ""
        perms = rec.setdefault("permissions", {})
        if not isinstance(perms, dict):
            perms = {}
            rec["permissions"] = perms
        for key, act in actions.items():
            if key not in perms:
                perms[key] = default_permission_state(act)
            if act.risk == RISK_NOT_IMPLEMENTED:
                perms[key] = BLOCKED  # can never be allowed, even by a stale file
        tmap = rec.setdefault("tasks", {})
        if not isinstance(tmap, dict):
            tmap = {}
            rec["tasks"] = tmap
        for task in tasks:
            if task.key not in tmap:
                tmap[task.key] = bool(task.default_enabled)

    return _mutate(connector.id, _apply)


def set_disconnected(connector_id: str) -> bool:
    """Mark disconnected; permissions/tasks are kept for a future re-connect."""

    def _apply(rec: Dict[str, Any]) -> None:
        rec["connected"] = False
        rec["account"] = ""
        rec["connected_at"] = 0
        rec["last_sync"] = 0
        rec["health_override"] = ""

    return _mutate(connector_id, _apply)


def is_allowed(connector_id: str, action: Optional[ConnectorAction]) -> bool:
    """Permission check for one DECLARED action.

    An explicit stored toggle always wins. When the store has no entry for
    this key (connector updated with new actions since the user connected),
    the action's own conservative seed applies — so a new read-only action
    works without forcing a reconnect, while a new destructive one stays
    blocked. Undeclared action keys never reach here (the manager resolves
    actions from the connector's declared map first).
    """
    if action is None or action.risk == RISK_NOT_IMPLEMENTED:
        return False
    perms = connector_record(connector_id).get("permissions")
    if not isinstance(perms, dict):
        return default_permission_state(action) == ALLOWED
    state = perms.get(action.key)
    if state is None:
        return default_permission_state(action) == ALLOWED
    return state == ALLOWED


def set_permission(connector_id: str, action: Optional[ConnectorAction],
                   allowed: bool) -> bool:
    """Flip one permission. ``not_implemented`` actions are locked blocked."""
    if action is None:
        return False
    if allowed and action.risk == RISK_NOT_IMPLEMENTED:
        return False

    def _apply(rec: Dict[str, Any]) -> None:
        perms = rec.setdefault("permissions", {})
        if isinstance(perms, dict):
            perms[action.key] = ALLOWED if allowed else BLOCKED

    return _mutate(connector_id, _apply)


def task_enabled(connector_id: str, task_key: str) -> bool:
    tmap = connector_record(connector_id).get("tasks")
    if not isinstance(tmap, dict):
        return False
    return bool(tmap.get(str(task_key)))


def set_task(connector_id: str, task_key: str, enabled: bool) -> bool:
    key = str(task_key or "").strip()
    if not key:
        return False

    def _apply(rec: Dict[str, Any]) -> None:
        tmap = rec.setdefault("tasks", {})
        if isinstance(tmap, dict):
            tmap[key] = bool(enabled)

    return _mutate(connector_id, _apply)


def update_last_sync(connector_id: str) -> None:
    _mutate(connector_id, lambda rec: rec.__setitem__("last_sync", time.time()))


def set_health_override(connector_id: str, health: str, detail: str = "") -> None:
    """Record a sticky health problem (needs_reauth, error) + short detail."""

    def _apply(rec: Dict[str, Any]) -> None:
        rec["health_override"] = str(health or "")
        rec["last_error"] = str(detail or "")[:200]

    _mutate(connector_id, _apply)


__all__ = [
    "ALLOWED", "BLOCKED",
    "load_state", "connector_record", "is_connected",
    "default_permission_state", "set_connected", "set_disconnected",
    "is_allowed", "set_permission",
    "task_enabled", "set_task",
    "update_last_sync", "set_health_override",
]
