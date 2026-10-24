"""The status label shown in the workspace's top-right pill.

Derived, never set. Every label is computed from state that already exists —
task state, browser control, the pending-card queue, the current step — so the
pill cannot drift from what is actually happening. Nothing writes a status
directly, which is what stops it saying "Working" while it is really blocked.

Labels (§13):
    planning     the plan is being built; nothing has run
    working      executing steps
    reading      the current step is reading a page
    waiting      a permission card is on screen
    user_control the user took the wheel
    verifying    checking the result before reporting
    blocked      stopped by policy, a decline, or an error
    complete     finished
"""
from __future__ import annotations

from typing import Any, Dict, Optional

PLANNING = "planning"
WORKING = "working"
READING = "reading"
WAITING = "waiting"
USER_CONTROL = "user_control"
VERIFYING = "verifying"
BLOCKED = "blocked"
COMPLETE = "complete"
IDLE = "idle"

LABELS = {
    PLANNING: "Planning",
    WORKING: "Working",
    READING: "Reading",
    WAITING: "Waiting for permission",
    USER_CONTROL: "User in control",
    VERIFYING: "Verifying",
    BLOCKED: "Blocked",
    COMPLETE: "Complete",
    IDLE: "Ready",
}

# Tone drives the dot colour in the pill.
TONE = {
    PLANNING: "busy",
    WORKING: "busy",
    READING: "busy",
    VERIFYING: "busy",
    WAITING: "attention",
    USER_CONTROL: "user",
    BLOCKED: "error",
    COMPLETE: "done",
    IDLE: "idle",
}

# Step labels that mean "reading" rather than generic work, so the pill can be
# specific without the loop having to declare it.
_READING_HINTS = ("read", "extract", "digest", "scan", "review")
_VERIFY_HINTS = ("verify", "check", "confirm", "validate", "test")


def derive(
    *,
    task: Optional[Dict[str, Any]] = None,
    browser: Optional[Dict[str, Any]] = None,
    pending_permission: Optional[list] = None,
) -> Dict[str, Any]:
    """Compute the current status from the snapshot pieces."""
    t = task or {}
    b = browser or {}
    pending = pending_permission or []

    state = str(t.get("state") or "")
    control = str(b.get("control") or "")

    # Order matters: the most specific and most user-relevant wins.
    if pending:
        key = WAITING
    elif control == "user":
        key = USER_CONTROL
    elif state in ("stopped", "failed"):
        key = BLOCKED
    elif state == "paused_discussion":
        key = WAITING
    elif state == "paused_user":
        key = USER_CONTROL
    elif state == "done":
        key = COMPLETE
    elif state == "planning":
        key = PLANNING
    elif state == "running":
        key = _running_detail(t)
    elif not t.get("task_id"):
        key = IDLE
    else:
        key = WORKING

    return {
        "key": key,
        "label": LABELS.get(key, "Working"),
        "tone": TONE.get(key, "busy"),
    }


def _running_detail(task: Dict[str, Any]) -> str:
    """Sharpen "working" into reading/verifying using the active step."""
    steps = task.get("steps") or []
    active = None
    for s in reversed(steps):
        if str(s.get("status") or "") in ("running", "active"):
            active = s
            break
    if active is None:
        return WORKING
    label = str(active.get("label") or "").lower()
    if any(h in label for h in _VERIFY_HINTS):
        return VERIFYING
    if any(h in label for h in _READING_HINTS):
        return READING
    return WORKING


__all__ = ["derive", "LABELS", "TONE", "PLANNING", "WORKING", "READING",
           "WAITING", "USER_CONTROL", "VERIFYING", "BLOCKED", "COMPLETE", "IDLE"]
