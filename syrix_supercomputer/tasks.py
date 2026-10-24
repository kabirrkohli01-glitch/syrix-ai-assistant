"""Supercomputer task registry and state machine.

A "task" is one unit of agentic work with an immutable :class:`TaskContract`.
Ordinary questions answered without tools do not create a task.

States:

``planning``            building the plan; nothing has run yet
``running``             executing steps
``awaiting_permission`` a card is on screen; the loop is stopped
``paused_discussion``   the user chose "Chat about this"; nothing runs in the
                        background — this is a full stop, not a background wait
``paused_user``         the user pressed Pause or took control of the browser
``done`` / ``stopped`` / ``failed``  terminal; grants are revoked on entry

Ending a task always revokes its grants, so authority cannot outlive the work it
was given for.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import syrix_obs as obs

from .contract import TaskContract

STATE_PLANNING = "planning"
STATE_RUNNING = "running"
STATE_AWAITING_PERMISSION = "awaiting_permission"
STATE_PAUSED_DISCUSSION = "paused_discussion"
STATE_PAUSED_USER = "paused_user"
STATE_DONE = "done"
STATE_STOPPED = "stopped"
STATE_FAILED = "failed"

TERMINAL_STATES = (STATE_DONE, STATE_STOPPED, STATE_FAILED)
# States in which no step may execute. "paused_discussion" is here because the
# spec is explicit that "Chat about this" must not continue in the background.
HALTED_STATES = (
    STATE_AWAITING_PERMISSION,
    STATE_PAUSED_DISCUSSION,
    STATE_PAUSED_USER,
) + TERMINAL_STATES

_LOCK = threading.RLock()
_TASKS: Dict[str, "Task"] = {}
_CURRENT_ID = ""


# Step lifecycle, mirrored by the Steps panel's icons.
STEP_PENDING = "pending"
STEP_RUNNING = "running"
STEP_DONE = "done"
STEP_BLOCKED = "blocked"
STEP_SKIPPED = "skipped"


@dataclass
class Step:
    label: str
    status: str = STEP_PENDING
    detail: str = ""
    ts: float = field(default_factory=time.time)


@dataclass
class Task:
    task_id: str
    contract: TaskContract
    state: str = STATE_PLANNING
    steps: List[Step] = field(default_factory=list)
    created_ts: float = field(default_factory=time.time)
    ended_ts: float = 0.0
    result_summary: str = ""
    stop_reason: str = ""
    # Bumped on stop/pause/take-control. Any in-flight browser or model
    # operation carrying an older generation is discarded on return, which is
    # what makes Stop actually stop rather than merely stop issuing new work.
    generation: int = 0

    def is_halted(self) -> bool:
        return self.state in HALTED_STATES

    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES


def create(goal: str, contract: TaskContract) -> Task:
    """Register a new task and make it current."""
    global _CURRENT_ID
    tid = contract.task_id or uuid.uuid4().hex[:12]
    task = Task(task_id=tid, contract=contract)
    with _LOCK:
        _TASKS[tid] = task
        _CURRENT_ID = tid
    return task


def get(task_id: str) -> Optional[Task]:
    with _LOCK:
        return _TASKS.get(str(task_id or ""))


def current() -> Optional[Task]:
    with _LOCK:
        return _TASKS.get(_CURRENT_ID) if _CURRENT_ID else None


def recent(limit: int = 10) -> List[Task]:
    """Tasks in the order they were created, oldest first, capped at ``limit``.

    Used to give a new task a one-line digest of what the session has already
    done, so "go back to the first one" resolves against something. Insertion
    order is dict order in 3.7+, which is exactly creation order here.
    """
    with _LOCK:
        return list(_TASKS.values())[-max(1, int(limit)):]


def set_state(task_id: str, state: str, *, detail: str = "") -> bool:
    """Move a task to a new state. Entering a terminal state revokes grants."""
    task = get(task_id)
    if task is None:
        return False
    with _LOCK:
        task.state = state
        if state in TERMINAL_STATES:
            task.ended_ts = time.time()
            if detail:
                task.stop_reason = detail
    if state in TERMINAL_STATES:
        with obs.swallow("supercomputer.tasks.revoke_on_end", task_id=task_id):
            from . import permissions as _perm

            _perm.revoke_task(task_id, reason=f"task_{state}")
    return True


def add_step(task_id: str, label: str, *, status: str = STEP_PENDING, detail: str = "") -> None:
    task = get(task_id)
    if task is None:
        return
    with _LOCK:
        task.steps.append(Step(label=str(label or "")[:160], status=status, detail=str(detail or "")[:200]))


def plan_steps(task_id: str, labels: List[str]) -> None:
    """Seed the whole plan up front, all pending.

    The Steps panel is far more useful when it shows where the task is *going*,
    not just where it has been, and a plan that appears one line at a time reads
    as improvisation.
    """
    task = get(task_id)
    if task is None:
        return
    with _LOCK:
        task.steps.clear()
        for label in labels:
            task.steps.append(Step(label=str(label or "")[:160], status=STEP_PENDING))


def set_step(task_id: str, index: int, status: str, *, detail: str = "") -> None:
    """Advance one step. Out-of-range indexes are ignored rather than raising —
    a mis-numbered step must never abort a running task."""
    task = get(task_id)
    if task is None:
        return
    with _LOCK:
        if 0 <= int(index) < len(task.steps):
            step = task.steps[int(index)]
            step.status = str(status)
            step.ts = time.time()
            if detail:
                step.detail = str(detail)[:200]


def set_active_step(task_id: str, index: int) -> None:
    """Put the plan on step ``index``: earlier ones done, this one running.

    The agent says which step it is on, because only it knows. The alternative —
    advancing one step per tool call — desynchronises immediately: a single plan
    step takes several actions, so the panel showed "Review the results" as done
    while the first search was still running. A plan that lies is worse than no
    plan.
    """
    task = get(task_id)
    if task is None:
        return
    with _LOCK:
        idx = max(0, min(int(index), len(task.steps) - 1)) if task.steps else -1
        if idx < 0:
            return
        for i, s in enumerate(task.steps):
            if i < idx:
                if s.status in (STEP_PENDING, STEP_RUNNING):
                    s.status = STEP_DONE
                    s.ts = time.time()
            elif i == idx:
                if s.status != STEP_RUNNING:
                    s.status = STEP_RUNNING
                    s.ts = time.time()
            elif s.status == STEP_RUNNING:
                # Exactly one step runs at a time. Going back to an earlier step
                # used to leave the later one still marked running, so the panel
                # showed three things happening at once.
                s.status = STEP_PENDING
                s.ts = time.time()


def set_step_note(task_id: str, note: str) -> None:
    """Attach the agent's current reasoning to the running step.

    This is what turns the Steps panel from a static plan into a live view of
    what SYRIX is doing *right now*, without inventing a second progress
    channel that could disagree with the step states.
    """
    task = get(task_id)
    if task is None:
        return
    with _LOCK:
        for s in task.steps:
            if s.status == STEP_RUNNING:
                s.detail = str(note or "")[:200]
                return


def advance_step(task_id: str, *, detail: str = "") -> int:
    """Mark the running step done and start the next. Returns the new index."""
    task = get(task_id)
    if task is None:
        return -1
    with _LOCK:
        idx = -1
        for i, s in enumerate(task.steps):
            if s.status == STEP_RUNNING:
                s.status = STEP_DONE
                s.ts = time.time()
                if detail:
                    s.detail = str(detail)[:200]
                idx = i
                break
        nxt = idx + 1
        if 0 <= nxt < len(task.steps):
            task.steps[nxt].status = STEP_RUNNING
            task.steps[nxt].ts = time.time()
            return nxt
    return -1


def block_remaining(task_id: str, *, detail: str = "") -> None:
    """Mark the running step blocked and everything after it too, so the panel
    shows plainly what did not happen rather than leaving it looking pending."""
    task = get(task_id)
    if task is None:
        return
    with _LOCK:
        hit = False
        for s in task.steps:
            if s.status == STEP_RUNNING:
                hit = True
            if hit and s.status in (STEP_RUNNING, STEP_PENDING):
                s.status = STEP_BLOCKED
                s.ts = time.time()
                if detail and not s.detail:
                    s.detail = str(detail)[:200]


def grant_domain(task_id: str, url_or_host: str, *, permission_request_id: str) -> bool:
    """Widen one task's contract by one domain, on the back of an answered card.

    The contract itself is frozen, so this swaps in a new one built by
    :meth:`TaskContract.grant_domain` — which refuses without a request id. That
    keeps "only a human decision can widen scope" true even though the task
    object is mutable.
    """
    task = get(task_id)
    if task is None:
        return False
    try:
        widened = task.contract.grant_domain(
            url_or_host, permission_request_id=permission_request_id)
    except ValueError as exc:
        obs.report("supercomputer.tasks.grant_domain", exc, task_id=task_id)
        return False
    with _LOCK:
        task.contract = widened
    with obs.swallow("supercomputer.tasks.rebind_contract", task_id=task_id):
        from .browser import controller as _browser

        if _browser.contract() is not None:
            _browser.set_contract(widened)
    return True


def bump_generation(task_id: str) -> int:
    """Invalidate in-flight work for this task. Returns the new generation."""
    task = get(task_id)
    if task is None:
        return 0
    with _LOCK:
        task.generation += 1
        return task.generation


def generation(task_id: str) -> int:
    task = get(task_id)
    return task.generation if task else 0


def stop(task_id: str, *, reason: str = "user_stop") -> bool:
    """Hard stop: invalidate in-flight work, then terminate the task."""
    bump_generation(task_id)
    return set_state(task_id, STATE_STOPPED, detail=reason)


def cancel_all(*, reason: str = "mode_exit") -> int:
    """Stop every non-terminal task. Used on mode exit."""
    with _LOCK:
        ids = [t.task_id for t in _TASKS.values() if not t.is_terminal()]
    for tid in ids:
        stop(tid, reason=reason)
    return len(ids)


def snapshot() -> Dict[str, Any]:
    """HUD-facing view of the current task."""
    task = current()
    if task is None:
        return {"active": False}
    with _LOCK:
        return {
            "active": not task.is_terminal(),
            "task_id": task.task_id,
            "state": task.state,
            "goal": task.contract.goal[:400],
            "generation": task.generation,
            "created_ts": task.created_ts,
            "ended_ts": task.ended_ts,
            "result_summary": task.result_summary[:600],
            "stop_reason": task.stop_reason,
            "allowed_domains": sorted(task.contract.allowed_domains),
            "steps": [
                {"label": s.label, "status": s.status, "detail": s.detail, "ts": s.ts}
                for s in task.steps[-40:]
            ],
        }


def clear_all() -> int:
    """Forget every task. Used by the New chat button.

    A real operation with a real name, rather than production code reaching for
    ``_reset_for_tests`` — which would quietly acquire test-only semantics the
    first time anyone changed what "reset for tests" means.
    """
    with _LOCK:
        count = len(_TASKS)
    _reset_for_tests()
    return count


def _reset_for_tests() -> None:
    global _CURRENT_ID
    with _LOCK:
        _TASKS.clear()
        _CURRENT_ID = ""


__all__ = [
    "Task",
    "Step",
    "STATE_PLANNING",
    "STATE_RUNNING",
    "STATE_AWAITING_PERMISSION",
    "STATE_PAUSED_DISCUSSION",
    "STATE_PAUSED_USER",
    "STATE_DONE",
    "STATE_STOPPED",
    "STATE_FAILED",
    "TERMINAL_STATES",
    "HALTED_STATES",
    "STEP_PENDING",
    "STEP_RUNNING",
    "STEP_DONE",
    "STEP_BLOCKED",
    "STEP_SKIPPED",
    "create",
    "get",
    "current",
    "recent",
    "clear_all",
    "set_state",
    "add_step",
    "plan_steps",
    "set_step",
    "set_step_note",
    "set_active_step",
    "advance_step",
    "block_remaining",
    "grant_domain",
    "bump_generation",
    "generation",
    "stop",
    "cancel_all",
    "snapshot",
]
