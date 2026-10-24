"""Supercomputer session lifecycle — enter, exit, and the teardown contract.

One session at a time. Entering is idempotent; exiting must leave nothing
running, nothing resident, and no authority outstanding:

* the agent loop is cancelled,
* every Supercomputer BrowserView and its ephemeral partition is destroyed,
* speech stops and the microphone queue is released (Phase 8),
* any model this mode loaded is unloaded,
* **every permission grant is voided**,
* the HUD state key is cleared.

``BOOT_ID`` is generated once per Python process and stamped on every grant, so
a grant from a previous run can never be honoured after a restart even if one
somehow reached disk.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

import syrix_obs as obs

from . import STATE_KEY

# Regenerated on every process start. Any grant not carrying this value is void.
BOOT_ID = uuid.uuid4().hex[:16]


def _process_started_at() -> float:
    """When THIS backend process started, as an epoch timestamp.

    Used to reject a replayed ``supercomputer_enter``. It has to be the
    *process* start, not this module's import time: the module imports lazily on
    the first Supercomputer command, so import time would be moments after a
    genuine enter and would reject it too.

    Falls back to import time if the process table is unreadable — which makes
    the staleness check inert rather than wrong, and an inert check is the safe
    failure here (the workspace still opens; a stale replay is caught by nothing
    worse than the situation we already had).
    """
    try:
        import psutil

        return float(psutil.Process(os.getpid()).create_time())
    except Exception as exc:
        obs.report("supercomputer.session.process_start", exc, level="debug")
        return 0.0


PROCESS_STARTED_TS = _process_started_at()


def is_stale_command(ts: Any) -> bool:
    """True when a HUD command predates this backend process.

    ``hud_control.json`` keeps a queue that survives a backend restart, so a
    ``supercomputer_enter`` from a previous run is replayed on boot — which
    opened an agentic session with no workspace anywhere on screen. A command
    older than the process that is reading it did not come from the HUD that is
    running now, and must not open a mode.
    """
    if not PROCESS_STARTED_TS:
        return False
    try:
        when = float(ts or 0.0)
    except (TypeError, ValueError):
        return False
    return bool(when) and when < PROCESS_STARTED_TS

_LOCK = threading.RLock()
_ACTIVE = False
# Session alive but the workspace window is closed: a task is finishing in the
# background and the user is watching it from the Dynamic Island.
_DETACHED = False
_SESSION_ID = ""
_STARTED_TS = 0.0
_LAST_EXIT_REASON = ""
# The transcript of a conversation reopened from history.
#
# It cannot come through hud_chat.json: `chat_load_session` publishes the
# loaded transcript to the `syrix_loaded_session` key for the NORMAL chat panel
# and never rewrites the live chat buffer. The workspace renders only
# `chat_lines`, so a reopened Supercomputer chat arrived with nothing to show —
# which is exactly what "it opens a new chat instead" looked like.
#
# Carrying it in this snapshot instead keeps it scoped to the mode: normal
# chat's loaded-session key is untouched, and remounting the workspace cannot
# lose it.
_RESTORED_LINES: List[Dict[str, Any]] = []
# Pages a reopened conversation was last on, for the WORKSPACE to open.
#
# Deliberately not opened from here. Every Python-side navigation goes through
# policy.check_navigation, which refuses when there is no TaskContract — and a
# resume happens outside any task, so restoring from the backend was refused
# every time with `no_task_contract`. It is also the wrong actor: the user chose
# this conversation, so reopening its tabs is his navigation, the same as the
# start page the workspace already opens itself. The agent still cannot
# navigate without a contract, which is the property that matters.
_RESTORE_TABS: List[str] = []


def boot_id() -> str:
    return BOOT_ID


def set_restore_tabs(urls: Optional[List[str]]) -> int:
    global _RESTORE_TABS
    clean = [str(u) for u in (urls or [])
             if str(u).startswith(("http://", "https://"))]
    with _LOCK:
        _RESTORE_TABS = clean[:8]
        return len(_RESTORE_TABS)


def restore_tabs() -> List[str]:
    with _LOCK:
        return list(_RESTORE_TABS)


def clear_restore_tabs() -> None:
    global _RESTORE_TABS
    with _LOCK:
        _RESTORE_TABS = []


def set_restored_lines(lines: Optional[List[Dict[str, Any]]]) -> int:
    """Hold a reopened conversation for the workspace to render above the live one."""
    global _RESTORED_LINES
    cleaned: List[Dict[str, Any]] = []
    for entry in (lines or []):
        role = str((entry or {}).get("role") or "")
        text = str((entry or {}).get("text") or (entry or {}).get("content") or "")
        if role in ("user", "assistant", "system") and text.strip():
            cleaned.append({"role": role, "text": text, "ts": (entry or {}).get("ts", 0)})
    with _LOCK:
        _RESTORED_LINES = cleaned[-200:]
        return len(_RESTORED_LINES)


def restored_lines() -> List[Dict[str, Any]]:
    with _LOCK:
        return list(_RESTORED_LINES)


def clear_restored_lines() -> None:
    global _RESTORED_LINES
    with _LOCK:
        _RESTORED_LINES = []


def is_active() -> bool:
    with _LOCK:
        return _ACTIVE


def session_id() -> str:
    with _LOCK:
        return _SESSION_ID


def enabled(config: Optional[Dict[str, Any]] = None) -> bool:
    return bool((config or {}).get("supercomputer_enabled", True))


def enter(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Begin a Supercomputer session. Idempotent — re-entering an active session
    returns the existing one rather than stacking state."""
    global _ACTIVE, _SESSION_ID, _STARTED_TS
    if not enabled(config):
        return {"ok": False, "reason": "supercomputer_disabled"}
    with _LOCK:
        if _ACTIVE:
            return {"ok": True, "reason": "already_active", "session_id": _SESSION_ID}
        _SESSION_ID = uuid.uuid4().hex[:12]
        _STARTED_TS = time.time()
        _ACTIVE = True
        sid = _SESSION_ID
    _log(f"session_enter session_id={sid} boot_id={BOOT_ID}")
    _publish(config)
    return {"ok": True, "session_id": sid}


