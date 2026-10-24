"""Connector manager — the single choke point for every connector operation.

Nothing in SYRIX executes a connector action except :func:`execute`. The gate
order is fixed and fully audited:

    kill switch → catalogue/implemented → schema version → connected →
    known action → not_implemented block → granular permission →
    confirmation gate (risk="confirm") → thinking-panel announce →
    timeout-budgeted run → audit → ActionResult

Confirmation system (connector-agnostic)
----------------------------------------
Any action declared with ``risk="confirm"`` — regardless of connector —
parks as a pending confirmation instead of running: the manager stores
``{confirm_id, connector, action, args, summary, expires}``, the HUD renders
a confirm/deny dialog from the state snapshot, and only an explicit
``confirm_action(confirm_id)`` executes it (``deny_action`` or a 120 s expiry
cancels). The gate lives HERE, keyed purely off the action's risk level, so
no connector can opt out and no connector needs its own implementation.

Config keys (all optional; defaults shown):
  connectors_enabled            true   — master kill switch
  connectors_tools_enabled      true   — expose actions to the brain as tools
  connectors_background_enabled true   — allow background tasks (scheduler)
"""
from __future__ import annotations

import threading
import time
import webbrowser
import uuid
from typing import Any, Callable, Dict, List, Optional

import syrix_obs as obs

from . import audit, events, permissions, registry, secure_store
from .base import (
    HEALTH_AVAILABLE,
    HEALTH_COMING_SOON,
    HEALTH_CONNECTED,
    HEALTH_DISABLED,
    HEALTH_ERROR,
    HEALTH_NEEDS_REAUTH,
    HEALTH_NEEDS_SETUP,
    HEALTH_NEEDS_UPDATE,
    RISK_CONFIRM,
    RISK_NOT_IMPLEMENTED,
    SCHEMA_VERSION,
    ActionResult,
    ConnectorAction,
    ConnectorBase,
    NeedsReauth,
)

_CONFIRM_TTL_S = 120.0
_CONNECT_BUDGET_S = 240.0  # browser consent can be slow; generous but bounded

_CONFIRM_LOCK = threading.RLock()
_PENDING_CONFIRMS: Dict[str, Dict[str, Any]] = {}

_AUTH_LOCK = threading.RLock()
_AUTH_FLOW: Dict[str, Any] = {}          # {"connector","status","detail","ts"}
_AUTH_THREADS: Dict[str, threading.Thread] = {}
_CONNECT_LAUNCH_TS: Dict[str, float] = {}
_CONNECT_DEDUPE_S = 3.0


def _open_external_browser(url: str) -> bool:
    """Open a provider page in the user's real default browser.

    The HUD installs a global ``webbrowser`` shim for ordinary web requests.
    OAuth consent and provider credential setup must never use that isolated
    BrowserView: the loopback redirect and the user's signed-in browser
    session both belong to the operating-system browser.
    """
    opener = getattr(webbrowser, "_syrix_hud_orig_open", None)
    if not callable(opener):
        opener = webbrowser.open
    return bool(opener(str(url), new=2))


def _open_provider_setup(connector_id: str, inst: Any, *,
                         on_change: Optional[Callable[[], None]] = None,
                         detail: str = "") -> bool:
    """Send a non-OAuth connector to its provider setup/sign-in page."""
    setup_url = str(getattr(inst, "setup_url", "") or "").strip()
    if not setup_url.startswith("https://"):
        return False
    try:
        if not _open_external_browser(setup_url):
            raise RuntimeError("default_browser_refused")
    except Exception as exc:
        obs.report("connectors.manager.open_setup", exc, connector=connector_id)
        _set_auth_flow(connector_id, "failed", "Could not open your default browser", on_change)
        return False
    _set_auth_flow(
        connector_id,
        "setup_opened",
        detail or "Finish provider setup in your default browser, then press Connect again.",
        on_change,
    )
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Config gates
# ─────────────────────────────────────────────────────────────────────────────
def is_enabled(config: Optional[Dict[str, Any]]) -> bool:
    return bool((config or {}).get("connectors_enabled", True))


