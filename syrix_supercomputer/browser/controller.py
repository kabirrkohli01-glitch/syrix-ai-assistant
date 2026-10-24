"""Supercomputer browser controller — the verb contract and control state.

Every verb returns ``{ok, reason, data}`` and passes ``_guard`` first. The guard
is the enforcement point for four separate things, in this order:

1. **Capability** — browser automation switched off in settings means the verb
   is refused here, not merely hidden from the model.
2. **Control state** — if the user took control or paused, no agent input op is
   emitted at all. This is authoritative in Python, so a stuck or slow renderer
   cannot defeat Stop or Take Control.
3. **Generation** — every op carries the generation it was issued under. An op
   already in flight when the user stops is discarded on return rather than
   landing late. That is what makes Stop actually stop.
4. **Policy** — transport rules (scheme, private network, credentials) and task
   scope (may THIS task reach that domain).

Control states:
  ``agent``  the agent may issue input ops
  ``user``   the user took control; every agent input op is refused
  ``paused`` explicitly paused; refused
  ``closed`` no session
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Dict, List, Optional

import syrix_obs as obs

from ..contract import TaskContract
from . import policy
from .transport import result, send

CONTROL_AGENT = "agent"
CONTROL_USER = "user"
CONTROL_PAUSED = "paused"
CONTROL_CLOSED = "closed"

# Verbs that change the page. Read-only verbs stay available while paused so the
# workspace can still show what is on screen. Tab management (new_tab, focus,
# close) is the user's own chrome and stays available too.
_INPUT_VERBS = frozenset({"open", "click", "type", "scroll", "back",
                          # 2026-07-26. The original four could not express how
                          # a real web app is driven: an indicator is a keyboard
                          # shortcut, a timeframe is a <select>, a fib
                          # retracement is a DRAG. "Open TradingView and add a
                          # fib line" was not a hard task, it was an
                          # unrepresentable one.
                          "press_key", "hover", "select_option", "drag",
                          "forward", "reload", "attach_file"})

_LOCK = threading.RLock()
# A per-session tag generated here. It is NOT the browser's Electron partition —
# that is owned by hud_electron/syrix-supercomputer-browser.js and learned from
# its replies into _BROWSER_PARTITION below. Keeping the two separate stops the
# HUD from reporting a session id as though it were the profile in use.
_PARTITION = ""
_BROWSER_PARTITION = ""
_SHARED_PROFILE = False
_CONTROL = CONTROL_CLOSED
_OPEN = False
_LAST_ERROR = ""
_GENERATION = 0
_CONTRACT: Optional[TaskContract] = None
_TABS: List[Dict[str, Any]] = []


# ── session / control state ────────────────────────────────────────────────
def partition() -> str:
    """A fresh tag identifying THIS Supercomputer session on the Python side.

    Not the browser profile — see :func:`browser_partition`. Kept distinct
    because the browser now runs in the HUD's persistent profile, so a rotating
    id here would be a lie if it were reported as the session's storage.
    """
    global _PARTITION
    with _LOCK:
        if not _PARTITION:
            _PARTITION = f"syrix-supercomputer-{uuid.uuid4().hex[:12]}"
        return _PARTITION


def browser_partition() -> str:
    """The Electron partition actually in use, once the browser has reported it."""
    with _LOCK:
        return _BROWSER_PARTITION


def shared_profile() -> bool:
    """True when the browser is running in the HUD browser's own profile — i.e.
    signed in as the user. False means a throwaway partition, signed out of all."""
    with _LOCK:
        return _SHARED_PROFILE


def _absorb(data: Any) -> None:
    """Learn the real session details from any reply that carries them."""
    global _BROWSER_PARTITION, _SHARED_PROFILE
    if not isinstance(data, dict):
        return
    part = str(data.get("partition") or "")
    with _LOCK:
        if part:
            _BROWSER_PARTITION = part
        if "sharedProfile" in data:
            _SHARED_PROFILE = bool(data.get("sharedProfile"))


def control_state() -> str:
    with _LOCK:
        return _CONTROL


def is_agent_allowed() -> bool:
    with _LOCK:
        return _OPEN and _CONTROL == CONTROL_AGENT


def generation() -> int:
    with _LOCK:
        return _GENERATION


def set_contract(contract: Optional[TaskContract]) -> None:
    """Bind the active task's contract. Navigation scope comes from here."""
    global _CONTRACT
    with _LOCK:
        _CONTRACT = contract


