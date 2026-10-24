"""Connector actions → brain tools (OpenAI function-calling schemas).

The hard guarantee (user requirement): **an action the user cannot run right
now is never visible to the brain.** :func:`list_tool_schemas` re-derives the
surface on every turn from three live gates:

    connector health == connected
    AND action.risk != not_implemented
    AND the user's permission toggle for that action == allowed
    AND the action declares a tool_schema at all

So blocking "Search Emails" in the Connectors page removes the
``gmail_search`` tool from the very next brain call — the model cannot even
attempt it. Execution goes back through :func:`syrix_connectors.manager.execute`
(actor="brain"), which re-checks every gate anyway (defence in depth) and
emits the "Using <name> connector" thinking-panel line.

Wired into the existing seams:
  * ``syrix_core_v2/adapters/brain_adapter.py`` appends
    :func:`list_tool_schemas` next to its MCP tool injection.
  * ``syrix_core_v2/tool_executor.py`` falls through to :func:`execute_tool`
    for names :func:`is_connector_tool` claims.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import syrix_obs as obs

from . import manager, permissions, registry
from .base import HEALTH_CONNECTED, RISK_NOT_IMPLEMENTED


# Connector tools can expose private account data. Domain-word matching alone
# is insufficient: a user may be discussing a connector, giving a synthetic
# example, or explicitly saying not to access it. This connector-agnostic guard
# protects every current and future connector before its own claim heuristic.
_NON_ACTION_CONNECTOR_CONTEXT_RE = re.compile(
    r"\b(?:synthetic|hypothetical|fictional|imaginary)\s+(?:example|test|case|scenario)\b|"
    r"\b(?:for\s+example|as\s+an\s+example|suppose|imagine)\b|"
    r"\b(?:a\s+user|the\s+user|someone|somebody)\s+(?:asks?|says?|wants?)\b|"
    r"\b(?:explain|describe|discuss|state)\s+(?:your\s+)?(?:privacy|permission|authorization|capability)\s+(?:boundary|boundaries|policy|rules?|limits?)\b|"
    r"\b(?:do\s+not|don['’]?t|must\s+not|should\s+not|never)\s+(?:access|open|read|search|check|use|connect)\b|"
    r"\bwithout\s+(?:my\s+|the\s+user['’]?s\s+|explicit\s+)?(?:permission|authorization|authorisation|consent)\b|"
    r"\bnot\s+(?:explicitly\s+)?authori[sz]ed\b",
    re.IGNORECASE,
)


def _is_affirmative_connector_request(text: str) -> bool:
    """False for meta, hypothetical, or explicitly denied actions."""
    return not bool(_NON_ACTION_CONNECTOR_CONTEXT_RE.search(str(text or "")))


def _live_actions(config: Optional[Dict[str, Any]]) -> List[Tuple[str, Any]]:
    """(connector_id, ConnectorAction) pairs that pass every exposure gate."""
    out: List[Tuple[str, Any]] = []
    if not manager.tools_enabled(config):
        return out
    for e in registry.CATALOGUE:
        if not e.implemented or not permissions.is_connected(e.id):
            continue
        if manager.connector_health(e.id, config) != HEALTH_CONNECTED:
            continue
        inst = registry.get_connector(e.id)
        if inst is None:
            continue
        try:
            actions = inst.actions()
        except Exception as exc:
            obs.report("connectors.tool_surface.actions", exc, connector=e.id)
            continue
        for action in actions.values():
            if action.tool_schema is None or action.risk == RISK_NOT_IMPLEMENTED:
                continue
            if not permissions.is_allowed(e.id, action):
                continue
            out.append((e.id, action))
    return out


def list_tool_schemas(config: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Schemas for every currently-exposable connector action."""
    schemas: List[Dict[str, Any]] = []
    for _cid, action in _live_actions(config):
        schema = action.tool_schema
        if isinstance(schema, dict) and schema.get("type") == "function":
            schemas.append(schema)
    return schemas


def lookup(tool_name: str) -> Optional[Tuple[str, str]]:
    """Map a tool name → (connector_id, action_key), else None.

    Uses the static catalogue (not the permission gates) so the executor can
    route a call it received; the manager then enforces the gates and returns
    a clean denial if the user flipped a toggle mid-turn.
    """
    name = str(tool_name or "").strip()
    if not name:
        return None
    for e in registry.CATALOGUE:
        if not e.implemented:
            continue
        inst = registry.get_connector(e.id)
        if inst is None:
            continue
        try:
            action = inst.actions().get(name)
        except Exception as exc:
            obs.report("connectors.tool_surface.lookup", exc, connector=e.id)
            continue
        if action is not None and action.tool_schema is not None:
            return (e.id, action.key)
    return None


def is_connector_tool(tool_name: str) -> bool:
    return lookup(tool_name) is not None


def connector_is_live(connector_id: str, config: Optional[Dict[str, Any]] = None) -> bool:
    """True when this connector is connected, healthy and exposes ≥1 tool
    right now — the precondition for honouring a remembered follow-up claim."""
    cid = str(connector_id or "").strip().lower()
    return bool(cid) and any(c == cid for c, _a in _live_actions(config))