def tools_enabled(config: Optional[Dict[str, Any]]) -> bool:
    return is_enabled(config) and bool((config or {}).get("connectors_tools_enabled", True))


def background_enabled(config: Optional[Dict[str, Any]]) -> bool:
    return is_enabled(config) and bool((config or {}).get("connectors_background_enabled", True))


# ─────────────────────────────────────────────────────────────────────────────
# Timeout budget (worker-thread pattern — same contract as tool_executor._safe)
# ─────────────────────────────────────────────────────────────────────────────
class ConnectorTimeout(Exception):
    """The action exceeded its declared budget; late results are discarded."""


def _run_budgeted(fn: Callable[[], Any], timeout_s: float, tag: str) -> Any:
    box: Dict[str, Any] = {}
    done = threading.Event()

    def _worker() -> None:
        try:
            box["r"] = fn()
        except Exception as exc:  # noqa: BLE001 — re-raised to the caller below
            box["e"] = exc
        finally:
            done.set()

    t = threading.Thread(target=_worker, daemon=True, name=f"connector:{tag}")
    t.start()
    if not done.wait(timeout=max(1.0, float(timeout_s))):
        raise ConnectorTimeout(f"{tag} exceeded {timeout_s:.0f}s budget")
    if "e" in box:
        raise box["e"]
    return box.get("r")


# ─────────────────────────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────────────────────────
def connector_health(connector_id: str, config: Optional[Dict[str, Any]]) -> str:
    """Resolve the effective health state for one catalogue entry."""
    if not is_enabled(config):
        return HEALTH_DISABLED
    e = registry.entry(connector_id)
    if e is None:
        return HEALTH_ERROR
    if not e.implemented:
        return HEALTH_COMING_SOON
    if registry.quarantine_reason(connector_id):
        return HEALTH_NEEDS_UPDATE
    inst = registry.get_connector(connector_id)
    if inst is None:
        return HEALTH_NEEDS_UPDATE
    override = str(permissions.connector_record(connector_id).get("health_override") or "")
    if override in (HEALTH_NEEDS_REAUTH, HEALTH_ERROR):
        return override
    try:
        return inst.health(config=dict(config or {}))
    except Exception as exc:
        obs.report("connectors.manager.health", exc, connector=connector_id)
        return HEALTH_ERROR