def has_live_task() -> bool:
    """True when a Supercomputer task is still mid-flight."""
    try:
        from . import tasks as _tasks

        task = _tasks.current()
        return bool(task is not None and not task.is_terminal() and not task.is_halted())
    except Exception as exc:
        obs.report("supercomputer.session.has_live_task", exc, level="debug")
        return False


def detach(config: Optional[Dict[str, Any]] = None, *, reason: str = "workspace_closed") -> Dict[str, Any]:
    """Leave the workspace WITHOUT killing work that is still running.

    Closing the window is not the same as saying stop. When a task is mid-flight
    the user should be able to go back to the normal HUD and watch it finish from
    the Dynamic Island — so the task, its browser and its grants stay exactly as
    they are, and only the things that belong to having a window open are
    released.

    Voice goes, because a microphone attached to a workspace nobody is looking
    at is the one thing that must never linger. Everything else keeps running
    and :func:`exit_mode` is still what ends it.
    """
    global _DETACHED
    with _LOCK:
        if not _ACTIVE:
            return {"ok": False, "reason": "not_active"}
        _DETACHED = True
        sid = _SESSION_ID
    with obs.swallow("supercomputer.session.detach_voice", session_id=sid):
        from . import voice as _voice

        _voice.stop(reason="workspace_closed")

    # With nothing running there is nothing that needs a grant, and a detached
    # session must not be able to act on anything. Dropping them here is what
    # lets the workspace close WITHOUT tearing the session down: the user keeps his
    # tabs and his conversation, and the mode keeps no standing authority while
    # he is not looking at it. A task still in flight keeps its grants, because
    # it is mid-way through work he already approved.
    if not has_live_task():
        with obs.swallow("supercomputer.session.detach_grants", session_id=sid):
            from . import permissions as _perm

            _perm.revoke_all(reason="workspace_closed")
        with obs.swallow("supercomputer.session.detach_priority", session_id=sid):
            from . import resources as _resources

            _resources.restore_priority(config)
    _log(f"session_detach session_id={sid} reason={reason}")
    _publish(config)
    return {"ok": True, "detached": True, "session_id": sid}


