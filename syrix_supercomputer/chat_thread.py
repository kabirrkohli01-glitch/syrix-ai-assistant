"""Supercomputer's conversation: its own thread, saved under its own kind.

Two things were wrong, and they are the same thing seen from either end.

**Leaving and coming back wiped the conversation.** The workspace stamped a
watermark at mount and sliced away every line before it, so re-entering the mode
started blank. That was deliberate once — "a fresh conversation rather than
replaying the last one" — but it makes the mode amnesiac in exactly the way the user
noticed and disliked.

**And the chats were being filed as ordinary chats.** Every visible line written
while a session is live is already tagged ``channel:"supercomputer"``
(``syrix_host._voice_isolation_channel``), so it stays out of normal chat while
it is happening. But it landed in whatever text session happened to be open, and
that session was never tagged — so it appeared under Chats in history, and
reopening it dropped the user into normal SYRIX with the browser gone.

This module fixes both by doing for Supercomputer exactly what
``syrix_host`` already does for Voice Mode: rotate a session on entry, tag it
``meta.kind = "supercomputer"``, and record enough alongside it that reopening
can put the mode back the way it was.

It lives here rather than in ``syrix_host`` on purpose. That file is frozen by
``tests/test_host_freeze.py`` and is 66k lines; this is a lazy import
inside the mode, so a SYRIX that never opens Supercomputer never loads it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import syrix_obs as obs

# The session-store key that puts a chat under "Supercomputer" in history
# instead of under "Chats". Mirrors Voice Mode's "voice".
KIND = "supercomputer"

# Session meta keys we own.
META_TABS = "sc_tabs"
META_TITLE = "sc_title"

# How many tab URLs are remembered. Restoring twenty tabs on reopen would be
# hostile; the handful that were actually in use is the useful part.
MAX_TABS = 6


def _store():
    """The chat session store, or None when it is unavailable."""
    try:
        from syrix_host_modules import chat_sessions

        return chat_sessions
    except Exception as exc:
        obs.report("supercomputer.chat_thread.store", exc, level="debug")
        return None


def start(title: str = "Supercomputer") -> str:
    """Open a fresh session tagged as Supercomputer. Returns its id, or "".

    Called on mode entry and by the New chat button, so the conversation is
    filed correctly from its first line rather than being retro-tagged.

    **It does NOT take the active session.** There is one active pointer, and
    taking it works only while the workspace is on screen: with a task still
    running in the background the user goes back to normal chat, types, and his
    message lands in the Supercomputer session because nothing hands the
    pointer back. Found on disk — a session tagged kind="supercomputer" holding
    four supercomputer lines and one untagged normal one. Instead the channel
    is BOUND to this session, so lines tagged "supercomputer" come here and
    everything else keeps going wherever normal chat is pointing.
    """
    store = _store()
    if store is None:
        return ""
    with obs.swallow("supercomputer.chat_thread.start"):
        sid = store.new_session(title=str(title or "Supercomputer"),
                                force_new=True, set_active=False)
        if sid:
            store.set_session_meta(sid, "kind", KIND)
            _bind(store, sid)
        return str(sid or "")
    return ""


def _bind(store, session_id: str) -> None:
    """Point the supercomputer channel at this session. Older builds of the
    store have no binding API; there the mode simply behaves as it did."""
    binder = getattr(store, "bind_channel", None)
    if callable(binder):
        with obs.swallow("supercomputer.chat_thread.bind"):
            binder(KIND, session_id)


def release() -> bool:
    """Stop routing supercomputer lines to a session. Called on mode exit.

    Safe to call twice, and safe to call when nothing was ever bound — after
    it, every line goes to the normal active session again.
    """
    store = _store()
    if store is None:
        return False
    unbind = getattr(store, "unbind_channel", None)
    if not callable(unbind):
        return False
    with obs.swallow("supercomputer.chat_thread.release"):
        return bool(unbind(KIND))
    return False


def active_id() -> str:
    """THIS mode's session — the bound one, not the global active pointer.

    Since the mode stopped taking the active pointer, that pointer belongs to
    normal chat and asking it for "our" session would hand back the user's ordinary
    conversation. Falls back to the active pointer only on a store with no
    binding API, which is the behaviour that existed before.
    """
    store = _store()
    if store is None:
        return ""
    with obs.swallow("supercomputer.chat_thread.active"):
        bound = getattr(store, "bound_session", None)
        if callable(bound):
            sid = str(bound(KIND) or "")
            if sid:
                return sid
            return ""
        return str(store.get_active_id() or "")
    return ""


def make_active(session_id: str) -> bool:
    """Make a reopened session the one new supercomputer lines land in.

    Without this the transcript would be restored on screen while every new
    turn was written into whatever session happened to be bound — the
    conversation would look continuous and be recorded split in two.

    Binds the channel rather than moving the global active pointer, for the
    same reason :func:`start` does: normal chat must keep its own session even
    while this one is live.
    """
    store = _store()
    if store is None or not session_id:
        return False
    if callable(getattr(store, "bind_channel", None)):
        _bind(store, session_id)
        return True
    with obs.swallow("supercomputer.chat_thread.make_active"):
        return store.load_session(session_id) is not None
    return False


def is_supercomputer_session(session_id: str) -> bool:
    """True only for a session this module tagged.

    Used by the reopen path: a session claiming to be a Supercomputer chat must
    actually be one before the mode is opened for it, the same guard Voice Mode
    applies to `resume_session_id`.
    """
    store = _store()
    if store is None or not session_id:
        return False
    with obs.swallow("supercomputer.chat_thread.kind_check"):
        return str(store.get_session_meta(session_id, "kind", "") or "") == KIND
    return False


def remember_browser(session_id: str, tabs: Optional[List[Dict[str, Any]]] = None) -> bool:
    """Store the open tab URLs against the session, for reopening later.

    Only URLs. The BrowserViews and their partition are destroyed on exit, so
    there is no live state to keep — but the profile is the user's own signed-in
    one, so reopening the URLs lands back on the same pages, still logged in.
    """
    store = _store()
    if store is None or not session_id:
        return False
    urls: List[str] = []
    for tab in (tabs or []):
        url = str((tab or {}).get("url") or "").strip()
        if url and url.startswith(("http://", "https://")) and url not in urls:
            urls.append(url)
    with obs.swallow("supercomputer.chat_thread.remember_tabs"):
        return bool(store.set_session_meta(session_id, META_TABS, urls[:MAX_TABS]))
    return False


def remembered_tabs(session_id: str) -> List[str]:
    store = _store()
    if store is None or not session_id:
        return []
    with obs.swallow("supercomputer.chat_thread.read_tabs"):
        raw = store.get_session_meta(session_id, META_TABS, []) or []
        return [str(u) for u in raw if str(u).startswith(("http://", "https://"))][:MAX_TABS]
    return []


def messages(session_id: str) -> List[Dict[str, Any]]:
    """The saved lines of one session, without making it active."""
    store = _store()
    if store is None or not session_id:
        return []
    with obs.swallow("supercomputer.chat_thread.peek"):
        data = store.peek_session(session_id) or {}
        return list(data.get("messages") or [])
    return []


def rehydrate(session_id: str, *, turns: int = 12) -> int:
    """Put a reopened session's conversation back into the agent's memory.

    Without this, reopening a Supercomputer chat would show the user the transcript
    while the agent behind it remembered nothing — it would read as continuous
    and behave as amnesiac, which is worse than starting clean.
    """
    from . import spine

    saved = messages(session_id)
    if not saved:
        return 0
    spine.forget()
    restored = 0
    for entry in saved[-(max(1, int(turns)) * 2):]:
        role = str(entry.get("role") or "")
        text = str(entry.get("text") or entry.get("content") or "")
        if role in ("user", "assistant") and text.strip():
            spine.remember(role, text)
            restored += 1
    return restored


__all__ = ["KIND", "META_TABS", "start", "release", "active_id", "make_active",
           "is_supercomputer_session", "remember_browser", "remembered_tabs",
           "messages", "rehydrate"]
