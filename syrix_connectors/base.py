"""SYRIX Connectors — core contracts (framework schema v1).

Every connector implements :class:`ConnectorBase` and declares its actions as
:class:`ConnectorAction` records. Nothing executes a connector action except
:func:`syrix_connectors.manager.execute` — the single choke point that
enforces the kill switch, connection state, schema version, granular
permissions, the confirmation gate, timeouts and the audit log.

Schema versioning
-----------------
``SCHEMA_VERSION`` is the framework contract version. Each connector class
declares the version it was built against in ``schema_version``; the manager
refuses to load or execute a connector whose declared version does not match,
surfacing health ``needs_update`` instead of misbehaving at runtime. The
on-disk state file carries the same version so a future migration has a
hook point.

Permission model
----------------
Each action carries a *verb* (read/search/summarize/create/modify/delete/
send/execute). Safe verbs (read/search/summarize) default to **allowed** when
The user connects (they just OAuth-consented to exactly those scopes);
destructive verbs default to **blocked**. Unknown action keys are always
denied. Actions with risk ``not_implemented`` are rendered as Blocked in the
HUD and can never execute — and are never exposed to the brain as tools.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

# Framework contract version — bump only with a migration path.
SCHEMA_VERSION = 1

# ── Permission verbs ─────────────────────────────────────────────────────────
PERM_READ = "read"
PERM_SEARCH = "search"
PERM_SUMMARIZE = "summarize"
PERM_CREATE = "create"
PERM_MODIFY = "modify"
PERM_DELETE = "delete"
PERM_SEND = "send"
PERM_EXECUTE = "execute"

SAFE_VERBS = frozenset({PERM_READ, PERM_SEARCH, PERM_SUMMARIZE})
DESTRUCTIVE_VERBS = frozenset({PERM_CREATE, PERM_MODIFY, PERM_DELETE, PERM_SEND, PERM_EXECUTE})
ALL_VERBS = SAFE_VERBS | DESTRUCTIVE_VERBS

# ── Action risk levels ───────────────────────────────────────────────────────
RISK_SAFE = "safe"                      # runs directly once permission allows
RISK_CONFIRM = "confirm"                # requires an explicit user confirmation per call
RISK_NOT_IMPLEMENTED = "not_implemented"  # declared for the UI, can never execute

# ── Connector health states ──────────────────────────────────────────────────
HEALTH_CONNECTED = "connected"          # authenticated, ready
HEALTH_AVAILABLE = "available"          # implemented, not connected yet
HEALTH_COMING_SOON = "coming_soon"      # in the catalogue, not implemented yet
HEALTH_NEEDS_REAUTH = "needs_reauth"    # token present but refresh failed / revoked
HEALTH_NEEDS_SETUP = "needs_setup"      # missing prerequisite (client secrets, DPAPI, ...)
HEALTH_NEEDS_UPDATE = "needs_update"    # connector schema_version != framework SCHEMA_VERSION
HEALTH_ERROR = "error"                  # last operation failed in an unexpected way
HEALTH_DISABLED = "disabled"            # master kill switch off

ALL_HEALTH = frozenset({
    HEALTH_CONNECTED, HEALTH_AVAILABLE, HEALTH_COMING_SOON, HEALTH_NEEDS_REAUTH,
    HEALTH_NEEDS_SETUP, HEALTH_NEEDS_UPDATE, HEALTH_ERROR, HEALTH_DISABLED,
})


class NeedsReauth(Exception):
    """Raised by a connector when stored credentials can no longer be used
    (refresh failed / user revoked access). The manager converts this into
    health ``needs_reauth`` + an island notice instead of a generic error."""


@dataclass(frozen=True)
class ConnectorAction:
    """One capability a connector offers (and the unit permissions apply to)."""

    key: str                 # unique within the connector, e.g. "gmail_search"
    label: str               # HUD label, e.g. "Search Emails"
    description: str         # HUD sub-label, e.g. "Search and find emails"
    verb: str                # one of ALL_VERBS
    risk: str = RISK_SAFE    # RISK_SAFE | RISK_CONFIRM | RISK_NOT_IMPLEMENTED
    timeout_s: float = 12.0  # execution budget enforced by the manager
    # OpenAI function-calling schema; None → never offered to the brain.
    tool_schema: Optional[Dict[str, Any]] = None
    # Explicit product decision to seed a non-safe verb as allowed (e.g.
    # Gmail draft creation: nothing ever leaves the account, fully reversible,
    # and the user explicitly authorised it). Never applies to not_implemented.
    default_allowed: bool = False


@dataclass(frozen=True)
class BackgroundTask:
    """A safe, individually-toggleable background job a connector offers."""

    key: str
    label: str
    description: str
    interval_s: float = 1800.0
    default_enabled: bool = False  # background work is opt-in, always


@dataclass
class ActionResult:
    """Outcome of one action execution. ``content`` is what the brain reads."""

    ok: bool
    content: str = ""
    error: str = ""            # machine code: permission_denied, not_connected, ...
    human_message: str = ""    # user-facing one-liner for HUD/voice
    latency_ms: float = 0.0
    count: Optional[int] = None          # e.g. number of emails touched (audit)
    needs_confirmation: bool = False
    confirm_id: str = ""


class ConnectorBase(abc.ABC):
    """Interface every connector implements. No connector bypasses the manager."""

    # Identity (class attributes on implementations).
    id: str = ""
    name: str = ""
    description: str = ""
    icon: str = ""             # HUD icon key (renderer maps it to an SVG)
    category: str = ""         # "communication", "productivity", "development", ...
    # Fixed first-party setup page used by the HUD for account sign-in/token setup.
    setup_url: str = ""
    auth_kind: str = "none"    # "oauth_google" | "token" | "none"
    schema_version: int = 0    # implementations MUST set this to SCHEMA_VERSION
    background_tasks: Tuple[BackgroundTask, ...] = ()

    @abc.abstractmethod
    def actions(self) -> Dict[str, ConnectorAction]:
        """Ordered mapping of action_key → ConnectorAction (stable order for UI)."""

    @abc.abstractmethod
    def execute(self, action_key: str, args: Dict[str, Any], *,
                config: Dict[str, Any], request_id: str = "") -> ActionResult:
        """Run one action. Called ONLY by the manager, after every gate passed."""

    @abc.abstractmethod
    def connect(self, *, config: Dict[str, Any], progress=None) -> Tuple[bool, str]:
        """Authenticate with the external service.

        Blocking (the manager runs it on a worker thread). ``progress`` is an
        optional ``fn(status: str, detail: str)`` callback for HUD updates.
        Returns ``(True, account_label)`` or ``(False, error_code)``.
        """

    @abc.abstractmethod
    def disconnect(self, *, config: Dict[str, Any]) -> None:
        """Revoke credentials (best-effort) and delete every stored token."""

    @abc.abstractmethod
    def health(self, *, config: Dict[str, Any]) -> str:
        """Current health state (one of ALL_HEALTH, minus coming_soon/disabled)."""

    def run_task(self, task_key: str, *, config: Dict[str, Any]) -> Optional[str]:
        """Run one background task; return a short notice text or None."""
        return None

    def claims_query(self, text: str) -> bool:
        """True when this query is about THIS connector's personal-data domain
        (e.g. Gmail claims "do I have unread emails"). The runtime spine uses
        it to route the turn to the tool-calling brain instead of web
        research — web grounding can never see the user's own data. Only
        consulted for connected connectors with ≥1 user-allowed tool, so a
        False default keeps every other connector neutral."""
        return False

    def suggest_action(self, text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Deterministic (action_key, args) for an unambiguous read query, or
        None. Lets the brain SKIP the LLM tool-selection round for the common
        cases ("do I have unread emails" → gmail_list_unread) — halving the
        latency (one synthesis call instead of select + synthesise). Return
        None whenever the request needs the model to decide or to write
        content (drafts, reading a specific message), so those still go through
        the normal tool-calling round. The caller re-checks the action is
        connected + permitted before executing, so a wrong guess is safe."""
        return None

    def setup_hint(self) -> str:
        """Non-secret setup guidance safe to render in the HUD."""
        return ""