# ─────────────────────────────────────────────────────────────────────────────
# Execute — THE choke point
# ─────────────────────────────────────────────────────────────────────────────
def execute(
    connector_id: str,
    action_key: str,
    args: Optional[Dict[str, Any]] = None,
    *,
    actor: str = audit.ACTOR_BRAIN,
    request_id: str = "",
    reason: str = "",
    config: Optional[Dict[str, Any]] = None,
    _confirmed: bool = False,
) -> ActionResult:
    """Run one connector action through every gate. Never raises."""
    cid = str(connector_id or "").strip().lower()
    akey = str(action_key or "").strip()
    cfg = dict(config or {})

    def _deny(result_code: str, msg: str, log: bool = True) -> ActionResult:
        if log:
            audit.record(cid, akey, actor=actor, result=result_code,
                         reason=reason, request_id=request_id)
        return ActionResult(ok=False, error=result_code, human_message=msg,
                            content=msg)

    if not is_enabled(cfg):
        return _deny("disabled", "Connectors are turned off in settings.", log=False)

    entry = registry.entry(cid)
    if entry is None or not entry.implemented:
        return _deny("unknown_connector", f"No connector named {cid!r} is available.")

    inst = registry.get_connector(cid)
    if inst is None:
        detail = registry.quarantine_reason(cid) or "load_failed"
        return _deny("needs_update", f"The {entry.name} connector cannot load ({detail}).")

    if not permissions.is_connected(cid):
        return _deny(audit.RESULT_NOT_CONNECTED,
                     f"{entry.name} is not connected. Open the Connectors page to connect it.")

    action = inst.actions().get(akey)
    if action is None:
        return _deny(audit.RESULT_DENIED, f"{entry.name} has no action {akey!r}.")

    if action.risk == RISK_NOT_IMPLEMENTED:
        return _deny(audit.RESULT_BLOCKED,
                     f"{action.label} is not available in this version of SYRIX.")

    if not permissions.is_allowed(cid, action):
        return _deny(audit.RESULT_DENIED,
                     f"{action.label} is blocked for {entry.name}. "
                     f"You can allow it in the Connectors page.")

    if action.risk == RISK_CONFIRM and not _confirmed:
        confirm_id = _register_confirmation(cid, akey, dict(args or {}),
                                            entry.name, action.label,
                                            actor=actor, request_id=request_id,
                                            reason=reason)
        audit.record(cid, akey, actor=actor, result=audit.RESULT_CONFIRM_PENDING,
                     reason=reason, request_id=request_id)
        msg = (f"{action.label} on {entry.name} needs your confirmation — "
               f"check the Connectors panel.")
        return ActionResult(ok=False, needs_confirmation=True, confirm_id=confirm_id,
                            error="confirmation_required", human_message=msg, content=msg)

    # Thinking-panel line — EVERY execution announces which connector is in use.
    events.announce_usage(entry.name, action.label,
                          detail=str(reason or "")[:80], request_id=request_id)

    t0 = time.perf_counter()
    try:
        result = _run_budgeted(
            lambda: inst.execute(akey, dict(args or {}), config=cfg, request_id=request_id),
            timeout_s=action.timeout_s, tag=f"{cid}.{akey}",
        )
        if not isinstance(result, ActionResult):
            result = ActionResult(ok=True, content=str(result or ""))
    except ConnectorTimeout as exc:
        ms = (time.perf_counter() - t0) * 1000.0
        audit.record(cid, akey, actor=actor, result=audit.RESULT_TIMEOUT,
                     reason=reason, latency_ms=ms, request_id=request_id)
        events.announce_result(entry.name, action.label, ok=False,
                               summary="timed out", request_id=request_id)
        msg = f"{entry.name} took too long to respond. Try again in a moment."
        return ActionResult(ok=False, error="timeout", human_message=msg,
                            content=msg, latency_ms=ms)
    except NeedsReauth:
        ms = (time.perf_counter() - t0) * 1000.0
        permissions.set_health_override(cid, HEALTH_NEEDS_REAUTH,
                                        "stored credentials expired or revoked")
        audit.record(cid, akey, actor=actor, result="needs_reauth",
                     reason=reason, latency_ms=ms, request_id=request_id)
        events.lifecycle(entry.name, "needs_reauth")
        events.announce_result(entry.name, action.label, ok=False,
                               summary="needs re-authentication", request_id=request_id)
        msg = (f"{entry.name} needs to be reconnected — open the Connectors "
               f"page and sign in again.")
        return ActionResult(ok=False, error="needs_reauth", human_message=msg,
                            content=msg, latency_ms=ms)
    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000.0
        obs.report("connectors.manager.execute", exc, connector=cid, action=akey)
        audit.record(cid, akey, actor=actor, result=audit.RESULT_ERROR,
                     reason=reason, detail=type(exc).__name__,
                     latency_ms=ms, request_id=request_id)
        events.announce_result(entry.name, action.label, ok=False,
                               summary=type(exc).__name__, request_id=request_id)
        msg = f"{entry.name} request failed ({type(exc).__name__})."
        return ActionResult(ok=False, error="exec_failed", human_message=msg,
                            content=msg, latency_ms=ms)

    result.latency_ms = (time.perf_counter() - t0) * 1000.0
    audit.record(cid, akey, actor=actor,
                 result=audit.RESULT_OK if result.ok else (result.error or audit.RESULT_ERROR),
                 reason=reason, latency_ms=result.latency_ms,
                 count=result.count, request_id=request_id)
    events.announce_result(
        entry.name, action.label, ok=result.ok,
        summary=(f"{result.count} item(s)" if result.count is not None
                 else (result.human_message or ("done" if result.ok else result.error))),
        request_id=request_id,
    )
    if result.ok:
        permissions.update_last_sync(cid)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Confirmation registry (connector-agnostic)
