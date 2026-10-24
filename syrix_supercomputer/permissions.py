"""Supercomputer permission grants — in memory only, never persisted.

A grant is authority to perform ONE action at ONE scope for ONE task. It is
void the moment any of these happens:

* the task ends (success, failure or abandonment),
* Supercomputer mode is exited,
* the app restarts (enforced by the ``boot_id`` stamp, so even a grant that
  somehow reached disk could not be honoured after a restart).

There is deliberately no "remember this", no "always allow", and no setting
that pre-approves an action. Capability *toggles* (what is even possible) live
in :mod:`syrix_supercomputer.settings`; they are not authority to act.

Declining is stronger than simply not granting: a declined ``(action, scope)``
is recorded in the task's deny set, so the agent cannot reach the same effect
by asking again in different words or routing through a different verb.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import syrix_obs as obs

from .session import BOOT_ID

# How long an unanswered permission card stays valid before it must be re-asked.
CONFIRM_TTL_S = 300.0

# How many answered cards stay on screen. They are display history only — no
# authority survives in them — so the cap exists to bound the HUD payload, not
# for safety.
RESOLVED_KEEP = 24

DECISION_ALLOW = "allow"
DECISION_DECLINE = "decline"
DECISION_DISCUSS = "discuss"
DECISIONS = (DECISION_ALLOW, DECISION_DECLINE, DECISION_DISCUSS)

_LOCK = threading.RLock()
# request_id -> PendingRequest
_PENDING: Dict[str, "PendingRequest"] = {}
# request_id -> PendingRequest, answered, newest last. Kept so the card can BE
# the message: before 2026-07-28 answering one deleted it and posted a wall of
# chat text in its place, which is how a single decision turned into three
# stray messages clustered around a hole where the card had been.
_RESOLVED: "OrderedDict[str, PendingRequest]" = OrderedDict()
# task_id -> list[Grant]
_GRANTS: Dict[str, List["Grant"]] = {}
# task_id -> set of normalised "action|scope" that the user declined
_DENIED: Dict[str, set] = {}


def _norm_scope(scope: str) -> str:
    """Normalise a scope so a decline cannot be dodged by re-spelling it."""
    return " ".join(str(scope or "").strip().lower().split())


def _key(action: str, scope: str) -> str:
    return f"{str(action or '').strip().lower()}|{_norm_scope(scope)}"


@dataclass
class Grant:
    """Authority for one action at one scope, bound to one task and this boot."""

    task_id: str
    action: str
    scope: str
    boot_id: str = BOOT_ID
    granted_ts: float = field(default_factory=time.time)
    # Single-use by default: an approval is for the action that was described,
    # not an ongoing licence. Read-only browsing within an already-approved
    # scope is what `uses` above 1 is for.
    uses_remaining: int = 1

    def is_valid(self) -> bool:
        return self.boot_id == BOOT_ID and self.uses_remaining > 0


@dataclass
class PendingRequest:
    """A permission card awaiting the user's answer."""

    request_id: str
    task_id: str
    action: str
    scope: str
    description: str
    why: str = ""
    # The destination this card is about, when there is one. Approving a card
    # that carries a url is what widens the task contract — see
    # gate.resolve_permission. Page content can never reach this field.
    url: str = ""
    created_ts: float = field(default_factory=time.time)
    # Filled in when the card is answered. `outcome` is display detail attached
    # afterwards by resume.py — what was reached, what to do next — so that the
    # consequence of the answer renders inside the card that caused it.
    decision: str = ""
    decided_ts: float = 0.0
    outcome: Dict[str, Any] = field(default_factory=dict)

    def expired(self, now: Optional[float] = None) -> bool:
        return (now or time.time()) - self.created_ts > CONFIRM_TTL_S

    def as_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "task_id": self.task_id,
            "action": self.action,
            "scope": self.scope,
            "description": self.description,
            "why": self.why,
            "url": self.url,
            "created_ts": self.created_ts,
            "decision": self.decision,
            "decided_ts": self.decided_ts,
            "outcome": dict(self.outcome or {}),
        }


# ── asking ─────────────────────────────────────────────────────────────────
def request(
    task_id: str,
    action: str,
    scope: str,
    description: str,
    *,
    why: str = "",
    url: str = "",
) -> PendingRequest:
    """Park a permission card and return it. The caller must NOT proceed."""
    req = PendingRequest(
        request_id=uuid.uuid4().hex[:12],
        task_id=str(task_id or ""),
        action=str(action or ""),
        scope=str(scope or ""),
        description=str(description or ""),
        why=str(why or ""),
        url=str(url or ""),
    )
    with _LOCK:
        _prune_locked()
        _PENDING[req.request_id] = req
    return req


def pending(request_id: str) -> Optional[PendingRequest]:
    with _LOCK:
        _prune_locked()
        return _PENDING.get(str(request_id or ""))


def pending_snapshot() -> List[Dict[str, Any]]:
    """HUD-facing view of outstanding cards. Labels and ids only."""
    with _LOCK:
        _prune_locked()
        return [p.as_dict() for p in _PENDING.values()]


def resolved_snapshot() -> List[Dict[str, Any]]:
    """HUD-facing view of already-answered cards, oldest first.

    Display only. A resolved card carries no authority — the grant it may have
    created lives in ``_GRANTS`` and is spent through :func:`consume`.
    """
    with _LOCK:
        return [p.as_dict() for p in _RESOLVED.values()]