def mask_account(label: str) -> str:
    """Mask an account identifier for HUD display: ``kab…@gmail.com``."""
    s = str(label or "").strip()
    if not s:
        return ""
    if "@" in s:
        local, _, domain = s.partition("@")
        keep = local[:3] if len(local) > 3 else local[:1]
        return f"{keep}…@{domain}"
    return (s[:4] + "…") if len(s) > 5 else s


__all__ = [
    "SCHEMA_VERSION",
    "PERM_READ", "PERM_SEARCH", "PERM_SUMMARIZE", "PERM_CREATE",
    "PERM_MODIFY", "PERM_DELETE", "PERM_SEND", "PERM_EXECUTE",
    "SAFE_VERBS", "DESTRUCTIVE_VERBS", "ALL_VERBS",
    "RISK_SAFE", "RISK_CONFIRM", "RISK_NOT_IMPLEMENTED",
    "HEALTH_CONNECTED", "HEALTH_AVAILABLE", "HEALTH_COMING_SOON",
    "HEALTH_NEEDS_REAUTH", "HEALTH_NEEDS_SETUP", "HEALTH_NEEDS_UPDATE",
    "HEALTH_ERROR", "HEALTH_DISABLED", "ALL_HEALTH",
    "ConnectorAction", "BackgroundTask", "ActionResult", "ConnectorBase",
    "NeedsReauth", "mask_account",
]
