"""HUD ↔ connectors command surface.

``syrix_host_hud_wiring._dispatch`` routes the ``connector_*`` commands
here; every handler returns ``(ok, detail)`` for the standard widget ack and
finishes by pushing a fresh ``syrix_connectors`` snapshot into
``hud_state.json`` so the panel re-renders immediately.

State writes prefer syrix_host's ``_syrix_update_hud_state`` (in-memory +
debounced disk, immediate-flush key). When the backend isn't fully up (probe
mode, tests) they fall back to an atomic read-merge-replace of
``hud_state.json`` under the shared ``HUD_STATE_WRITE_LOCK`` — the same
pattern ``syrix_core_v2.activity_feed`` uses.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, Optional, Tuple

import syrix_obs as obs

from . import manager

STATE_KEY = "syrix_connectors"

_PUSH_LOCK = threading.RLock()
_LAST_SNAPSHOT: Optional[Dict[str, Any]] = None


def last_snapshot() -> Optional[Dict[str, Any]]:
    """Most recent snapshot pushed to the HUD (None before the first push).

    syrix_host's filtered hud_state writer re-injects this on every write
    cycle — the full-state builder there knows nothing about connectors, so
    without this the key would be wiped from hud_state.json ~6×/second.
    """
    with _PUSH_LOCK:
        return dict(_LAST_SNAPSHOT) if isinstance(_LAST_SNAPSHOT, dict) else None

# Commands the wiring layer routes here (mirrored into _SYRIX_OWNED_COMMANDS).
HUD_COMMANDS = (
    "connector_connect",
    "connector_disconnect",
    "connector_set_permission",
    "connector_set_task",
    "connector_confirm_action",
    "connector_deny_action",
    "connectors_refresh",
)


def _hud_state_path() -> str:
    try:
        import syrix_paths

        return syrix_paths.state_file("hud_state.json")
    except Exception as exc:  # pragma: no cover
        obs.report("connectors.hud_api.state_path", exc)
        return os.path.join(os.getcwd(), "hud_state.json")


def _fallback_merge_write(fields: Dict[str, Any]) -> None:
    """Atomic read-merge-replace under the shared HUD write lock."""
    try:
        from syrix_core_v2.state_write_lock import HUD_STATE_WRITE_LOCK as _wlock
    except Exception:
        _wlock = threading.RLock()
    try:
        with _wlock:
            path = _hud_state_path()
            existing: Dict[str, Any] = {}
            try:
                if os.path.isfile(path):
                    with open(path, "r", encoding="utf-8") as fh:
                        existing = json.load(fh) or {}
                    if not isinstance(existing, dict):
                        existing = {}
            except Exception:
                existing = {}
            existing.update(fields)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(existing, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
    except Exception as exc:
        obs.report("connectors.hud_api.fallback_write", exc)


def push_state(config: Optional[Dict[str, Any]] = None) -> None:
    """Build a fresh snapshot and write it to hud_state.json."""
    global _LAST_SNAPSHOT
    with _PUSH_LOCK:
        try:
            snap = manager.snapshot(config)
        except Exception as exc:
            obs.report("connectors.hud_api.snapshot", exc)
            snap = {"enabled": False, "error": type(exc).__name__, "ts": time.time()}
        _LAST_SNAPSHOT = snap
        fields = {STATE_KEY: snap}
        try:
            import syrix_host as _jc  # type: ignore

            updater = getattr(_jc, "_syrix_update_hud_state", None)
            if callable(updater):
                updater(fields)
                return
        except Exception as exc:
            obs.report("connectors.hud_api.jc_update", exc)
        _fallback_merge_write(fields)


def handle_command(cmd: str, data: Optional[Dict[str, Any]] = None,
                   config: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """Dispatch one HUD command. Returns ``(ok, detail)`` for the widget ack."""
    d = data if isinstance(data, dict) else {}
    cfg = dict(config or {})
    cid = str(d.get("connector") or d.get("connector_id") or d.get("id") or "").strip().lower()
    try:
        if cmd == "connector_connect":
            res = manager.start_connect(cid, config=cfg,
                                        on_change=lambda: push_state(cfg))
            push_state(cfg)
            ok = bool(res.get("ok"))
            return ok, ("sign-in started — finish in your browser" if ok
                        else str(res.get("status") or "connect_failed"))

        if cmd == "connector_disconnect":
            ok = manager.disconnect(cid, config=cfg)
            push_state(cfg)
            return ok, "disconnected" if ok else "disconnect_incomplete"

        if cmd == "connector_set_permission":
            from . import registry

            inst = registry.get_connector(cid)
            action = inst.actions().get(str(d.get("action") or "")) if inst else None
            if action is None:
                return False, "unknown_action"
            from . import permissions

            allowed = bool(d.get("allowed"))
            ok = permissions.set_permission(cid, action, allowed)
            push_state(cfg)
            return ok, (f"{action.label} {'allowed' if allowed else 'blocked'}"
                        if ok else "permission_locked")

        if cmd == "connector_set_task":
            from . import permissions

            task_key = str(d.get("task") or "").strip()
            ok = permissions.set_task(cid, task_key, bool(d.get("enabled")))
            push_state(cfg)
            return ok, "task updated" if ok else "task_update_failed"

        if cmd == "connector_confirm_action":
            result = manager.confirm_action(str(d.get("confirm_id") or ""), config=cfg)
            push_state(cfg)
            return bool(result.ok), result.human_message or result.error or "done"

        if cmd == "connector_deny_action":
            ok = manager.deny_action(str(d.get("confirm_id") or ""))
            push_state(cfg)
            return ok, "denied" if ok else "not_found"

        if cmd == "connectors_refresh":
            push_state(cfg)
            return True, "refreshed"

        return False, f"unknown_command:{cmd}"
    except Exception as exc:
        obs.report("connectors.hud_api.handle", exc, cmd=cmd, connector=cid)
        return False, f"error:{type(exc).__name__}"


__all__ = ["HUD_COMMANDS", "STATE_KEY", "handle_command", "push_state"]
