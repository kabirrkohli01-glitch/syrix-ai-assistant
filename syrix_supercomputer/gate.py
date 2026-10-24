"""The Supercomputer choke point. Every action goes through :func:`execute`.

Gate order, and why each one is where it is:

  1. **kill switch**      — mode off means nothing runs, not even auditing
  2. **known action**     — deny-unknown; an undeclared verb fails closed
  3. **capability**       — switched off in settings = technically unavailable
  4. **task state**       — a halted task executes nothing (Stop, or a pending
                            card, or "Chat about this")
  5. **declined before**  — a refusal covers the effect, not the phrasing
  6. **task scope**       — the contract's limit; may narrow, never widen
  7. **permission**       — side effects need an answered card, every time
  8. **timeout budget**   — a wedged action cannot hold the task forever
  9. **audit**            — the outcome, either way

Ordering matters. The capability check precedes the permission check so a
disabled capability can never even raise a card, and the scope check precedes
the permission check so an out-of-scope target is refused rather than asked
about (asking would train the user to approve scope expansion).

:func:`execute` never raises. Callers get a structured result and report it
honestly; an exception escaping here would reach the spine's fail-closed
handler and abort the whole turn.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

import syrix_obs as obs

from . import audit, capabilities, permissions, safety_mode, tasks
from .browser import policy

# Default per-action wall clock. Generous enough for a slow page, short enough
# that a wedged action cannot hold the task open indefinitely.
DEFAULT_TIMEOUT_S = 45.0

RESULT_OK = "ok"
RESULT_DENIED = "denied"
RESULT_BLOCKED = "blocked"
RESULT_NEEDS_PERMISSION = "needs_permission"
RESULT_TIMEOUT = "timeout"
RESULT_ERROR = "error"

# Actions refused in every safety mode, with no card and no setting that can
# turn them on. Not "risky and card-gated" — outside what this system is
# designed to do at all. Everything short of the irreversible act stays
# available: the research, the chart, the levels, the written case.
REFUSED_OUTRIGHT: Dict[str, str] = {
    "place_trade": (
        "I do not place trades. There is no approved trading-execution system "
        "here, and a market order is irreversible and moves while you decide — "
        "so that click is yours, not mine. I can do everything up to it: the "
        "chart, the levels, the bull and bear case, the invalidation, and what "
        "would change my mind. Press Take Control when you want to place it "
        "yourself."
    ),
}


@dataclass
class ActionResult:
    ok: bool = False
    result: str = RESULT_DENIED
    reason: str = ""
    # Plain-English line for the chat. A refusal is never mysterious.
    message: str = ""
    data: Any = None
    permission_request_id: str = ""
    needs_permission: bool = False
    latency_ms: int = 0
    diagnostics: Dict[str, Any] = field(default_factory=dict)


class _Timeout(Exception):
    pass


def _run_budgeted(fn: Callable[[], Any], timeout_s: float, tag: str) -> Any:
    """Run ``fn`` with a wall-clock budget. Late results are discarded.

    A worker thread rather than a signal: this runs off the main thread and on
    Windows, where signal-based timeouts are not available.
    """
    box: Dict[str, Any] = {}
    done = threading.Event()

    def _worker() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's side
            box["error"] = exc
        finally:
            done.set()

    t = threading.Thread(target=_worker, name=f"sc-gate-{tag}", daemon=True)
    t.start()
    if not done.wait(timeout=max(0.5, float(timeout_s))):
        raise _Timeout(tag)
    if "error" in box:
        raise box["error"]
    return box.get("value")


def is_enabled(config: Optional[Dict[str, Any]] = None) -> bool:
    return bool((config or {}).get("supercomputer_enabled", True))


def tools_enabled(config: Optional[Dict[str, Any]] = None) -> bool:
    """A sub-flag can never be on when the master is off."""
    return is_enabled(config) and bool((config or {}).get("supercomputer_tools_enabled", True))


def execute(
    action: str,
    fn: Callable[[], Any],
    *,
    task_id: str,
    scope: str,
    description: str = "",
    why: str = "",
    config: Optional[Dict[str, Any]] = None,
    actor: str = "agent",
    request_id: str = "",
    timeout_s: Optional[float] = None,
    url: str = "",
) -> ActionResult:
    """Run one action through every gate. Never raises."""
    cfg = config or {}
    act = str(action or "").strip().lower()
    started = time.monotonic()

    def _deny(result: str, reason: str, message: str, *, log: bool = True) -> ActionResult:
        if log:
            audit.record(act, _audit_result(result), task_id=task_id, scope=scope,
                         reason=reason, actor=actor, request_id=request_id)
        return ActionResult(
            ok=False, result=result, reason=reason, message=message,
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    # 1. Kill switches. Not audited: the subsystem is meant to be inert.
    if not is_enabled(cfg):
        return _deny(RESULT_BLOCKED, "supercomputer_disabled",
                     "Supercomputer is switched off in settings, so I ran nothing.",
                     log=False)
    if not tools_enabled(cfg):
        return _deny(RESULT_BLOCKED, "tools_disabled",
                     "Supercomputer's tools are switched off, so I ran nothing.",
                     log=False)

    # 2. Refused outright, in every mode, with no card and no setting.
    #
    # These are not "risky things the user might approve" — they are things this
    # system has no design for and must not improvise. A trade is an
    # irreversible market order against his own money at a price that moves
    # while a card sits on screen; Supercomputer has no trading-execution
    # system that has been built and approved, so it stops at the analysis and
    # hands control back. Analysis, charts, levels and a written case are all
    # still fully available — it is the click that places the order that is not.
    if act in REFUSED_OUTRIGHT:
        return _deny(RESULT_DENIED, f"refused_outright:{act}", REFUSED_OUTRIGHT[act])

    # 3. Deny-unknown. An undeclared verb must fail closed, not run ungated.
    cap = capabilities.capability_for(act)
    if cap is None:
        return _deny(RESULT_DENIED, "unknown_action",
                     f"I do not have an action called '{act}', so I did nothing.")

    # 3. Capability.
    #
    # Originally a switched-off capability was a flat refusal, on the reasoning
    # that "off" should mean unavailable rather than merely hidden. In practice
    # that produced dead ends with no way forward from the chat: the user was told
    # to go and change a setting mid-task, for an action he was standing right
    # there ready to approve.
    #
    # So a switched-off capability now raises the SAME permission card as
    # anything else, saying plainly that it is off by default. Nothing is
    # weakened: the card is still the only thing that authorises the action, it
    # still authorises exactly one use, and a decline is still remembered for
    # the whole task. What changes is that the answer is one click away instead
    # of buried in settings.
    #
    # Set `supercomputer_strict_capabilities` true to restore the hard refusal.
    #
    # The safety mode decides whether a card is even possible here. If nothing
    # is going to ask — "don't ask" mode — then a switched-off capability has to
    # go back to being a flat refusal, because the card was the only thing
    # standing between it and running. "Don't ask" removes the question; it must
    # never quietly grant what settings withhold.
    asks = safety_mode.asks_for(act, config=cfg)
    capability_off = not capabilities.is_enabled(act, cfg)
    if capability_off:
        strict = bool(cfg.get("supercomputer_strict_capabilities", False))
        if strict or not cap.side_effecting or not asks:
            return _deny(
                RESULT_BLOCKED, "capability_disabled",
                f"\"{cap.label}\" is switched off in Supercomputer settings, so I "
                f"cannot do that. Turn it on there if you want it available; I will "
                f"still ask before each use.",
            )

    # 4. A halted task executes nothing. "Chat about this" is a full stop, not
    #    a background wait.
    task = tasks.get(task_id)
    if task is not None and task.is_halted():
        state_message = {
            tasks.STATE_PAUSED_DISCUSSION:
                "We are still talking this one through, so I have not run anything.",
            tasks.STATE_AWAITING_PERMISSION:
                "I am waiting on your answer to the permission card, so I ran nothing.",
            tasks.STATE_PAUSED_USER: "The task is paused, so I ran nothing.",
            tasks.STATE_STOPPED: "That task was stopped, so I ran nothing.",
        }.get(task.state, "That task is not running, so I ran nothing.")
        return _deny(RESULT_BLOCKED, f"task_{task.state}", state_message)

    # 5. A previous decline covers the effect, not the wording.
    if permissions.is_denied(task_id, act, scope):
        return _deny(
            RESULT_DENIED, "previously_declined",
            "You declined that earlier in this task, so I have not tried it "
            "again by another route.",
        )

    # 6. Task scope. A destination that is merely outside the named set is
    #    allowed when open navigation is on (that is ordinary browsing); a
    #    SENSITIVE destination becomes a card instead, and a hard transport
    #    refusal stays a refusal. Asking is reserved for the case where the
    #    answer genuinely changes what happens.
    needs_scope_card = False
    if url:
        ok, reason = policy.check_navigation(url, task.contract if task else None, config=cfg)
        if not ok:
            if reason != "needs_scope_permission":
                return _deny(RESULT_DENIED, reason,
                             f"I did not open that: {policy.describe_block(reason)}.")
            needs_scope_card = True

    # 7. Permission.
    #
    # WHETHER to ask is the safety mode's call (ask-always / balanced / auto);
    # what may be done at all is still the capability's, and what is refused
    # outright is still refused in every mode. So "don't ask" removes the
    # question, never a protection.
    #
    # `asks` alone is enough: in ask-always mode that covers plain browsing,
    # which is not side-effecting and would otherwise never raise a card.
    if asks or needs_scope_card:
        if not permissions.consume(task_id, act, scope):
            reason_line = why
            if not reason_line and needs_scope_card:
                reason_line = (f"{scope} holds private data or money. Allowing this "
                               f"adds it to this task's scope for as long as the "
                               f"task runs.")
            if not reason_line:
                # safety_mode already knows how to say why this class of action
                # stops. It had been written and then never called from
                # anywhere, so cards for actions with no bespoke `why` went out
                # with a blank reason.
                reason_line = safety_mode.explain(act, config=cfg)
            elif safety_mode.always_asks(act, cfg) and safety_mode.mode() == safety_mode.MODE_AUTO:
                reason_line = f"{reason_line} {safety_mode.explain(act, config=cfg)}".strip()
            if capability_off:
                # Say why this one is unusual, so Allow is an informed click and
                # not just the next button in a sequence.
                note = (f"\"{cap.label}\" is switched off by default in "
                        f"Supercomputer settings. Allowing this authorises it "
                        f"once, for this action only — it does not turn the "
                        f"setting on.")
                reason_line = f"{reason_line} {note}".strip()
            req = permissions.request(
                task_id, act, scope,
                description or f"{cap.label}: {scope}",
                why=reason_line,
                url=url,
            )
            if task is not None:
                tasks.set_state(task_id, tasks.STATE_AWAITING_PERMISSION)
            audit.record(act, audit.RESULT_PENDING, task_id=task_id, scope=scope,
                         reason="awaiting_user", actor=actor, request_id=request_id)
            return ActionResult(
                ok=False, result=RESULT_NEEDS_PERMISSION, reason="permission_required",
                message=description or f"{cap.label}: {scope}",
                permission_request_id=req.request_id, needs_permission=True,
                latency_ms=int((time.monotonic() - started) * 1000),
            )

    # 8. Budgeted run.
    budget = float(timeout_s if timeout_s is not None else DEFAULT_TIMEOUT_S)
    try:
        value = _run_budgeted(fn, budget, act)
    except _Timeout:
        audit.record(act, audit.RESULT_TIMEOUT, task_id=task_id, scope=scope,
                     reason=f"timeout_{budget:.0f}s", actor=actor, request_id=request_id)
        return ActionResult(
            ok=False, result=RESULT_TIMEOUT, reason="timeout",
            message=f"That took longer than {budget:.0f} seconds, so I stopped it. "
                    f"It may or may not have completed on the site.",
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    except Exception as exc:
        obs.report("supercomputer.gate.execute", exc, action=act, task_id=task_id)
        audit.record(act, audit.RESULT_ERROR, task_id=task_id, scope=scope,
                     reason=type(exc).__name__, actor=actor, request_id=request_id)
        return ActionResult(
            ok=False, result=RESULT_ERROR, reason=f"error:{type(exc).__name__}",
            message=f"That failed with {type(exc).__name__}. Nothing further was run.",
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    # 9. Success.
    ok = bool(value.get("ok")) if isinstance(value, dict) else bool(value)
    reason = str(value.get("reason") or "") if isinstance(value, dict) else ""
    audit.record(act, audit.RESULT_ALLOWED if ok else audit.RESULT_BLOCKED,
                 task_id=task_id, scope=scope, reason=reason, actor=actor,
                 request_id=request_id)
    return ActionResult(
        ok=ok,
        result=RESULT_OK if ok else RESULT_BLOCKED,
        reason=reason,
        message="" if ok else f"That did not go through: {reason or 'no reason given'}.",
        data=value.get("data") if isinstance(value, dict) else value,
        latency_ms=int((time.monotonic() - started) * 1000),
    )


def resolve_permission(
    request_id: str,
    decision: str,
    *,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Apply a decision from the HUD card and move the task accordingly.

    This is the only path from a human decision into a grant. It deliberately
    does NOT re-run the action: the caller re-attempts, so every other gate is
    evaluated again on the retry.
    """
    req = permissions.pending(request_id)
    if req is None:
        return {"ok": False, "reason": "request_expired_or_unknown"}

    ok, detail = permissions.decide(request_id, decision)
    if not ok:
        return {"ok": False, "reason": detail}

    task = tasks.get(req.task_id)
    if detail == "allowed":
        audit.record(req.action, audit.RESULT_ALLOWED, task_id=req.task_id,
                     scope=req.scope, reason="user_allowed", actor="user",
                     request_id=request_id)
        # A card about a destination widens the contract, attributed to this
        # decision. This is the ONLY path that adds a domain.
        if req.url:
            tasks.grant_domain(req.task_id, req.url, permission_request_id=request_id)
        if task is not None:
            tasks.set_state(req.task_id, tasks.STATE_RUNNING)
    elif detail == "declined":
        audit.record(req.action, audit.RESULT_DECLINED, task_id=req.task_id,
                     scope=req.scope, reason="user_declined", actor="user",
                     request_id=request_id)
        # No means STOP — not "skip that one and carry on". The task ends where
        # it stands, and the user is told what it had reached and how to finish it
        # by hand, because being refused and then left with nothing is the
        # worst of both.
        if task is not None:
            tasks.bump_generation(req.task_id)
            tasks.block_remaining(req.task_id, detail="you said no")
            tasks.set_state(req.task_id, tasks.STATE_STOPPED, detail="user_declined")
    else:  # discuss
        audit.record(req.action, audit.RESULT_DISCUSS, task_id=req.task_id,
                     scope=req.scope, reason="user_wants_to_discuss", actor="user",
                     request_id=request_id)
        if task is not None:
            # A full stop: nothing continues in the background.
            tasks.bump_generation(req.task_id)
            tasks.set_state(req.task_id, tasks.STATE_PAUSED_DISCUSSION)
    return {"ok": True, "decision": detail, "action": req.action, "scope": req.scope,
            "task_id": req.task_id, "description": req.description}


def _audit_result(result: str) -> str:
    return {
        RESULT_DENIED: audit.RESULT_DENIED,
        RESULT_BLOCKED: audit.RESULT_BLOCKED,
        RESULT_TIMEOUT: audit.RESULT_TIMEOUT,
        RESULT_ERROR: audit.RESULT_ERROR,
    }.get(result, audit.RESULT_BLOCKED)


__all__ = [
    "ActionResult", "execute", "resolve_permission", "is_enabled", "tools_enabled",
    "capabilities",
    "RESULT_OK", "RESULT_DENIED", "RESULT_BLOCKED", "RESULT_NEEDS_PERMISSION",
    "RESULT_TIMEOUT", "RESULT_ERROR", "DEFAULT_TIMEOUT_S",
]
