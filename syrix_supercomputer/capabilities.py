"""Capability registry — what Supercomputer is even able to do.

A capability is a class of action, not an individual action. It answers "could
this ever happen?", which is a settings question. Whether a *particular* action
happens is a separate question answered by a permission card every single time.

Two independent layers, and both must say yes:

    capability ON  (settings, durable)   AND   permission granted (card, per-use)

A capability that is OFF is **technically unavailable**, not hidden: it is
absent from the tool surface the model sees, and the gate refuses it even if
something asks anyway. Turning one on grants no authority by itself — it only
makes the permission card reachable.

Destructive and account-changing capabilities default OFF. The default-ON set is
deliberately tiny: read-only browsing inside the task's scope, reading what is
on the page, and writing files inside the task's own isolated workspace.

Every capability here must have a TOOL behind it
------------------------------------------------
Until 2026-07-26 this registry declared thirteen actions that no tool could ever
call — ``file_read``, ``folder_list``, ``upload``, ``clipboard_*``,
``file_write``, ``open_project``, ``edit_syrix`` and friends. They were
classified in :mod:`syrix_supercomputer.safety_mode` and rendered as switches on
the Settings page, and flipping any of them did precisely nothing. A switch that
does nothing is worse than a missing feature: it tells the user he has granted
something.

That is now fixed in both directions. ``file_read``/``folder_list`` and
``upload`` gained real tools (:mod:`syrix_supercomputer.local_files`), and the
rest were deleted. If you add a capability here, add the tool in the same
change — ``tests/test_supercomputer_capabilities_wired.py`` fails otherwise.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# ── capability ids ─────────────────────────────────────────────────────────
CAP_BROWSE = "browse"
CAP_READ_PAGE = "read_page"
CAP_INTERACT = "interact"
CAP_WORKSPACE_FILES = "workspace_files"

CAP_COMPUTE = "compute"
CAP_CONSULT_ASSISTANT = "consult_assistant"

CAP_FILE_READ = "file_read"
CAP_UPLOADS = "uploads"
CAP_DOWNLOADS = "downloads"
CAP_AUTHENTICATED_SITES = "authenticated_sites"
CAP_READ_PRIVATE_DOCUMENTS = "read_private_documents"
CAP_DRAFT_MESSAGES = "draft_messages"
CAP_SEND_MESSAGES = "send_messages"
CAP_SUBMIT_FORMS = "submit_forms"
CAP_PUBLISH = "publish"
CAP_DELETE_ONLINE_CONTENT = "delete_online_content"
CAP_ACCOUNT_SETTINGS = "account_settings"
CAP_PAYMENTS = "payments"
CAP_AGREE_TO_TERMS = "agree_to_terms"
CAP_EXPORT_OUTSIDE_CHAT = "export_outside_chat"


@dataclass(frozen=True)
class Capability:
    key: str
    label: str
    # config.json flag. None means "always on, no flag" (the baseline set).
    flag: Optional[str]
    default_on: bool
    # Actions that require it. The gate maps action -> capability through these.
    actions: tuple
    # True for anything that changes the world outside SYRIX. Used by the UI to
    # group them and by the gate to require a confirmation card.
    side_effecting: bool = True


CAPABILITIES: Dict[str, Capability] = {
    # ── default ON: read-only, or confined to the task's own workspace ──
    CAP_BROWSE: Capability(
        CAP_BROWSE, "Browse websites in scope", "supercomputer_browser_enabled", True,
        ("open_url", "scroll", "back", "forward", "reload", "close_tab", "wait_for"),
        side_effecting=False,
    ),
    CAP_READ_PAGE: Capability(
        CAP_READ_PAGE, "Read the current page", None, True,
        ("read_page", "find_element", "screenshot", "web_search"), side_effecting=False,
    ),
    # Clicking a link and typing in a search box are how you *browse*, not how
    # you change the world — an agent that needs a card per keystroke cannot
    # complete a single real task. The consequential moves (send, post, buy,
    # delete) are classified separately in agent_policy.py and routed to the
    # side-effecting capabilities below, so they still stop for a card.
    CAP_INTERACT: Capability(
        CAP_INTERACT, "Click and type on pages", "supercomputer_capability_interact", True,
        ("click", "type_text", "switch_tab", "new_tab",
         # Added 2026-07-26. A chart tool is drawn with a DRAG, an indicator is
         # added with a KEYBOARD SHORTCUT, and a timeframe is a DROPDOWN — none
         # of which the click/type/scroll trio could express, which is why
         # "open TradingView and add a fib retracement" was not doable at all.
         "press_key", "hover", "select_option", "drag"),
        side_effecting=False,
    ),
    CAP_WORKSPACE_FILES: Capability(
        CAP_WORKSPACE_FILES, "Create files in this task's workspace", None, True,
        ("workspace_write", "workspace_read", "workspace_list", "create_document",
         "start_preview", "preview_document"),
        side_effecting=False,
    ),
    # Running code the agent wrote. Default ON and not side-effecting, because
    # the sandbox it runs in has no network, no shell, no readable secrets and
    # cannot write outside the task workspace — so it can compute and draw, and
    # that is all. See syrix_supercomputer.sandbox_exec for what enforces that.
    CAP_COMPUTE: Capability(
        CAP_COMPUTE, "Do calculations and draw charts",
        "supercomputer_capability_compute", True, ("run_python",),
        side_effecting=False,
    ),
    # Asking a public assistant, as the LAST rung of the escalation ladder. It
    # is ordinary browsing — open a site, type, read the reply — so it is not
    # side-effecting; what makes it special is that the answer is labelled
    # secondhand and unverified in the report.
    CAP_CONSULT_ASSISTANT: Capability(
        CAP_CONSULT_ASSISTANT, "Ask another AI when every source has failed",
        "supercomputer_capability_consult_assistant", True, ("consult_assistant",),
        side_effecting=False,
    ),

    # ── touches private data or the world ──
    # ON by default since 2026-07-26, because "submit the CV in my Downloads"
    # was otherwise impossible — but strictly read-only, allowlisted to a few
    # folders, and every single use raises a card naming the exact file.
    CAP_FILE_READ: Capability(
        CAP_FILE_READ, "Read files from your Downloads, Documents, Desktop and Pictures",
        "supercomputer_capability_file_read", True, ("file_read", "folder_list"),
    ),
    # ON, and pinned to ALWAYS ask — see safety_mode.ALWAYS_ASK. Sending a file
    # of the user's to a website is exactly the moment to stop and show him what is
    # about to leave the machine, in every safety mode including "don't ask".
    CAP_UPLOADS: Capability(
        CAP_UPLOADS, "Attach your files to a web form", "supercomputer_capability_uploads",
        True, ("upload",),
    ),
    CAP_DOWNLOADS: Capability(
        CAP_DOWNLOADS, "Download files", "supercomputer_capability_downloads", False, ("download",),
    ),
    # Default ON since the browser now runs in the user's signed-in profile — that
    # is the premise of the mode, so "off" would mean the browser is logged in
    # but the agent may not look at anything. Still side-effecting, so opening
    # an account surface raises a card the first time in each task.
    CAP_AUTHENTICATED_SITES: Capability(
        CAP_AUTHENTICATED_SITES, "Use a signed-in account",
        "supercomputer_capability_authenticated_sites", True, ("use_account",),
    ),
    CAP_READ_PRIVATE_DOCUMENTS: Capability(
        CAP_READ_PRIVATE_DOCUMENTS, "Read email or private documents",
        "supercomputer_capability_read_private_documents", True,
        ("read_private_document",),
    ),
    # Drafting is not sending. A draft sits in the account unsent and is the
    # explicitly requested half of "draft replies but do not send" — so it is
    # available, and asks, while send_email stays off.
    CAP_DRAFT_MESSAGES: Capability(
        CAP_DRAFT_MESSAGES, "Write drafts (never sends them)",
        "supercomputer_capability_draft_messages", True, ("draft_message",),
    ),
    CAP_SEND_MESSAGES: Capability(
        CAP_SEND_MESSAGES, "Send emails and messages",
        "supercomputer_capability_send_messages", False,
        ("send_email", "send_message"),
    ),
    # ON since 2026-07-26, and pinned to ALWAYS ask. A job application that
    # stops dead at the Submit button is not a completed task — but a form
    # submitted while the user is not looking is not acceptable either, so the
    # answer is "available, and it always shows you first".
    CAP_SUBMIT_FORMS: Capability(
        CAP_SUBMIT_FORMS, "Fill in and submit forms (always asks first)",
        "supercomputer_capability_submit_forms", True, ("submit_form",),
    ),
    CAP_PUBLISH: Capability(
        CAP_PUBLISH, "Publish content", "supercomputer_capability_publish", False, ("publish",),
    ),
    CAP_DELETE_ONLINE_CONTENT: Capability(
        CAP_DELETE_ONLINE_CONTENT, "Delete online content",
        "supercomputer_capability_delete_online_content", False,
        ("delete_online_content",),
    ),
    CAP_ACCOUNT_SETTINGS: Capability(
        CAP_ACCOUNT_SETTINGS, "Change account settings",
        "supercomputer_capability_account_settings", False, ("change_account_settings",),
    ),
    CAP_PAYMENTS: Capability(
        # `place_trade` is declared here so the action is known rather than
        # merely unrecognised, and so it is off by default like every other
        # money action. It never actually reaches this check: gate.execute
        # refuses it outright, before capabilities, in every safety mode. This
        # is the layer underneath that — if the outright refusal were ever
        # removed, a trade would still be off by default and card-gated.
        CAP_PAYMENTS, "Make purchases or payments",
        "supercomputer_capability_payments", False, ("purchase", "place_trade"),
    ),
    CAP_AGREE_TO_TERMS: Capability(
        CAP_AGREE_TO_TERMS, "Agree to terms on your behalf",
        "supercomputer_capability_agree_to_terms", False, ("agree_to_terms",),
    ),
    CAP_EXPORT_OUTSIDE_CHAT: Capability(
        CAP_EXPORT_OUTSIDE_CHAT, "Export information outside this chat",
        "supercomputer_capability_export_outside_chat", False, ("export",),
    ),
}

# action -> capability key, built once from the table above.
_ACTION_TO_CAP: Dict[str, str] = {
    action: cap.key for cap in CAPABILITIES.values() for action in cap.actions
}


def capability_for(action: str) -> Optional[Capability]:
    """The capability an action belongs to, or None when the action is unknown.

    Unknown actions are refused by the gate — deny-unknown, so adding a verb
    without declaring it here fails closed rather than running ungated.
    """
    key = _ACTION_TO_CAP.get(str(action or "").strip().lower())
    return CAPABILITIES.get(key) if key else None


def is_enabled(action: str, config: Optional[Dict[str, Any]] = None) -> bool:
    """True when the capability behind ``action`` is switched on."""
    cap = capability_for(action)
    if cap is None:
        return False
    if cap.flag is None:
        return True
    return bool((config or {}).get(cap.flag, cap.default_on))


def requires_confirmation(action: str) -> bool:
    """Side-effecting actions always raise a card, no matter what is enabled."""
    cap = capability_for(action)
    return bool(cap and cap.side_effecting)


# How many times one approval may be spent before the card comes back.
# Reading is repetitive by nature — approving "read this inbox" once and then
# being asked again for every message is how a permission system trains people
# to click Allow without reading it. Anything that changes the world stays
# single-use: one approval, one action, every time.
_GRANT_USES = {
    CAP_READ_PRIVATE_DOCUMENTS: 40,
    CAP_AUTHENTICATED_SITES: 40,
    CAP_FILE_READ: 20,
    CAP_DOWNLOADS: 3,
    CAP_DRAFT_MESSAGES: 5,
}


def grant_uses(action: str) -> int:
    """Uses a single approval of ``action`` buys. Defaults to one."""
    cap = capability_for(action)
    return int(_GRANT_USES.get(cap.key, 1)) if cap else 1


def enabled_actions(config: Optional[Dict[str, Any]] = None) -> List[str]:
    """Every action currently available, for building the model's tool surface.

    A disabled capability's actions are simply absent, which is what makes
    "technically unavailable" true rather than merely hidden.
    """
    return sorted(a for a in _ACTION_TO_CAP if is_enabled(a, config))


def snapshot(config: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """HUD-facing capability list for the settings page."""
    return [
        {
            "key": cap.key,
            "label": cap.label,
            "flag": cap.flag,
            "enabled": bool(cap.flag is None or (config or {}).get(cap.flag, cap.default_on)),
            "default_on": cap.default_on,
            "side_effecting": cap.side_effecting,
            "locked": cap.flag is None,
        }
        for cap in CAPABILITIES.values()
    ]


__all__ = [
    "Capability", "CAPABILITIES", "capability_for", "is_enabled",
    "requires_confirmation", "grant_uses", "enabled_actions", "snapshot",
]