def connector_tool_schemas(connector_id: str,
                           config: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Tool schemas for ONE connector's currently-exposable actions.

    Used to focus the brain's first tool-call round on the connector that
    claimed the turn: offering only its ~5 tools (instead of all ~23 built-in
    + connector schemas) makes selection faster, smaller on the wire, and
    keeps the model from wandering to web search for a personal-data ask.
    """
    cid = str(connector_id or "").strip().lower()
    out: List[Dict[str, Any]] = []
    for c, action in _live_actions(config):
        if c != cid:
            continue
        schema = action.tool_schema
        if isinstance(schema, dict) and schema.get("type") == "function":
            out.append(schema)
    return out


def claiming_connector(text: str, config: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Id of the first connected connector that claims this query's domain.

    A claim requires ALL of: connector connected + healthy, at least one
    tool currently exposable (user-allowed), and the connector's own
    ``claims_query`` heuristic matching (e.g. Gmail on personal-mailbox
    questions). Returns None otherwise — the caller falls back to normal
    routing, so this can only ever ADD a capability, never remove one.
    """
    q = str(text or "").strip()
    if not q or not _is_affirmative_connector_request(q):
        return None
    live = _live_actions(config)
    if not live:
        return None
    live_ids = list(dict.fromkeys(cid for cid, _a in live))

    # An explicitly named service wins before broad domain claims. Without
    # this, Gmail's intentionally broad "my email" claim could steal
    # "show my Outlook emails" simply because Gmail appears first.
    q_fold = re.sub(r"[_-]+", " ", q).casefold()
    aliases = {
        "google_calendar": ("google calendar", "gcal"),
        "google_drive": ("google drive",),
        "hostinger_mail": ("hostinger email", "hostinger mail"),
        "outlook": ("outlook", "office 365 mail"),
        "onedrive": ("onedrive", "one drive"),
        "ibkr": ("ibkr", "interactive brokers"),
        "teams": ("microsoft teams",),
    }
    explicit: List[str] = []
    for cid in live_ids:
        entry = registry.entry(cid)
        names = {cid.replace("_", " "), str(getattr(entry, "name", "") or "").casefold()}
        names.update(aliases.get(cid, ()))
        if any(name and re.search(rf"\b{re.escape(name.casefold())}\b", q_fold)
               for name in names):
            explicit.append(cid)
    for cid in explicit:
        inst = registry.get_connector(cid)
        if inst is None:
            continue
        try:
            if inst.claims_query(q):
                return cid
        except Exception as exc:
            obs.report("connectors.tool_surface.explicit_claim", exc, connector=cid)

    for cid in live_ids:  # preserve catalogue order
        inst = registry.get_connector(cid)
        if inst is None:
            continue
        try:
            if inst.claims_query(q):
                return cid
        except Exception as exc:
            obs.report("connectors.tool_surface.claims", exc, connector=cid)
    return None


def suggest_action(connector_id: str, text: str,
                   config: Optional[Dict[str, Any]] = None):
    """(action_key, args) the connector deterministically maps this query to,
    but ONLY when that action is currently exposable (connected + allowed +
    has a tool). Returns None otherwise so the caller falls back to the LLM
    tool-selection round. Lets the brain skip a whole LLM round-trip on the
    common read asks."""
    cid = str(connector_id or "").strip().lower()
    inst = registry.get_connector(cid)
    if inst is None:
        return None
    try:
        hit = inst.suggest_action(text)
    except Exception as exc:
        obs.report("connectors.tool_surface.suggest", exc, connector=cid)
        return None
    if not hit:
        return None
    action_key, args = hit
    live_keys = {a.key for c, a in _live_actions(config) if c == cid}
    if action_key not in live_keys:
        return None
    return (action_key, dict(args or {}))


def execute_tool(
    tool_name: str,
    args: Optional[Dict[str, Any]] = None,
    *,
    config: Optional[Dict[str, Any]] = None,
    request_id: str = "",
) -> Dict[str, Any]:
    """Execute a connector tool for the brain. Returns a plain dict so the
    caller (tool_executor) can wrap it in its own ToolResult type::

        {"success": bool, "content": str, "error": str, "latency_ms": float}
    """
    hit = lookup(tool_name)
    if hit is None:
        return {"success": False, "content": f"Unknown connector tool: {tool_name!r}.",
                "error": "unknown_tool", "latency_ms": 0.0}
    cid, akey = hit
    result = manager.execute(cid, akey, dict(args or {}), actor="brain",
                             request_id=request_id, config=config)
    return {
        "success": bool(result.ok),
        "content": result.content or result.human_message or "",
        "error": result.error or "",
        "latency_ms": float(result.latency_ms or 0.0),
    }


__all__ = ["list_tool_schemas", "lookup", "is_connector_tool",
           "claiming_connector", "connector_is_live", "connector_tool_schemas",
           "suggest_action", "execute_tool"]