# ─────────────────────────────────────────────────────────────────────────────
def _register_confirmation(cid: str, akey: str, args: Dict[str, Any],
                           connector_name: str, action_label: str, *,
                           actor: str, request_id: str, reason: str) -> str:
    confirm_id = uuid.uuid4().hex[:12]
    with _CONFIRM_LOCK:
        _prune_confirmations_unlocked()
        _PENDING_CONFIRMS[confirm_id] = {
            "confirm_id": confirm_id,
            "connector_id": cid,
            "connector": connector_name,
            "action_key": akey,
            "action_label": action_label,
            "args": args,
            "actor": actor,
            "request_id": request_id,
            "reason": str(reason or "")[:160],
            "created_ts": time.time(),
            "expires_ts": time.time() + _CONFIRM_TTL_S,
        }
    return confirm_id


def _prune_confirmations_unlocked() -> None:
    now = time.time()
    for key in [k for k, v in _PENDING_CONFIRMS.items() if v.get("expires_ts", 0) < now]:
        item = _PENDING_CONFIRMS.pop(key)
        audit.record(item.get("connector_id", ""), item.get("action_key", ""),
                     actor=item.get("actor", ""), result=audit.RESULT_CONFIRM_EXPIRED,
                     request_id=item.get("request_id", ""))


def pending_confirmations() -> List[Dict[str, Any]]:
    """Snapshot for the HUD — args omitted (only labels + ids leave Python)."""
    with _CONFIRM_LOCK:
        _prune_confirmations_unlocked()
        return [
            {k: v for k, v in item.items() if k != "args"}
            for item in _PENDING_CONFIRMS.values()
        ]


def confirm_action(confirm_id: str, *, config: Optional[Dict[str, Any]] = None) -> ActionResult:
    """User approved the pending action — execute it now (single use)."""
    with _CONFIRM_LOCK:
        _prune_confirmations_unlocked()
        item = _PENDING_CONFIRMS.pop(str(confirm_id or ""), None)
    if item is None:
        msg = "That confirmation has expired — please try the action again."
        return ActionResult(ok=False, error="confirmation_expired",
                            human_message=msg, content=msg)
    return execute(item["connector_id"], item["action_key"], item.get("args") or {},
                   actor=item.get("actor") or audit.ACTOR_HUD,
                   request_id=item.get("request_id", ""),
                   reason=item.get("reason", ""), config=config, _confirmed=True)


def deny_action(confirm_id: str) -> bool:
    """User rejected the pending action."""
    with _CONFIRM_LOCK:
        item = _PENDING_CONFIRMS.pop(str(confirm_id or ""), None)
    if item is None:
        return False
    audit.record(item.get("connector_id", ""), item.get("action_key", ""),
                 actor=item.get("actor", ""), result=audit.RESULT_CONFIRM_DENIED,
                 request_id=item.get("request_id", ""))
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Connect / disconnect
# ─────────────────────────────────────────────────────────────────────────────
def auth_flow_state() -> Optional[Dict[str, Any]]:
    with _AUTH_LOCK:
        return dict(_AUTH_FLOW) if _AUTH_FLOW else None


def _set_auth_flow(connector_id: str, status: str, detail: str = "",
                   on_change: Optional[Callable[[], None]] = None) -> None:
    with _AUTH_LOCK:
        if status == "idle":
            _AUTH_FLOW.clear()
        else:
            _AUTH_FLOW.update({
                "connector": connector_id, "status": status,
                "detail": str(detail or "")[:160], "ts": time.time(),
            })
    if callable(on_change):
        try:
            on_change()
        except Exception as exc:
            obs.report("connectors.manager.auth_flow_push", exc)


