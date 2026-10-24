"""How much Supercomputer asks before it acts.

Three modes, chosen from the chat bar. They are a **band**, not a set of
overlapping opinions: every action falls into exactly one class, and each mode
names the classes it stops for. Given a mode and an action there is one answer,
which is what makes this predictable enough to trust.

    ask_always   stop before anything that touches the web at all.
                 Searching and reading what is already on screen are free;
                 opening a site, clicking, typing and every side effect ask.

    balanced     stop before anything risky or visible to someone else —
                 sending, posting, replying, deleting, buying, uploading,
                 downloading, agreeing to terms, changing account settings —
                 and before reaching private data for the first time.
                 Browsing and reading do not ask. This is the default.

    auto         do not ask. For when the user is watching and wants it to just go.

**auto is not "unsafe".** It removes the *question*, never a protection. The
things that are refused outright are refused in every mode and cannot be
switched off here: typing a password or card number, reaching the private
network or a router console, downloading an executable, and any capability
switched off in settings. Stop, Pause and Take Control are unaffected. What auto
changes is that SYRIX stops interrupting to ask — not what it is willing to do.

The mode lives in memory for the session and resets to the default on exit, so a
permissive setting can never outlive the session it was chosen for.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, Optional

MODE_ASK_ALWAYS = "ask_always"
MODE_BALANCED = "balanced"
MODE_AUTO = "auto"
MODES = (MODE_ASK_ALWAYS, MODE_BALANCED, MODE_AUTO)

DEFAULT_MODE = MODE_BALANCED

LABELS = {
    MODE_ASK_ALWAYS: "Ask every time",
    MODE_BALANCED: "Ask for risky things",
    MODE_AUTO: "Don't ask",
}
HINTS = {
    MODE_ASK_ALWAYS: "Stops before opening any site. It can search and read, nothing else.",
    MODE_BALANCED: "Browses freely. Stops before sending, posting, buying, deleting or downloading.",
    MODE_AUTO: ("Works without stopping. Still asks before sending, submitting or "
                "uploading, and still refuses passwords and unsafe files."),
}

# ── action classes ─────────────────────────────────────────────────────────
# Free: costs nothing and reveals nothing. Never asks, in any mode.
CLASS_FREE = "free"
# Reaching out: opening a page, clicking, typing. Visible to the site.
CLASS_BROWSE = "browse"
# Private: reading the user's own accounts and documents.
CLASS_PRIVATE = "private"
# Consequential: someone else can see it, or it costs money, or it destroys
# something. This is the class that matters.
CLASS_RISKY = "risky"

_ACTION_CLASS: Dict[str, str] = {
    # free
    "read_page": CLASS_FREE, "find_element": CLASS_FREE, "screenshot": CLASS_FREE,
    "web_search": CLASS_FREE, "workspace_read": CLASS_FREE, "workspace_list": CLASS_FREE,
    "wait_for": CLASS_FREE,
    # browse
    "open_url": CLASS_BROWSE, "scroll": CLASS_BROWSE, "back": CLASS_BROWSE,
    "forward": CLASS_BROWSE, "reload": CLASS_BROWSE,
    "click": CLASS_BROWSE, "type_text": CLASS_BROWSE,
    "press_key": CLASS_BROWSE, "hover": CLASS_BROWSE, "select_option": CLASS_BROWSE,
    "drag": CLASS_BROWSE,
    "new_tab": CLASS_BROWSE, "switch_tab": CLASS_BROWSE, "close_tab": CLASS_BROWSE,
    "workspace_write": CLASS_BROWSE, "create_document": CLASS_BROWSE,
    "start_preview": CLASS_BROWSE, "preview_document": CLASS_BROWSE,
    "consult_assistant": CLASS_BROWSE,
    # Running the agent's own code is not "reaching out" — the sandbox has no
    # network, no shell, no readable secrets and cannot write outside the task
    # workspace. It is arithmetic and a chart, so it costs nothing and reveals
    # nothing. See syrix_supercomputer.sandbox_exec for what enforces that.
    "run_python": CLASS_FREE,
    # private
    "read_email": CLASS_PRIVATE, "read_private_document": CLASS_PRIVATE,
    "use_account": CLASS_PRIVATE,
    "file_read": CLASS_PRIVATE, "folder_list": CLASS_PRIVATE,
    # risky
    "send_email": CLASS_RISKY, "send_message": CLASS_RISKY,
    "draft_message": CLASS_RISKY, "publish": CLASS_RISKY,
    "delete_online_content": CLASS_RISKY,
    "purchase": CLASS_RISKY, "upload": CLASS_RISKY,
    # Declared so it is a known action rather than an unknown one; gate.execute
    # refuses it outright before any mode is consulted.
    "place_trade": CLASS_RISKY,
    "download": CLASS_RISKY, "submit_form": CLASS_RISKY,
    "agree_to_terms": CLASS_RISKY, "change_account_settings": CLASS_RISKY,
    "export": CLASS_RISKY,
}

# Which classes each mode stops for. Disjoint by construction: an action has one
# class, and a mode either lists that class or it does not.
_ASKS_FOR = {
    MODE_ASK_ALWAYS: {CLASS_BROWSE, CLASS_PRIVATE, CLASS_RISKY},
    MODE_BALANCED: {CLASS_PRIVATE, CLASS_RISKY},
    MODE_AUTO: set(),
}

# Actions that raise a card in EVERY mode, including "don't ask".
#
# The three here share one property: they put something of the user's in front of
# someone else, irreversibly. An email is sent, an application is submitted, a
# file leaves the machine. "Don't ask" exists so the user does not have to
# approve every click while watching it work — it was never meant to cover
# sending personal documents to a third party without review.
#
# This is checked BEFORE the mode's own table, so no mode can clear it, and the
# capability being ON does not weaken it: on is what makes the card reachable,
# the card is what authorises the act.
ALWAYS_ASK = frozenset({
    "send_email", "send_message", "submit_form", "upload",
    "purchase", "delete_online_content", "change_account_settings",
    "agree_to_terms", "publish",
})

_LOCK = threading.RLock()
_mode: str = DEFAULT_MODE


def classify(action: str) -> str:
    """The class of an action. Unknown actions are risky — deny-unknown applies
    to "how much do we care" as much as to "is it allowed"."""
    return _ACTION_CLASS.get(str(action or "").strip().lower(), CLASS_RISKY)


def mode() -> str:
    with _LOCK:
        return _mode


def set_mode(value: str) -> str:
    """Choose the mode. Unknown values are ignored rather than guessed at."""
    global _mode
    v = str(value or "").strip().lower()
    if v in MODES:
        with _LOCK:
            _mode = v
    return mode()


def reset() -> None:
    """Back to the default. Called on mode exit so a permissive choice cannot
    outlive the session it was made for."""
    global _mode
    with _LOCK:
        _mode = DEFAULT_MODE


def always_asks(action: str, config: Optional[Dict[str, Any]] = None) -> bool:
    """True for the actions no mode may wave through. See :data:`ALWAYS_ASK`."""
    if not bool((config or {}).get("supercomputer_always_ask_enabled", True)):
        return False
    return str(action or "").strip().lower() in ALWAYS_ASK


def asks_for(action: str, *, current: Optional[str] = None,
             config: Optional[Dict[str, Any]] = None) -> bool:
    """Whether SYRIX stops to ask before ``action``.

    The always-ask set is checked first, so "don't ask" mode still stops before
    anything irreversible that another person will see.
    """
    if always_asks(action, config):
        return True
    return classify(action) in _ASKS_FOR.get(current or mode(), set())


def explain(action: str, *, current: Optional[str] = None,
            config: Optional[Dict[str, Any]] = None) -> str:
    """Why this action is being asked about, in one line for the card."""
    if always_asks(action, config) and mode() == MODE_AUTO:
        return ("Even on “don't ask” I stop for this one: it leaves your "
                "machine and someone else sees it.")
    return {
        CLASS_RISKY: "This one can be seen by someone else, costs money, or cannot be undone.",
        CLASS_PRIVATE: "This reaches your own accounts or private files.",
        CLASS_BROWSE: "You have asked me to check before I touch anything on the web.",
    }.get(classify(action), "")


def snapshot() -> Dict[str, Any]:
    """For the chat-bar switcher."""
    return {
        "mode": mode(),
        "modes": [{"key": k, "label": LABELS[k], "hint": HINTS[k]} for k in MODES],
        "always_ask": sorted(ALWAYS_ASK),
    }


def _reset_for_tests() -> None:
    reset()


__all__ = [
    "MODE_ASK_ALWAYS", "MODE_BALANCED", "MODE_AUTO", "MODES", "DEFAULT_MODE",
    "CLASS_FREE", "CLASS_BROWSE", "CLASS_PRIVATE", "CLASS_RISKY",
    "ALWAYS_ASK", "always_asks",
    "classify", "mode", "set_mode", "reset", "asks_for", "explain", "snapshot",
]