def is_detached() -> bool:
    """Session alive, workspace closed — a task running in the background."""
    with _LOCK:
        return _ACTIVE and _DETACHED


def reap_if_detached(config: Optional[Dict[str, Any]] = None) -> bool:
    """Close a detached session once its work is finished.

    The handoff in :func:`detach` keeps a session alive past the window so a
    task can finish. Once it has, nobody is watching and nothing is running, so
    the browser, grants and models must not sit there — this is what makes
    "fully unloads when exited" true even when the exit happened first.
    """
    if not is_detached() or has_live_task():
        return False
    exit_mode(config, reason="detached_task_finished")
    return True


def exit_mode(config: Optional[Dict[str, Any]] = None, *, reason: str = "user_exit") -> Dict[str, Any]:
    """End the session and tear every Supercomputer subsystem down.

    Every teardown step is independent and best-effort-but-reported: one failing
    step must never prevent the others from running, because a half-torn-down
    session is exactly the state that leaves a browser or a microphone live.
    """
    global _ACTIVE, _SESSION_ID, _STARTED_TS, _LAST_EXIT_REASON, _DETACHED
    with _LOCK:
        was_active = _ACTIVE
        sid = _SESSION_ID
        _ACTIVE = False
        _DETACHED = False
        _SESSION_ID = ""
        _STARTED_TS = 0.0
        _LAST_EXIT_REASON = str(reason or "")
    if not was_active:
        _publish(config)
        return {"ok": True, "reason": "not_active"}

    torn: List[str] = []

    # 1. Stop any in-flight agent work first, so nothing re-opens what we close.
    with obs.swallow("supercomputer.session.cancel_tasks", session_id=sid):
        from . import tasks as _tasks

        _tasks.cancel_all(reason="mode_exit")
        torn.append("tasks")

    # 2. Void every permission grant. Done BEFORE the browser teardown so that a
    #    racing action cannot slip through while views are still alive.
    with obs.swallow("supercomputer.session.revoke_grants", session_id=sid):
        from . import permissions as _perm

        _perm.revoke_all(reason="mode_exit")
        torn.append("permissions")

    # 3. Voice off BEFORE the browser, so the microphone is released even if a
    #    later step fails. Leaving the mic attached is the worst outcome here.
    with obs.swallow("supercomputer.session.stop_voice", session_id=sid):
        from . import voice as _voice

        _voice.stop(reason="mode_exit")
        torn.append("voice")

    # 4. Destroy the browser views and their ephemeral partition.
    with obs.swallow("supercomputer.session.close_browser", session_id=sid):
        from .browser import controller as _browser

        _browser.shutdown(config=config)
        torn.append("browser")

    # 5. Stop the local preview and release any model this mode loaded.
    with obs.swallow("supercomputer.session.stop_preview", session_id=sid):
        from . import preview as _preview

        _preview.stop()
        torn.append("preview")

    with obs.swallow("supercomputer.session.unload_models", session_id=sid):
        from . import models as _models

        _models.unload_local(config)
        torn.append("models")

    # 6. Drop the conversation. "Deleting the chat removes temporary task data"
    #    has to include what was said, not only what was browsed.
    with obs.swallow("supercomputer.session.forget_history", session_id=sid):
        from . import spine as _spine

        _spine.forget()
        torn.append("history")

    # 6a. Give the machine its priority back. A mode that is gone must not
    #     leave the backend running below normal for the rest of the session.
    with obs.swallow("supercomputer.session.restore_priority", session_id=sid):
        from . import resources as _resources

        _resources.restore_priority(config)
        _resources.reset_mode(config)
        torn.append("resources")

    # 6b. Hand chat recording back to normal chat. Leaving the channel bound
    #     after the mode is gone would file a later stray supercomputer-tagged
    #     line into a conversation the user has finished with.
    with obs.swallow("supercomputer.session.release_thread", session_id=sid):
        from . import chat_thread as _thread

        _thread.release()
        torn.append("chat_thread")

    # 7. Back to the default safety mode and reasoning depth. A permissive
    #    choice must never outlive the session it was made for.
    with obs.swallow("supercomputer.session.reset_safety", session_id=sid):
        from . import safety_mode as _safety

        _safety.reset()
        torn.append("safety_mode")

    with obs.swallow("supercomputer.session.reset_depth", session_id=sid):
        from . import models as _models_depth

        _models_depth.reset_depth(config)
        torn.append("depth")

    # 8. Task memory briefs. These are work product, not authority — but they
    #    describe what was read and made, so leaving them behind after the mode
    #    closes would outlive the conversation they belong to. Files SYRIX
    #    exported to Downloads are untouched; only the notes about them go.
    with obs.swallow("supercomputer.session.forget_memory", session_id=sid):
        from . import memory as _memory

        _memory.forget_all()
        torn.append("memory")

    clear_restored_lines()
    clear_restore_tabs()

    _log(f"session_exit session_id={sid} reason={reason} torn={','.join(torn) or 'none'}")
    _publish(config)
    return {"ok": True, "session_id": sid, "torn_down": torn}