def start_connect(connector_id: str, *, config: Optional[Dict[str, Any]] = None,
                  on_change: Optional[Callable[[], None]] = None) -> Dict[str, Any]:
    """Kick off the (blocking) auth flow on a worker thread.

    Returns immediately with ``{"ok": bool, "status": str}``; progress is
    readable via :func:`auth_flow_state` and pushed through ``on_change``.
    """
    cid = str(connector_id or "").strip().lower()
    cfg = dict(config or {})
    if not is_enabled(cfg):
        return {"ok": False, "status": "disabled"}
    entry = registry.entry(cid)
    if entry is None or not entry.implemented:
        return {"ok": False, "status": "unknown_connector"}
    inst = registry.get_connector(cid)
    if inst is None:
        return {"ok": False, "status": "needs_update"}
    # One UI click can arrive over both the WebSocket and mailbox transports.
    # Serialize launches before either the setup-page path or OAuth worker can
    # open a browser, otherwise two near-simultaneous requests create two tabs.
    with _AUTH_LOCK:
        now = time.monotonic()
        active = _AUTH_THREADS.get(cid)
        if active is not None and active.is_alive():
            return {"ok": False, "status": "auth_in_progress"}
        last_launch = float(_CONNECT_LAUNCH_TS.get(cid, 0.0) or 0.0)
        if now - last_launch < _CONNECT_DEDUPE_S:
            return {"ok": False, "status": "auth_in_progress"}
        _CONNECT_LAUNCH_TS[cid] = now
    # Token/password connectors need their owner to sign in or create a
    # personal credential first.  Do that in the operating system's default
    # browser, never inside Electron's isolated HUD browser.  Google OAuth
    # continues through its own loopback flow below.
    if connector_health(cid, cfg) == HEALTH_NEEDS_SETUP:
        if _open_provider_setup(cid, inst, on_change=on_change):
            return {"ok": True, "status": "setup_opened"}
    if not secure_store.available():
        _set_auth_flow(cid, "failed", "secure token storage unavailable (pywin32 missing)", on_change)
        return {"ok": False, "status": "needs_setup"}
    def _progress(status: str, detail: str = "") -> None:
        _set_auth_flow(cid, status, detail, on_change)

    def _worker() -> None:
        try:
            auth_kind = str(getattr(inst, "auth_kind", "") or "")
            if auth_kind.startswith("oauth"):
                _progress("opening_browser", "Complete the sign-in in your browser")
            else:
                _progress("verifying_credentials", "Verifying credentials securely")
            ok, label_or_err = _run_budgeted(
                lambda: inst.connect(config=cfg, progress=_progress),
                timeout_s=_CONNECT_BUDGET_S, tag=f"{cid}.connect",
            )
            if ok:
                permissions.set_connected(inst, label_or_err)
                audit.record(cid, "connect", actor=audit.ACTOR_HUD, result=audit.RESULT_OK)
                events.lifecycle(entry.name, "connected")
                _progress("connected", label_or_err)
            else:
                audit.record(cid, "connect", actor=audit.ACTOR_HUD,
                             result=audit.RESULT_ERROR, detail=label_or_err)
                # A token/local-gateway connector can be in an "available"
                # state yet still fail verification (stale token, stopped
                # gateway, signed out broker session).  Do not strand the
                # user on a bare failure: send them to the provider setup page.
                if not auth_kind.startswith("oauth") and _open_provider_setup(
                    cid, inst, on_change=on_change,
                    detail="Finish provider sign-in or setup in your default browser, then press Connect again.",
                ):
                    return
                _progress("failed", label_or_err)
        except ConnectorTimeout:
            audit.record(cid, "connect", actor=audit.ACTOR_HUD, result=audit.RESULT_TIMEOUT)
            _progress("failed", "Sign-in timed out — try again")
        except Exception as exc:
            obs.report("connectors.manager.connect", exc, connector=cid)
            audit.record(cid, "connect", actor=audit.ACTOR_HUD,
                         result=audit.RESULT_ERROR, detail=type(exc).__name__)
            _progress("failed", type(exc).__name__)

    t = threading.Thread(target=_worker, daemon=True, name=f"connector-auth:{cid}")
    with _AUTH_LOCK:
        _AUTH_THREADS[cid] = t
    t.start()
    return {"ok": True, "status": "started"}