def contract() -> Optional[TaskContract]:
    with _LOCK:
        return _CONTRACT


def take_control(*, reason: str = "user_request") -> Dict[str, Any]:
    """User takes the wheel. Agent input stops immediately; pending ops die."""
    global _CONTROL, _GENERATION
    with _LOCK:
        _CONTROL = CONTROL_USER
        _GENERATION += 1
        gen = _GENERATION
    _log(f"take_control reason={reason} generation={gen}")
    return {"ok": True, "control": CONTROL_USER, "generation": gen}


def return_control(*, reason: str = "user_request") -> Dict[str, Any]:
    global _CONTROL
    with _LOCK:
        if not _OPEN:
            return {"ok": False, "reason": "no_session"}
        _CONTROL = CONTROL_AGENT
        gen = _GENERATION
    _log(f"return_control reason={reason} generation={gen}")
    return {"ok": True, "control": CONTROL_AGENT, "generation": gen}


def pause(*, reason: str = "user_request") -> Dict[str, Any]:
    global _CONTROL, _GENERATION
    with _LOCK:
        _CONTROL = CONTROL_PAUSED
        _GENERATION += 1
        gen = _GENERATION
    _log(f"pause reason={reason} generation={gen}")
    return {"ok": True, "control": CONTROL_PAUSED, "generation": gen}


def resume(*, reason: str = "user_request") -> Dict[str, Any]:
    return return_control(reason=reason)


def stop(*, reason: str = "user_stop") -> Dict[str, Any]:
    """Cancel in-flight browser work without closing the session."""
    global _GENERATION, _CONTROL
    with _LOCK:
        _GENERATION += 1
        gen = _GENERATION
        if _CONTROL == CONTROL_AGENT:
            _CONTROL = CONTROL_PAUSED
    _log(f"stop reason={reason} generation={gen}")
    return {"ok": True, "generation": gen}