def snapshot(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The `syrix_supercomputer` HUD state payload."""
    with _LOCK:
        active = _ACTIVE
        detached = _DETACHED
        sid = _SESSION_ID
        started = _STARTED_TS
        last_reason = _LAST_EXIT_REASON
    snap: Dict[str, Any] = {
        "active": active,
        "detached": detached,
        "session_id": sid,
        "boot_id": BOOT_ID,
        "started_ts": started,
        "enabled": enabled(config),
        "last_exit_reason": last_reason,
        "updated_at": time.time(),
    }
    with obs.swallow("supercomputer.session.snapshot_browser"):
        from .browser import controller as _browser

        snap["browser"] = _browser.snapshot()
    with obs.swallow("supercomputer.session.snapshot_tasks"):
        from . import tasks as _tasks

        snap["task"] = _tasks.snapshot()
    with obs.swallow("supercomputer.session.snapshot_permissions"):
        from . import permissions as _perm

        snap["pending_permission"] = _perm.pending_snapshot()
        # Answered cards stay on screen so the decision renders where it was
        # made. Display only — a resolved card carries no authority.
        snap["resolved_permission"] = _perm.resolved_snapshot()

    # Status is DERIVED from the pieces above, never set, so the pill in the
    # workspace cannot drift from what is actually happening.
    with obs.swallow("supercomputer.session.snapshot_status"):
        from . import status as _status

        snap["status"] = _status.derive(
            task=snap.get("task"),
            browser=snap.get("browser"),
            pending_permission=snap.get("pending_permission"),
        )

    with obs.swallow("supercomputer.session.snapshot_results"):
        from . import results as _results

        snap["results"] = _results.recent(60)

    with obs.swallow("supercomputer.session.snapshot_activity"):
        from . import audit as _audit

        snap["activity"] = _audit.recent(60)

    # Connectors stay live inside this mode, so their use has to be visible in
    # the same Activity list as browser actions — otherwise "SYRIX read your
    # Gmail through the connector" is the one thing that happens off-screen.
    # Read-only: this reflects the connector subsystem's own audit, and never
    # writes to it.
    with obs.swallow("supercomputer.session.snapshot_connectors"):
        import sys as _sys

        if "syrix_connectors" in _sys.modules:
            from syrix_connectors import audit as _conn_audit

            recent = _conn_audit.recent(20) if hasattr(_conn_audit, "recent") else []
            merged = list(snap.get("activity") or [])
            for entry in recent:
                merged.append({
                    "action": f"connector:{entry.get('action') or ''}",
                    "scope": str(entry.get("connector") or entry.get("scope") or ""),
                    "result": entry.get("result") or "",
                    "reason": entry.get("reason") or "",
                    "ts": entry.get("ts") or 0,
                })
            merged.sort(key=lambda e: float(e.get("ts") or 0))
            snap["activity"] = merged[-60:]

    with obs.swallow("supercomputer.session.snapshot_voice"):
        from . import voice as _voice

        snap["voice"] = _voice.snapshot(config)

    with obs.swallow("supercomputer.session.snapshot_capabilities"):
        from . import capabilities as _caps

        snap["capabilities"] = _caps.snapshot(config)

    with obs.swallow("supercomputer.session.snapshot_safety"):
        from . import safety_mode as _safety

        snap["safety"] = _safety.snapshot()

    with obs.swallow("supercomputer.session.snapshot_workspace"):
        from . import paths as _paths

        snap["workspace_root"] = _paths.workspace_root()

    # Eco / Performance, and whether the governor is currently holding back
    # because something demanding is in front, so the interface can show it.
    with obs.swallow("supercomputer.session.snapshot_resources"):
        from . import resources as _resources

        snap["resources"] = _resources.snapshot(config)

    # The stage graph of a running project, so the Steps panel and the Island
    # can show real structure rather than a flat list.
    with obs.swallow("supercomputer.session.snapshot_project"):
        from . import project as _project
        from . import tasks as _tasks

        live = _tasks.current()
        if live is not None:
            graph = _project.snapshot(live.task_id)
            if graph:
                snap["project"] = graph

    # The task-completion chime. A long task is precisely the one the user looks
    # away from, so it has to be able to tell him it finished.
    snap["sounds_enabled"] = bool((config or {}).get("supercomputer_sounds_enabled", True))

    # A conversation reopened from history, for the workspace to show above the
    # live lines, and the pages it was on for the workspace to reopen. Both
    # empty for an ordinary session.
    snap["chat_restored"] = restored_lines()
    snap["restore_tabs"] = restore_tabs()

    with obs.swallow("supercomputer.session.snapshot_models"):
        from . import models as _models

        # `depth` drives the in-mode switcher's selected state. Without it the
        # pill reads "Auto" for ever, whatever the user picked.
        snap["models"] = {"roles": {r: _models.model_for(r, config) or ""
                                    for r in _models.ROLES},
                          "resident": _models.resident(),
                          "depth": _models.depth(),
                          "depths": list(_models.DEPTHS)}

    with obs.swallow("supercomputer.session.snapshot_preview"):
        from . import preview as _preview

        snap["preview"] = _preview.snapshot()

    return snap


def _publish(config: Optional[Dict[str, Any]] = None) -> None:
    with obs.swallow("supercomputer.session.publish"):
        from . import hud_api

        hud_api.push_state(config)


def _log(msg: str) -> None:
    with obs.swallow("supercomputer.session.log"):
        print(f"[SUPERCOMPUTER] {msg}", flush=True)


def _reset_for_tests() -> None:
    """Drop all session state. Tests only."""
    global _ACTIVE, _SESSION_ID, _STARTED_TS, _LAST_EXIT_REASON, _DETACHED
    global _RESTORED_LINES, _RESTORE_TABS
    with _LOCK:
        _ACTIVE = False
        _DETACHED = False
        _SESSION_ID = ""
        _STARTED_TS = 0.0
        _LAST_EXIT_REASON = ""
        _RESTORED_LINES = []
        _RESTORE_TABS = []


__all__ = [
    "BOOT_ID",
    "PROCESS_STARTED_TS",
    "is_stale_command",
    "boot_id",
    "is_active",
    "is_detached",
    "has_live_task",
    "detach",
    "reap_if_detached",
    "session_id",
    "enabled",
    "enter",
    "exit_mode",
    "snapshot",
    "STATE_KEY",
]