def disconnect(connector_id: str, *, config: Optional[Dict[str, Any]] = None) -> bool:
    """Revoke + wipe credentials and mark disconnected. Audited."""
    cid = str(connector_id or "").strip().lower()
    entry = registry.entry(cid)
    inst = registry.get_connector(cid)
    ok = True
    if inst is not None:
        try:
            inst.disconnect(config=dict(config or {}))
        except Exception as exc:
            obs.report("connectors.manager.disconnect", exc, connector=cid)
            ok = False
    # Token blob must be gone even if the connector's revoke failed.
    secure_store.delete_token(cid)
    permissions.set_disconnected(cid)
    audit.record(cid, "disconnect", actor=audit.ACTOR_HUD,
                 result=audit.RESULT_OK if ok else audit.RESULT_ERROR)
    if entry is not None:
        events.lifecycle(entry.name, "disconnected")
    return ok


# ─────────────────────────────────────────────────────────────────────────────
# HUD snapshot
# ─────────────────────────────────────────────────────────────────────────────
def snapshot(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The full ``syrix_connectors`` state object the HUD renders from."""
    cfg = dict(config or {})
    connectors: List[Dict[str, Any]] = []
    for e in registry.CATALOGUE:
        rec = permissions.connector_record(e.id)
        health = connector_health(e.id, cfg)
        item: Dict[str, Any] = {
            "id": e.id, "name": e.name, "description": e.description,
            "icon": e.icon, "category": e.category,
            "implemented": e.implemented, "health": health,
            "account": str(rec.get("account") or ""),
            "connected_at": rec.get("connected_at") or 0,
            "last_sync": rec.get("last_sync") or 0,
            "last_error": str(rec.get("last_error") or ""),
        }
        inst = registry.get_connector(e.id) if e.implemented else None
        if inst is not None:
            try:
                item["setup_hint"] = str(inst.setup_hint() or "")[:240]
            except Exception:
                item["setup_hint"] = ""
            setup_url = str(getattr(inst, "setup_url", "") or "").strip()
            item["setup_url"] = setup_url if setup_url.startswith("https://") else ""
            perms = rec.get("permissions") if isinstance(rec.get("permissions"), dict) else {}
            item["permissions"] = [
                {
                    "key": a.key, "label": a.label, "description": a.description,
                    "verb": a.verb, "risk": a.risk,
                    "state": (permissions.BLOCKED if a.risk == RISK_NOT_IMPLEMENTED
                              else perms.get(a.key, permissions.default_permission_state(a))),
                    "locked": a.risk == RISK_NOT_IMPLEMENTED,
                }
                for a in inst.actions().values()
            ]
            tmap = rec.get("tasks") if isinstance(rec.get("tasks"), dict) else {}
            item["tasks"] = [
                {
                    "key": t.key, "label": t.label, "description": t.description,
                    "enabled": bool(tmap.get(t.key, t.default_enabled)),
                }
                for t in inst.background_tasks
            ]
        connectors.append(item)
    return {
        "schema_version": SCHEMA_VERSION,
        "enabled": is_enabled(cfg),
        "tools_enabled": tools_enabled(cfg),
        "background_enabled": background_enabled(cfg),
        "secure_store": secure_store.status(),
        "connectors": connectors,
        "activity": audit.recent(limit=25),
        "pending_confirm": pending_confirmations(),
        "auth_flow": auth_flow_state(),
        "ts": time.time(),
    }


__all__ = [
    "is_enabled", "tools_enabled", "background_enabled",
    "execute", "ConnectorTimeout",
    "pending_confirmations", "confirm_action", "deny_action",
    "start_connect", "disconnect", "auth_flow_state",
    "connector_health", "snapshot",
]
