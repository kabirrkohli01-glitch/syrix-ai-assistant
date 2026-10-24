"""HUD ↔ Supercomputer command surface.

``syrix_host_hud_wiring._dispatch`` routes the ``supercomputer_*`` commands
here; every handler returns ``(ok, detail)`` for the standard widget ack and
finishes by pushing a fresh ``syrix_supercomputer`` snapshot into
``hud_state.json`` so the workspace re-renders immediately.

State writes prefer syrix_host's ``_syrix_update_hud_state`` (in-memory +
debounced disk, immediate-flush key). When the backend isn't fully up (probe
mode, tests) they fall back to an atomic read-merge-replace of
``hud_state.json`` under the shared ``HUD_STATE_WRITE_LOCK`` — the same pattern
``syrix_connectors.hud_api`` and ``syrix_core_v2.activity_feed`` use.

Permission decisions arrive here and ONLY here. This is the single channel
through which authority to act can enter the system: not model output, not page
content, not config.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import syrix_obs as obs

from . import STATE_KEY

_PUSH_LOCK = threading.RLock()
_LAST_SNAPSHOT: Optional[Dict[str, Any]] = None


def last_snapshot() -> Optional[Dict[str, Any]]:
    """Most recent snapshot pushed to the HUD (None before the first push).

    syrix_host's filtered hud_state writer re-injects this on every write
    cycle — the full-state builder there knows nothing about Supercomputer, so
    without this the key would be wiped from hud_state.json ~6x/second.
    """
    with _PUSH_LOCK:
        return dict(_LAST_SNAPSHOT) if isinstance(_LAST_SNAPSHOT, dict) else None


# Commands the wiring layer routes here (mirrored into _SYRIX_OWNED_COMMANDS).
HUD_COMMANDS = (
    "supercomputer_enter",
    "supercomputer_exit",
    "supercomputer_refresh",
    "supercomputer_stop",
    "supercomputer_pause",
    "supercomputer_resume",
    "supercomputer_take_control",
    "supercomputer_return_control",
    "supercomputer_permission_decision",
    "supercomputer_clear_data",
    "supercomputer_reset_permissions",
    "supercomputer_set_depth",
    "supercomputer_set_resource_mode",
    "supercomputer_set_safety_mode",
    "supercomputer_open_result",
    "supercomputer_choose_workspace",
    "supercomputer_voice_on",
    "supercomputer_voice_off",
    "supercomputer_voice_interrupt",
    "supercomputer_voice_preview",
    # 2026-07-26 — start a fresh conversation without leaving the mode, and
    # without throwing away the browser you are in the middle of using.
    "supercomputer_new_chat",
)
# NOTE: annotate is deliberately NOT a HUD command. It waits on a human
# pointing at something, and these handlers run on the wiring layer's dispatch
# thread — parking that thread for minutes would freeze every other owned
# command (widget acks, connector actions, the lot). The renderer drives
# annotate directly through window.syrixHud.superComputer.annotate().


def _hud_state_path() -> str:
    try:
        import syrix_paths

        return syrix_paths.state_file("hud_state.json")
    except Exception as exc:  # pragma: no cover
        obs.report("supercomputer.hud_api.state_path", exc)
        return os.path.join(os.getcwd(), "hud_state.json")


def _fallback_merge_write(fields: Dict[str, Any]) -> None:
    """Atomic read-merge-replace under the shared HUD write lock."""
    try:
        from syrix_core_v2.state_write_lock import HUD_STATE_WRITE_LOCK as _wlock
    except Exception as exc:
        obs.report("supercomputer.hud_api.write_lock", exc, level="debug")
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
            except Exception as exc:
                obs.report("supercomputer.hud_api.read_existing", exc, level="debug")
                existing = {}
            existing.update(fields)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(existing, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
    except Exception as exc:
        obs.report("supercomputer.hud_api.fallback_write", exc)


def push_state(config: Optional[Dict[str, Any]] = None) -> None:
    """Build a fresh snapshot and write it to hud_state.json."""
    global _LAST_SNAPSHOT
    with _PUSH_LOCK:
        try:
            from . import session

            snap = session.snapshot(config)
        except Exception as exc:
            obs.report("supercomputer.hud_api.snapshot", exc)
            snap = {"active": False, "error": type(exc).__name__, "updated_at": time.time()}
        _LAST_SNAPSHOT = snap
        fields = {STATE_KEY: snap}
        try:
            import syrix_host as _jc  # type: ignore

            updater = getattr(_jc, "_syrix_update_hud_state", None)
            if callable(updater):
                updater(fields)
                return
        except Exception as exc:
            obs.report("supercomputer.hud_api.jc_update", exc)
        _fallback_merge_write(fields)


# ── the conversation thread ────────────────────────────────────────────────
def _open_chat_thread(resume_session_id: str, config: Dict[str, Any],
                      *, fresh_session: bool = True) -> None:
    """Give this session its own chat thread, or reopen a saved one.

    Runs on entry. Without it Supercomputer's lines landed in whatever ordinary
    text session was open, so they showed up under Chats in history and
    reopening one dropped the user into normal SYRIX with no browser.

    ``fresh_session`` is False when the mode was already open. A RESUME is still
    honoured in that case — reopening a chat from history has to work whether or
    not the workspace happened to be up — but a plain re-entry must not rotate
    the thread out from under a conversation in progress.
    """
    from . import session as _session

    with obs.swallow("supercomputer.hud_api.chat_thread"):
        from . import chat_thread

        if resume_session_id:
            if not chat_thread.is_supercomputer_session(resume_session_id):
                # Deny-unknown: only a session this mode tagged may reopen the
                # mode. Anything else falls through to a normal fresh thread.
                obs.report("supercomputer.hud_api.bad_resume",
                           RuntimeError("resume id is not a supercomputer session"),
                           level="debug", session_id=resume_session_id)
            else:
                # Reopening: show the saved transcript, put it back into the
                # agent's working memory, and reopen the tabs it was on.
                _session.set_restored_lines(chat_thread.messages(resume_session_id))
                chat_thread.rehydrate(resume_session_id)
                chat_thread.make_active(resume_session_id)
                _session.set_restore_tabs(chat_thread.remembered_tabs(resume_session_id))
                push_state(config)
                return
        if fresh_session:
            _session.clear_restored_lines()
            _session.clear_restore_tabs()
            chat_thread.start()


def _remember_browser_state() -> None:
    """Record the open tabs against the current chat session, for reopening."""
    with obs.swallow("supercomputer.hud_api.remember_tabs"):
        from . import chat_thread
        from .browser import controller as browser

        sid = chat_thread.active_id()
        if sid and chat_thread.is_supercomputer_session(sid):
            chat_thread.remember_browser(sid, browser.snapshot().get("tabs") or [])


def _new_chat(config: Dict[str, Any]) -> Tuple[bool, str]:
    """Start a fresh conversation without leaving the mode.

    Deliberately keeps the browser and its tabs. The user asking for a new chat is
    saying "different subject", not "throw away the twelve pages I have open" —
    and a New chat button that closed his research would be used exactly once.
    What does go: the conversation, the agent's memory of it, and any finished
    task's panels.
    """
    from . import chat_thread, memory, results, spine, tasks

    _remember_browser_state()

    with obs.swallow("supercomputer.hud_api.new_chat_stop"):
        task = tasks.current()
        if task is not None and not task.is_terminal():
            tasks.stop(task.task_id, reason="new_chat")

    spine.forget()
    with obs.swallow("supercomputer.hud_api.new_chat_restored"):
        from . import session as _session

        _session.clear_restored_lines()
        _session.clear_restore_tabs()
    with obs.swallow("supercomputer.hud_api.new_chat_memory"):
        memory.forget_all()
    with obs.swallow("supercomputer.hud_api.new_chat_results"):
        results.clear_all()
    with obs.swallow("supercomputer.hud_api.new_chat_tasks"):
        tasks.clear_all()
    with obs.swallow("supercomputer.hud_api.new_chat_resume"):
        from . import resume as _resume

        _resume.clear_awaiting()
    with obs.swallow("supercomputer.hud_api.new_chat_cards"):
        from . import permissions as _perm

        # The answered cards are part of the conversation they were answered
        # in. A new chat clears the transcript, so it clears these too.
        _perm.clear_resolved()

    sid = chat_thread.start()
    push_state(config)
    return True, sid or "new_chat"


def handle_command(
    cmd: str,
    data: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """Route one ``supercomputer_*`` command. Returns ``(ok, detail)``."""
    d = data or {}
    cfg = config or {}
    try:
        from . import session

        if cmd == "supercomputer_enter":
            # hud_control.json keeps a command queue that survives a backend
            # restart, so an enter from a previous run gets replayed at boot —
            # which opened an agentic session with no workspace on screen and
            # let a marked chat turn run browser work invisibly. A command older
            # than this process did not come from the HUD that is running now.
            if session.is_stale_command(d.get("ts")):
                obs.report(
                    "supercomputer.hud_api.stale_enter",
                    RuntimeError("ignored a replayed supercomputer_enter"),
                    level="debug", ts=str(d.get("ts")),
                )
                return False, "stale_command_ignored"
            res = session.enter(cfg)
            if res.get("ok"):
                # The shell sends this when the user picks the mode (optionally with
                # a session to reopen) and the workspace sends it again on
                # mount, so BOTH orderings have to be safe: a resume must be
                # honoured even if the plain enter landed first, and a plain
                # re-entry must never rotate the thread of a live conversation.
                _data = d.get("data") if isinstance(d.get("data"), dict) else {}
                resume_id = str(d.get("resume_session_id")
                                or _data.get("resume_session_id") or "").strip()
                _open_chat_thread(resume_id, cfg,
                                  fresh_session=res.get("reason") != "already_active")
            return bool(res.get("ok")), str(res.get("reason") or res.get("session_id") or "")

        if cmd == "supercomputer_new_chat":
            return _new_chat(cfg)

        if cmd == "supercomputer_exit":
            reason = str(d.get("reason") or "user_exit")
            # Remember where the browser was BEFORE anything is torn down, so
            # reopening this chat from history can put the tabs back.
            _remember_browser_state()
            # Closing the window is not the same as saying stop, and it is not
            # the same as ending the session either.
            #
            # It used to tear the whole mode down whenever no task was running:
            # browser, tabs, conversation, everything. So the user would step out to
            # normal chat for two minutes, come back, and find a blank workspace
            # with his twelve research tabs gone — having never closed SYRIX.
            # Stepping out of a room is not leaving the building.
            #
            # So closing always DETACHES. The session, its tabs and its
            # conversation stay exactly where he left them until SYRIX itself
            # shuts down or he starts a new chat. Grants are still dropped when
            # nothing is running (see session.detach) so the safety model is
            # unchanged — a detached session can no longer act on anything.
            res = session.detach(cfg, reason=reason)
            push_state(cfg)
            return bool(res.get("ok")), (
                "detached_task_running" if session.has_live_task() else "detached")

        if cmd == "supercomputer_refresh":
            push_state(cfg)
            return True, "refreshed"

        from . import tasks
        from .browser import controller as browser

        if cmd == "supercomputer_stop":
            reason = str(d.get("reason") or "user_stop")
            browser.stop(reason=reason)
            task = tasks.current()
            if task is not None and not task.is_terminal():
                tasks.stop(task.task_id, reason=reason)
            push_state(cfg)
            return True, "stopped"

        if cmd == "supercomputer_pause":
            browser.pause(reason="user_request")
            task = tasks.current()
            if task is not None and not task.is_terminal():
                tasks.bump_generation(task.task_id)
                tasks.set_state(task.task_id, tasks.STATE_PAUSED_USER, detail="user_pause")
            push_state(cfg)
            return True, "paused"

        if cmd == "supercomputer_resume":
            res = browser.resume(reason="user_request")
            task = tasks.current()
            if task is not None and task.state == tasks.STATE_PAUSED_USER:
                tasks.set_state(task.task_id, tasks.STATE_RUNNING)
            push_state(cfg)
            return bool(res.get("ok")), str(res.get("reason") or "resumed")

        if cmd == "supercomputer_take_control":
            res = browser.take_control(reason="user_request")
            task = tasks.current()
            if task is not None and not task.is_terminal():
                tasks.bump_generation(task.task_id)
                tasks.set_state(task.task_id, tasks.STATE_PAUSED_USER, detail="user_took_control")
            push_state(cfg)
            return bool(res.get("ok")), "user_in_control"

        if cmd == "supercomputer_return_control":
            res = browser.return_control(reason="user_request")
            push_state(cfg)
            return bool(res.get("ok")), str(res.get("reason") or "agent_in_control")

        if cmd == "supercomputer_permission_decision":
            # Routed through the gate, not straight to permissions: the gate
            # also moves the task (running / paused-for-discussion) and audits
            # who decided what. This is the ONLY path from a human decision
            # into a grant.
            from . import gate

            req_id = str(d.get("request_id") or "")
            pending_req = None
            with obs.swallow("supercomputer.hud_api.pending_lookup", rid=req_id):
                from . import permissions as _perm

                pending_req = _perm.pending(req_id)
            res = gate.resolve_permission(req_id, str(d.get("decision") or ""), config=cfg)
            # Every answer gets a reply in the chat. Allow carries on; No stops
            # and says how to finish by hand; "Chat about this" stops and asks
            # what to do differently. A card that goes quiet whatever you press
            # is not a decision, it is a dead end.
            if res.get("ok") and pending_req is not None:
                from . import loop as _loop, resume as _resume

                decision = str(res.get("decision") or "")
                if decision == "allowed":
                    _resume.after_allow(pending_req.task_id, config=cfg, request_id=req_id)
                elif decision == "declined":
                    _loop.discard_pending_action(req_id)
                    _resume.after_decline(pending_req.task_id, pending_req.description,
                                          config=cfg, request_id=req_id)
                else:
                    _loop.discard_pending_action(req_id)
                    _resume.after_discuss(pending_req.task_id, pending_req.description,
                                          config=cfg, request_id=req_id)
            push_state(cfg)
            return bool(res.get("ok")), str(res.get("decision") or res.get("reason") or "")

        if cmd == "supercomputer_clear_data":
            from . import results

            browser.shutdown(config=cfg)
            results.clear()
            cleared = _clear_temp_files()
            push_state(cfg)
            return True, f"cleared:{cleared}"

        if cmd == "supercomputer_open_result":
            # Open a file SYRIX produced, in the browser, through the local
            # preview server. Read-only and inside the task's own workspace —
            # this is "let me look at it before I decide", not an export.
            from . import preview, tasks

            rel = str(d.get("relative") or "").strip().replace("\\", "/")
            task = tasks.current()
            if not rel or task is None:
                return False, "nothing_to_open"
            started = preview.start(task.task_id)
            if not started.get("ok"):
                return False, str(started.get("reason") or "preview_unavailable")
            base = str((started.get("data") or {}).get("url") or "").rstrip("/")
            opened = browser.open_url(f"{base}/{rel}", new_tab=True, config=cfg)
            push_state(cfg)
            return bool(opened.get("ok")), str(opened.get("reason") or "opened")

        if cmd == "supercomputer_choose_workspace":
            # The renderer runs the folder picker (Electron owns dialogs) and
            # sends the chosen path here, where it is validated before anything
            # is written to it.
            from . import paths

            ok, detail = paths.set_workspace_root(str(d.get("path") or ""))
            push_state(cfg)
            return bool(ok), detail or "default"

        if cmd == "supercomputer_set_safety_mode":
            from . import safety_mode

            chosen = safety_mode.set_mode(str(d.get("mode") or ""))
            push_state(cfg)
            return True, chosen

        if cmd == "supercomputer_set_depth":
            from . import models

            chosen = models.set_depth(str(d.get("depth") or ""))
            push_state(cfg)
            return True, chosen

        if cmd == "supercomputer_set_resource_mode":
            # Eco / Auto / Performance. Auto is the default and gets out of the
            # way on its own when a full-screen application is in front; the
            # other two are for when the user wants to decide rather than be
            # guessed at.
            from . import resources

            chosen = resources.set_mode(str(d.get("mode") or ""))
            push_state(cfg)
            return True, chosen

        if cmd == "supercomputer_reset_permissions":
            from . import permissions

            n = permissions.revoke_all(reason="user_reset")
            push_state(cfg)
            return True, f"revoked:{n}"

        if cmd.startswith("supercomputer_voice"):
            from . import voice

            if cmd == "supercomputer_voice_on":
                res = voice.start(cfg)
            elif cmd == "supercomputer_voice_off":
                res = voice.stop(reason=str(d.get("reason") or "user_toggle"))
            elif cmd == "supercomputer_voice_interrupt":
                res = voice.interrupt(reason=str(d.get("reason") or "barge_in"))
            else:  # preview — speaks one line without starting a session
                res = voice.preview_voice(str(d.get("gender") or "female"), config=cfg)
            push_state(cfg)
            return bool(res.get("ok")), str(res.get("reason") or "ok")

        return False, f"unknown_command:{cmd}"
    except Exception as exc:
        obs.report("supercomputer.hud_api.handle_command", exc, command=str(cmd))
        return False, f"error:{type(exc).__name__}"


def _clear_temp_files() -> int:
    """Wipe browser-derived scratch: page text, snapshots, caches.

    User-approved exported files live in the workspaces tree and are NOT touched
    — deleting a chat must not delete work the user asked to be saved.
    """
    import shutil

    from . import paths

    removed = 0
    try:
        cache = paths.cache_dir()
        if os.path.isdir(cache):
            for name in os.listdir(cache):
                target = os.path.join(cache, name)
                try:
                    if os.path.isdir(target):
                        shutil.rmtree(target, ignore_errors=True)
                    else:
                        os.remove(target)
                    removed += 1
                except OSError as exc:
                    obs.report("supercomputer.hud_api.clear_temp_entry", exc, path=target)
    except Exception as exc:
        obs.report("supercomputer.hud_api.clear_temp", exc)
    return removed


def _reset_for_tests() -> None:
    global _LAST_SNAPSHOT
    with _PUSH_LOCK:
        _LAST_SNAPSHOT = None


__all__ = ["STATE_KEY", "HUD_COMMANDS", "last_snapshot", "push_state", "handle_command"]