# ── the guard ──────────────────────────────────────────────────────────────
def _guard(
    verb: str,
    *,
    config: Optional[Dict[str, Any]],
    url: str = "",
    text: str = "",
    generation_at_issue: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Return a refusal envelope when the op must not run, else None."""
    cfg = config or {}
    if not bool(cfg.get("supercomputer_enabled", True)):
        return result(False, "supercomputer_disabled")
    if not bool(cfg.get("supercomputer_browser_enabled", True)):
        return result(False, "browser_capability_disabled")

    with _LOCK:
        control = _CONTROL
        gen = _GENERATION
        is_open = _OPEN

    # An op issued before a stop/pause/take-control must not land afterwards.
    if generation_at_issue is not None and int(generation_at_issue) != gen:
        return result(False, "cancelled")

    if verb in _INPUT_VERBS:
        if not is_open and verb != "open":
            return result(False, "browser_not_open")
        if control == CONTROL_USER:
            return result(False, "user_in_control")
        if control == CONTROL_PAUSED:
            return result(False, "paused")
        if control == CONTROL_CLOSED and verb != "open":
            return result(False, "browser_not_open")

    if verb == "open":
        ok, reason = policy.check_navigation(url, contract(), config=cfg)
        if not ok:
            _log(f"navigation_refused reason={reason} url={str(url)[:80]}")
            return result(False, reason, data={"url": url})

    # Typing a password is refused in every mode. press_key and select_option
    # are keystrokes and values too, so the same check applies to them — routing
    # text through a different verb must not be a way around it.
    if verb in ("type", "press_key", "select_option"):
        ok, reason = policy.check_typed_text(text)
        if not ok:
            _log(f"{verb}_refused reason=sensitive_text")
            return result(False, reason)

    return None


# ── verbs ──────────────────────────────────────────────────────────────────
def ensure_open(*, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Mark the browser session live. Views are created lazily by the first
    :func:`open`, so entering the mode alone spawns no renderer."""
    global _OPEN, _CONTROL
    cfg = config or {}
    if not bool(cfg.get("supercomputer_enabled", True)):
        return result(False, "supercomputer_disabled")
    if not bool(cfg.get("supercomputer_browser_enabled", True)):
        return result(False, "browser_capability_disabled")
    with _LOCK:
        _OPEN = True
        if _CONTROL in (CONTROL_CLOSED,):
            _CONTROL = CONTROL_AGENT
        need_profile = not _BROWSER_PARTITION
    partition()
    # Ask the Electron module which profile it is actually using, once per
    # session. Without this the HUD reports an empty partition and cannot say
    # whether the browser is signed in as the user or running throwaway.
    if need_profile:
        with obs.swallow("supercomputer.browser.learn_profile"):
            probe = send("tabs", timeout_s=6.0)
            if probe.get("ok"):
                _absorb(probe.get("data"))
    return result(True, data={"partition": browser_partition() or partition(),
                              "shared_profile": shared_profile(),
                              "control": control_state()})


def open_url(
    url: str,
    *,
    tab_id: str = "",
    new_tab: bool = False,
    config: Optional[Dict[str, Any]] = None,
    generation_at_issue: Optional[int] = None,
) -> Dict[str, Any]:
    blocked = _guard("open", config=config, url=url,
                     generation_at_issue=generation_at_issue)
    if blocked:
        return blocked
    opened = ensure_open(config=config)
    if not opened["ok"]:
        return opened
    res = send("open", url=str(url), tab_id=str(tab_id or ""),
               new_tab=bool(new_tab), timeout_s=20.0)
    # Every reply that carries session details teaches us which profile is live.
    # `open` always happens, so this is the reliable place to learn it.
    _absorb(res.get("data"))
    _record(res, url=url)
    return res


def tabs(*, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    blocked = _guard("tabs", config=config)
    if blocked:
        return blocked
    res = send("tabs", timeout_s=6.0)
    if res["ok"]:
        _absorb(res.get("data"))
        with _LOCK:
            _TABS[:] = list((res.get("data") or {}).get("tabs") or [])
    return res


def read(
    tab_id: str = "",
    mode: str = "digest",
    offset: int = 0,
    *,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Read the page. The returned text is UNTRUSTED — callers must wrap it in
    the injection guard's envelope before it reaches a model."""
    blocked = _guard("read", config=config)
    if blocked:
        return blocked
    try:
        text_offset = max(0, int(offset or 0))
    except (TypeError, ValueError):
        text_offset = 0
    return send("read", tab_id=str(tab_id or ""), mode=str(mode or "digest"),
                offset=text_offset, timeout_s=15.0)


def find(tab_id: str, query: str, *, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Locate elements by label substring, from a fresh digest."""
    dig = read(tab_id, "digest", config=config)
    if not dig.get("ok"):
        return dig
    q = str(query or "").strip().lower()
    matches = [
        {
            "id": e.get("id"),
            "label": e.get("label", ""),
            "tag": e.get("tag", ""),
            "editable": e.get("editable", False),
        }
        for e in (dig.get("data") or {}).get("elements") or []
        if q in str(e.get("label", "")).lower()
    ]
    return result(bool(matches), "" if matches else "no_match", data={"matches": matches[:8]})


def click(
    tab_id: str,
    element_id: str,
    *,
    config: Optional[Dict[str, Any]] = None,
    generation_at_issue: Optional[int] = None,
) -> Dict[str, Any]:
    blocked = _guard("click", config=config, generation_at_issue=generation_at_issue)
    if blocked:
        return blocked
    return send("click", tab_id=str(tab_id or ""), element_id=str(element_id or ""), timeout_s=12.0)


def type_text(
    tab_id: str,
    element_id: str,
    text: str,
    *,
    submit: bool = False,
    config: Optional[Dict[str, Any]] = None,
    generation_at_issue: Optional[int] = None,
) -> Dict[str, Any]:
    blocked = _guard("type", config=config, text=text,
                     generation_at_issue=generation_at_issue)
    if blocked:
        return blocked
    return send("type", tab_id=str(tab_id or ""), element_id=str(element_id or ""),
                text=str(text), submit=bool(submit), timeout_s=15.0)


def scroll(
    tab_id: str = "",
    dy: int = 600,
    *,
    config: Optional[Dict[str, Any]] = None,
    generation_at_issue: Optional[int] = None,
) -> Dict[str, Any]:
    blocked = _guard("scroll", config=config, generation_at_issue=generation_at_issue)
    if blocked:
        return blocked
    return send("scroll", tab_id=str(tab_id or ""), dy=int(dy), timeout_s=8.0)


def back(
    tab_id: str = "",
    *,
    config: Optional[Dict[str, Any]] = None,
    generation_at_issue: Optional[int] = None,
) -> Dict[str, Any]:
    blocked = _guard("back", config=config, generation_at_issue=generation_at_issue)
    if blocked:
        return blocked
    return send("back", tab_id=str(tab_id or ""), timeout_s=10.0)


def press_key(
    tab_id: str = "",
    keys: str = "",
    *,
    config: Optional[Dict[str, Any]] = None,
    generation_at_issue: Optional[int] = None,
) -> Dict[str, Any]:
    """Send a key or a chord ("Enter", "Escape", "Ctrl+A", "Alt+F")."""
    blocked = _guard("press_key", config=config, text=str(keys or ""),
                     generation_at_issue=generation_at_issue)
    if blocked:
        return blocked
    return send("press_key", tab_id=str(tab_id or ""), keys=str(keys or ""), timeout_s=10.0)


def hover(
    tab_id: str,
    element_id: str,
    *,
    config: Optional[Dict[str, Any]] = None,
    generation_at_issue: Optional[int] = None,
) -> Dict[str, Any]:
    """Move the pointer over an element — the only way to open a hover menu."""
    blocked = _guard("hover", config=config, generation_at_issue=generation_at_issue)
    if blocked:
        return blocked
    return send("hover", tab_id=str(tab_id or ""), element_id=str(element_id or ""),
                timeout_s=10.0)


def select_option(
    tab_id: str,
    element_id: str,
    value: str,
    *,
    config: Optional[Dict[str, Any]] = None,
    generation_at_issue: Optional[int] = None,
) -> Dict[str, Any]:
    """Choose an option in a <select>. Clicking one does not work in Chromium:
    the dropdown is native chrome, painted outside the page's own coordinates."""
    blocked = _guard("select_option", config=config, text=str(value or ""),
                     generation_at_issue=generation_at_issue)
    if blocked:
        return blocked
    return send("select_option", tab_id=str(tab_id or ""), element_id=str(element_id or ""),
                value=str(value or ""), timeout_s=10.0)


def drag(
    tab_id: str = "",
    *,
    from_element: str = "",
    to_element: str = "",
    from_xy: Optional[Dict[str, Any]] = None,
    to_xy: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
    generation_at_issue: Optional[int] = None,
) -> Dict[str, Any]:
    """Press, move and release. Element ids or viewport-relative coordinates.

    Coordinates matter here in a way they do not elsewhere: a trend line on a
    chart canvas has no element to aim at, only a point.
    """
    blocked = _guard("drag", config=config, generation_at_issue=generation_at_issue)
    if blocked:
        return blocked
    return send("drag", tab_id=str(tab_id or ""),
                from_element=str(from_element or ""), to_element=str(to_element or ""),
                from_xy=dict(from_xy or {}), to_xy=dict(to_xy or {}), timeout_s=15.0)


def wait_for(
    tab_id: str = "",
    *,
    text: str = "",
    element_id: str = "",
    timeout_s: float = 15.0,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Block until text or an element appears, or the budget runs out.

    Replaces guessing with a fixed settle delay. A chart that takes four seconds
    to draw was being read at 0.6s and reported as an empty page.
    """
    blocked = _guard("read", config=config)
    if blocked:
        return blocked
    budget = max(1.0, min(60.0, float(timeout_s)))
    return send("wait_for", tab_id=str(tab_id or ""), text=str(text or ""),
                element_id=str(element_id or ""), timeout_s=budget + 5.0,
                wait_s=budget)


def forward(
    tab_id: str = "",
    *,
    config: Optional[Dict[str, Any]] = None,
    generation_at_issue: Optional[int] = None,
) -> Dict[str, Any]:
    blocked = _guard("forward", config=config, generation_at_issue=generation_at_issue)
    if blocked:
        return blocked
    return send("forward", tab_id=str(tab_id or ""), timeout_s=10.0)


def reload(
    tab_id: str = "",
    *,
    config: Optional[Dict[str, Any]] = None,
    generation_at_issue: Optional[int] = None,
) -> Dict[str, Any]:
    blocked = _guard("reload", config=config, generation_at_issue=generation_at_issue)
    if blocked:
        return blocked
    return send("reload", tab_id=str(tab_id or ""), timeout_s=20.0)


def attach_file(
    tab_id: str,
    element_id: str,
    path: str,
    *,
    config: Optional[Dict[str, Any]] = None,
    generation_at_issue: Optional[int] = None,
) -> Dict[str, Any]:
    """Put a real file into a file input.

    A file picker cannot be driven by synthetic input events — Chromium opens a
    native OS dialog that lives outside the page entirely. The renderer sets the
    input's files through the debugger protocol instead. The path has already
    been through the local-files allowlist and a permission card by the time it
    gets here.
    """
    blocked = _guard("attach_file", config=config, generation_at_issue=generation_at_issue)
    if blocked:
        return blocked
    return send("attach_file", tab_id=str(tab_id or ""), element_id=str(element_id or ""),
                path=str(path or ""), timeout_s=20.0)


def wait(seconds: float = 1.0) -> Dict[str, Any]:
    """Bounded settle that respects Stop — polls the generation rather than
    sleeping through a cancellation."""
    start_gen = generation()
    deadline = time.monotonic() + max(0.0, min(10.0, float(seconds)))
    while time.monotonic() < deadline:
        if generation() != start_gen:
            return result(False, "cancelled")
        time.sleep(0.05)
    return result(True)


def screenshot(tab_id: str = "", *, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    blocked = _guard("screenshot", config=config)
    if blocked:
        return blocked
    return send("screenshot", tab_id=str(tab_id or ""), timeout_s=12.0)


def print_pdf(tab_id: str = "", *, page_size: str = "A4", landscape: bool = False,
              config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Render the tab through Chromium's print engine. Read-only w.r.t. the page.

    Generous timeout: printing a page with images is slower than reading it, and
    a premature give-up here loses a document the user asked for.
    """
    blocked = _guard("print_pdf", config=config)
    if blocked:
        return blocked
    return send("print_pdf", tab_id=str(tab_id or ""), page_size=str(page_size or "A4"),
                landscape=bool(landscape), timeout_s=60.0)


def arm_download(folder: str, *, ttl_s: float = 60.0,
                 config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Permit exactly ONE download, into ``folder``, for a short window.

    Called only after a permission card was answered. Single-use and expiring on
    the Electron side, so a page cannot sit waiting for an approval meant for
    something else and fire its own download into it.
    """
    blocked = _guard("download", config=config)
    if blocked:
        return blocked
    return send("arm_download", folder=str(folder or ""), ttl_s=float(ttl_s), timeout_s=8.0)


def download_status(*, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """What became of the most recent download. Read-only."""
    return send("download_status", timeout_s=8.0)


def close(tab_id: str = "", *, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    blocked = _guard("close", config=config)
    if blocked:
        return blocked
    return send("close", tab_id=str(tab_id or ""), timeout_s=8.0)


def new_tab(*, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    blocked = _guard("new_tab", config=config)
    if blocked:
        return blocked
    return send("new_tab", timeout_s=8.0)


def focus(tab_id: str, *, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    blocked = _guard("focus", config=config)
    if blocked:
        return blocked
    return send("focus", tab_id=str(tab_id or ""), timeout_s=6.0)


def navigate(
    action: str,
    *,
    tab_id: str = "",
    url: str = "",
    config: Optional[Dict[str, Any]] = None,
    generation_at_issue: Optional[int] = None,
) -> Dict[str, Any]:
    """Chrome-bar navigation: back / forward / reload / stop / goto.

    ``goto`` is an agent-visible navigation and goes through the full policy
    gate; the history verbs do not need a scope check because they can only
    reach pages this task already visited.
    """
    act = str(action or "").strip().lower()
    if act == "goto":
        blocked = _guard("open", config=config, url=url,
                         generation_at_issue=generation_at_issue)
    else:
        blocked = _guard("back", config=config, generation_at_issue=generation_at_issue)
    if blocked:
        return blocked
    return send("nav", tab_id=str(tab_id or ""), action=act, url=str(url or ""), timeout_s=15.0)


def annotate(tab_id: str = "", *, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Let the user point at an element and describe it back.

    Read-only by construction: the picker swallows the click, so pointing at
    something never activates it. Available while the user is in control —
    annotating is how they hand SYRIX a target without giving up the wheel.
    """
    cfg = config or {}
    if not bool(cfg.get("supercomputer_enabled", True)):
        return result(False, "supercomputer_disabled")
    if not bool(cfg.get("supercomputer_browser_enabled", True)):
        return result(False, "browser_capability_disabled")
    # This waits on a human, so it is bounded but generous. It must never be
    # called from the HUD dispatch thread — see the note in hud_api.HUD_COMMANDS.
    return send("annotate", tab_id=str(tab_id or ""), timeout_s=120.0)


def set_bounds(bounds: Dict[str, Any], *, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return send("set_bounds", bounds=dict(bounds or {}), timeout_s=5.0)


def shutdown(*, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Destroy every view and end the session.

    Safe to call when nothing is open — mode exit calls it unconditionally. The
    local state is cleared even if the renderer never answers, so a dead HUD
    cannot leave this module believing a browser is still live. Note the tabs
    always go; whether the *profile's* storage is also wiped is the Electron
    module's call, and it never wipes a shared one.
    """
    global _OPEN, _CONTROL, _PARTITION, _GENERATION, _CONTRACT
    global _BROWSER_PARTITION, _SHARED_PROFILE
    with _LOCK:
        was_open = _OPEN
        part = _PARTITION
        _OPEN = False
        _CONTROL = CONTROL_CLOSED
        _GENERATION += 1
        _PARTITION = ""
        _BROWSER_PARTITION = ""
        _SHARED_PROFILE = False
        _CONTRACT = None
        _TABS.clear()
    if was_open:
        with obs.swallow("supercomputer.browser.shutdown_send", partition=part):
            send("shutdown", timeout_s=8.0)
        _log(f"shutdown partition={part}")
    policy.clear_preview_origin()
    return {"ok": True, "was_open": was_open, "partition": part}


def snapshot() -> Dict[str, Any]:
    with _LOCK:
        return {
            "open": _OPEN,
            "control": _CONTROL,
            "session_tag": _PARTITION,
            "partition": _BROWSER_PARTITION,
            "shared_profile": _SHARED_PROFILE,
            "generation": _GENERATION,
            "last_error": _LAST_ERROR,
            "tabs": list(_TABS),
            "scope": sorted(_CONTRACT.allowed_domains) if _CONTRACT else [],
            "policy": policy.snapshot(),
            "updated_at": time.time(),
        }


def _record(res: Dict[str, Any], *, url: str = "") -> None:
    global _LAST_ERROR
    if not res.get("ok"):
        with _LOCK:
            _LAST_ERROR = str(res.get("reason") or "")[:200]
        _log(f"op_failed reason={res.get('reason')} url={str(url)[:80]}")


def _log(msg: str) -> None:
    with obs.swallow("supercomputer.browser.log"):
        print(f"[SUPERCOMPUTER_BROWSER] {msg}", flush=True)


def _reset_for_tests() -> None:
    global _PARTITION, _CONTROL, _OPEN, _LAST_ERROR, _GENERATION, _CONTRACT
    global _BROWSER_PARTITION, _SHARED_PROFILE
    with _LOCK:
        _PARTITION = ""
        _BROWSER_PARTITION = ""
        _SHARED_PROFILE = False
        _CONTROL = CONTROL_CLOSED
        _OPEN = False
        _LAST_ERROR = ""
        _GENERATION = 0
        _CONTRACT = None
        _TABS.clear()
    policy._reset_for_tests()


# Verb table for the tool registry (Phase 6).
# The `VERBS` dispatch table that used to live here was annotated "for the tool
# registry (Phase 6)". Phase 6 landed and tools.py calls these functions
# directly, so the table was never read by anything — verified repo-wide before
# removing it on 2026-07-26. The app dock's own sandbox_browser.VERBS is a
# different table, is live, and is untouched.

__all__ = [
    "CONTROL_AGENT", "CONTROL_USER", "CONTROL_PAUSED", "CONTROL_CLOSED",
    "partition", "control_state", "is_agent_allowed", "generation",
    "set_contract", "contract", "take_control", "return_control", "pause",
    "browser_partition", "shared_profile",
    "resume", "stop", "ensure_open", "open_url", "tabs", "read", "find",
    "click", "type_text", "scroll", "back", "wait", "screenshot", "close",
    "press_key", "hover", "select_option", "drag", "wait_for",
    "forward", "reload", "attach_file",
    "new_tab", "focus", "navigate", "annotate", "print_pdf",
    "arm_download", "download_status",
    "set_bounds", "shutdown", "snapshot",
]