def note(request_id: str, **outcome: Any) -> bool:
    """Attach display detail to an answered card. Returns False if unknown.

    This is how "what I got as far as / what to do instead" reaches the screen
    without becoming another chat message.
    """
    rid = str(request_id or "")
    with _LOCK:
        req = _RESOLVED.get(rid)
        if req is None:
            return False
        clean = {k: v for k, v in outcome.items() if v not in (None, "", [], {})}
        req.outcome.update(clean)
        return True


# ── answering ──────────────────────────────────────────────────────────────
def decide(request_id: str, decision: str) -> Tuple[bool, str]:
    """Record the user's answer. Returns ``(ok, detail)``.

    This is the ONLY way a grant is ever created. Nothing derived from model
    output or page content can reach it.
    """
    d = str(decision or "").strip().lower()
    if d not in DECISIONS:
        return False, f"unknown_decision:{d}"
    with _LOCK:
        _prune_locked()
        req = _PENDING.pop(str(request_id or ""), None)
        if req is None:
            return False, "request_expired_or_unknown"
        # The card is answered but NOT thrown away — it becomes the record of
        # the decision, in the place it was asked.
        req.decision = d
        req.decided_ts = time.time()
        _RESOLVED[req.request_id] = req
        while len(_RESOLVED) > RESOLVED_KEEP:
            _RESOLVED.popitem(last=False)
        if d == DECISION_ALLOW:
            from . import capabilities as _caps

            _GRANTS.setdefault(req.task_id, []).append(
                Grant(task_id=req.task_id, action=req.action, scope=req.scope,
                      uses_remaining=_caps.grant_uses(req.action))
            )
            return True, "allowed"
        if d == DECISION_DECLINE:
            # Remember the refusal for the whole task, normalised, so the same
            # effect cannot be reached by a differently-worded request.
            _DENIED.setdefault(req.task_id, set()).add(_key(req.action, req.scope))
            return True, "declined"
        return True, "discuss"


# ── checking ───────────────────────────────────────────────────────────────
def is_denied(task_id: str, action: str, scope: str) -> bool:
    with _LOCK:
        return _key(action, scope) in _DENIED.get(str(task_id or ""), set())


def consume(task_id: str, action: str, scope: str) -> bool:
    """Spend one use of a matching valid grant. False means "not authorised"."""
    tid = str(task_id or "")
    want = _key(action, scope)
    with _LOCK:
        for g in _GRANTS.get(tid, []):
            if g.is_valid() and _key(g.action, g.scope) == want:
                g.uses_remaining -= 1
                return True
    return False


def has_grant(task_id: str, action: str, scope: str) -> bool:
    """Non-consuming check, for building the tool surface."""
    tid = str(task_id or "")
    want = _key(action, scope)
    with _LOCK:
        return any(
            g.is_valid() and _key(g.action, g.scope) == want
            for g in _GRANTS.get(tid, [])
        )


# ── revoking ───────────────────────────────────────────────────────────────
def revoke_task(task_id: str, *, reason: str = "task_end") -> int:
    """Void every grant and pending card for one task.

    Denials are deliberately NOT cleared. A grant is authority and must not
    outlive the work it was given for; a decline is a restriction, and dropping
    it on task end points the wrong way — it would mean a refusal stopped
    applying the moment the task it was given in ended. They go on mode exit
    with everything else (:func:`revoke_all`).
    """
    tid = str(task_id or "")
    with _LOCK:
        n = len(_GRANTS.pop(tid, []))
        for rid in [k for k, v in _PENDING.items() if v.task_id == tid]:
            _PENDING.pop(rid, None)
    if n:
        obs.report(
            "supercomputer.permissions.revoke_task",
            RuntimeError(f"revoked {n} grants"),
            level="debug",
            task_id=tid,
            reason=reason,
        )
    return n


def revoke_all(*, reason: str = "mode_exit") -> int:
    """Void everything. Called on mode exit; a restart voids by ``boot_id``."""
    with _LOCK:
        n = sum(len(v) for v in _GRANTS.values())
        _GRANTS.clear()
        _DENIED.clear()
        _PENDING.clear()
        _RESOLVED.clear()
    return n


def clear_resolved() -> int:
    """Drop the answered-card history. Called when a new chat starts."""
    with _LOCK:
        n = len(_RESOLVED)
        _RESOLVED.clear()
    return n


def _prune_locked(now: Optional[float] = None) -> None:
    t = now or time.time()
    for rid in [k for k, v in _PENDING.items() if v.expired(t)]:
        req = _PENDING.pop(rid, None)
        if req is None:
            continue
        # An expired card is still an event the user should be able to see. It used
        # to simply vanish off the screen, which reads as "it did the thing".
        req.decision = "expired"
        req.decided_ts = t
        _RESOLVED[req.request_id] = req
        while len(_RESOLVED) > RESOLVED_KEEP:
            _RESOLVED.popitem(last=False)


def _reset_for_tests() -> None:
    with _LOCK:
        _PENDING.clear()
        _RESOLVED.clear()
        _GRANTS.clear()
        _DENIED.clear()


__all__ = [
    "DECISION_ALLOW",
    "DECISION_DECLINE",
    "DECISION_DISCUSS",
    "Grant",
    "PendingRequest",
    "request",
    "pending",
    "pending_snapshot",
    "resolved_snapshot",
    "clear_resolved",
    "note",
    "decide",
    "is_denied",
    "consume",
    "has_grant",
    "revoke_task",
    "revoke_all",
]
