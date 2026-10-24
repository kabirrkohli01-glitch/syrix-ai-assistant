"""Picking a task back up after the user answers a permission card.

Without this, the permission system is a dead end: the loop stops when a card is
raised (deliberately — a background loop spinning while a human decides is how
"waiting for permission" becomes a lie), and answering **Allow** would leave the
task parked with nothing to restart it. The user would have to retype the request,
which trains people to pre-approve everything instead.

So: an answered Allow schedules exactly one continuation, on a background
thread, and the report lands in the chat as a normal assistant message.

Three rules keep this from becoming a way for work to run unattended:

* only **Allow** resumes — Decline and "Chat about this" are full stops;
* only one continuation may be in flight per task, so a double-click on Allow
  cannot start two loops against the same browser;
* the resumed loop is the same loop, with the same gate in front of it, so the
  next side effect stops for the next card exactly like the first one did.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

import syrix_obs as obs

_LOCK = threading.RLock()
_IN_FLIGHT: Dict[str, bool] = {}

# The task, if any, that is waiting on something the user will type rather than
# click. Set when the agent calls ask_user, and when a card is answered with
# "Chat about this" or Decline.
#
# Until 2026-07-26 this did not exist, and the messages the mode showed were
# simply untrue: after_discuss promised "tell me in the chat and I will pick up
# from exactly here", after_decline promised "tell me a different way and I will
# take it", and neither had any mechanism behind it. The next thing the user typed
# started a brand-new task with a blank browser and no memory of the one it had
# just abandoned. A prompt that lies about what the software does is worse than
# no prompt.
_AWAITING: Dict[str, Any] = {"task_id": "", "kind": "", "question": "", "ts": 0.0}

# Kinds of wait, which differ only in how the resumed run is told why it moved.
WAIT_QUESTION = "question"      # the agent asked; the user is answering
WAIT_DISCUSSION = "discussion"  # The user pressed "Chat about this"
WAIT_REDIRECT = "redirect"      # The user declined; another route may exist


def await_reply(task_id: str, kind: str = WAIT_QUESTION, *, question: str = "") -> None:
    """Mark a task as waiting on something the user will type."""
    with _LOCK:
        _AWAITING.update({
            "task_id": str(task_id or ""),
            "kind": str(kind or WAIT_QUESTION),
            "question": str(question or "")[:400],
            "ts": time.time(),
        })


def awaiting() -> Dict[str, Any]:
    """The task waiting on a typed reply, or a blank record."""
    with _LOCK:
        return dict(_AWAITING)


def clear_awaiting() -> None:
    with _LOCK:
        _AWAITING.update({"task_id": "", "kind": "", "question": "", "ts": 0.0})


def _emit(text: str, *, request_id: str) -> None:
    """Put the continuation's report into the HUD chat.

    Goes through syrix_host's own append helper rather than writing
    hud_chat.json directly, so the message gets the same dedupe, transcript
    recording and redaction as every other assistant line.
    """
    body = str(text or "").strip()
    if not body:
        return
    with obs.swallow("supercomputer.resume.emit", rid=request_id):
        import syrix_host as _jc  # type: ignore

        append = getattr(_jc, "append_hud_chat_line", None)
        if callable(append):
            # Tagged EXPLICITLY, not left to the channel coercion. This report
            # can land while the workspace is closed and the session is merely
            # detached — and since 2026-07-27 the coercion deliberately does not
            # fire in that state, so that the user's normal chat is untouched while
            # a task finishes in the background. Without the tag here, a
            # completion report would drop into the normal chat thread instead
            # of the Supercomputer one it belongs to.
            append("assistant", body, request_id=request_id, force=True,
                   channel="supercomputer")
            return
    obs.report(
        "supercomputer.resume.emit_unavailable",
        RuntimeError("append_hud_chat_line unavailable; continuation report dropped"),
        rid=request_id,
    )


def _work(task_id: str, goal: str, request_id: str, config: Dict[str, Any],
          answer: str = "") -> None:
    from . import agent, loop, tasks

    try:
        report = loop.run(goal, task_id=task_id, request_id=request_id,
                          config=config, resuming=True, answer=answer)
        report.setdefault("task_id", task_id)
        _emit(agent.format_report(report), request_id=request_id)
    except Exception as exc:
        obs.report("supercomputer.resume.work", exc, task_id=task_id)
        with obs.swallow("supercomputer.resume.mark_failed", task_id=task_id):
            tasks.block_remaining(task_id, detail=type(exc).__name__)
            tasks.set_state(task_id, tasks.STATE_FAILED, detail=type(exc).__name__)
        _emit(f"I picked that task back up and it failed with {type(exc).__name__}. "
              f"Nothing further was run.", request_id=request_id)
    finally:
        with _LOCK:
            _IN_FLIGHT.pop(task_id, None)
        with obs.swallow("supercomputer.resume.push_state", task_id=task_id):
            from . import hud_api

            hud_api.push_state(config)


def _card_is_the_message(config: Optional[Dict[str, Any]]) -> bool:
    """Whether an answer renders inside its card instead of as a chat line.

    The user, 2026-07-28: "the card should be the message". Pressing **No** used to
    delete the card and post a paragraph in its place, so one decision produced a
    hole plus a wall of text — and because cards render below every chat line,
    the leftovers clustered in the wrong order too. The information did not
    change; where it lands did.

    Kill switch: ``supercomputer_card_is_the_message`` = false restores the old
    chat-line behaviour exactly.
    """
    return bool((config or {}).get("supercomputer_card_is_the_message", True))


def _stopped_where(tid: str) -> Dict[str, Any]:
    """What the task reached before it was stopped — for the card's detail."""
    from . import results, tasks

    task = tasks.get(tid)
    done = [r for r in results.for_task(tid)]
    detail: Dict[str, Any] = {}
    if task is not None and task.steps:
        got = [s.label for s in task.steps if s.status == tasks.STEP_DONE]
        if got:
            detail["reached"] = got[-1]
    sources = [r["location"] for r in done
               if r["kind"] == "source" and r.get("location")]
    files = [r["location"] for r in done if r["kind"] == "file"]
    # De-duplicated: the DBS run printed the same URL twice on its Sources line
    # because nothing folded repeats out of the results list.
    if sources:
        detail["sources"] = _dedupe(sources)[:4]
    if files:
        detail["files"] = _dedupe(files)[:4]
    return detail


def _dedupe(values: Any) -> list:
    seen: set = set()
    out: list = []
    for v in values:
        key = str(v or "").strip()
        if not key or key.lower() in seen:
            continue
        seen.add(key.lower())
        out.append(key)
    return out


def after_decline(task_id: str, description: str, *,
                  config: Optional[Dict[str, Any]] = None,
                  request_id: str = "") -> Dict[str, Any]:
    """Say what stopped, and how the user can finish it himself.

    A refusal that just goes quiet leaves him worse off than before he asked:
    the work is abandoned AND he does not know where it got to. So the task
    stops, and the answer says what was reached and what to do by hand — the
    browser is still sitting on the page, which is usually most of the way.

    Since 2026-07-28 that answer renders **inside the card that was declined**
    rather than as a fresh assistant message underneath it.
    """
    from . import permissions, tasks

    tid = str(task_id or "")
    detail = _stopped_where(tid)

    # The task stays resumable from the chat and its memory brief survives. What
    # does NOT come back is the declined action — `permissions._DENIED` still
    # covers it for the rest of the task, so a redirect can find another route
    # but cannot retry the thing you refused.
    await_reply(tid, WAIT_REDIRECT, question=description)

    rid = str(request_id or f"sc-decline-{tid}")
    if _card_is_the_message(config) and permissions.note(
        rid,
        headline="Not done. Task stopped here.",
        reached=detail.get("reached", ""),
        sources=detail.get("sources", []),
        files=detail.get("files", []),
        guidance=("Press **Take Control** to finish this step by hand — the browser "
                  "is still on the page I stopped at, signed in as you. Or tell me "
                  "another way to get there and I will take it; I still have "
                  "everything I found."),
    ):
        return {"ok": True, "task_id": tid, "rendered": "card"}

    task = tasks.get(tid)
    lines = ["Understood — I have not done that, and I have stopped the task here.",
             "", f"**What I was asking to do:** {description or 'the action on the card'}."]
    if detail.get("reached"):
        lines.append(f"**Got as far as:** {detail['reached']}.")
    if detail.get("sources"):
        lines.append(f"**Pages I read:** {', '.join(detail['sources'])}.")
    if detail.get("files"):
        lines.append(f"**Files I made:** {', '.join(detail['files'])}.")
    lines += [
        "",
        "**To do it yourself:** press **Take Control** and the browser is yours — "
        "it is still on the page I stopped at, signed in as you, so you can finish "
        "the step by hand. Press **Return Control** afterwards and tell me what "
        "happened if you want me to carry on from there.",
        "",
        "Or tell me a different way to get there and I will take it — I still have "
        "everything I found, so I will not start over.",
    ]
    _emit("\n".join(lines), request_id=rid)
    return {"ok": True, "task_id": tid, "rendered": "chat"}


def after_discuss(task_id: str, description: str, *,
                  config: Optional[Dict[str, Any]] = None,
                  request_id: str = "") -> Dict[str, Any]:
    """Stop and ask what to do differently. Nothing runs until the user answers."""
    from . import permissions

    tid = str(task_id or "")
    await_reply(tid, WAIT_DISCUSSION, question=description)

    rid = str(request_id or f"sc-discuss-{tid}")
    if _card_is_the_message(config) and permissions.note(
        rid,
        headline="Paused. Nothing is running.",
        guidance="Type what you would like me to do differently and I will pick up "
                 "from exactly here.",
    ):
        return {"ok": True, "task_id": tid, "rendered": "card"}

    _emit(
        f"Paused — nothing is running.\n\n"
        f"**I was about to:** {description or 'the action on the card'}.\n\n"
        f"What would you like me to do differently? Tell me in the chat and I "
        f"will pick up from exactly here.",
        request_id=rid,
    )
    return {"ok": True, "task_id": tid, "rendered": "chat"}


def after_allow(task_id: str, *, config: Optional[Dict[str, Any]] = None,
                request_id: str = "", answer: str = "") -> Dict[str, Any]:
    """Continue a task whose card was just allowed. Returns immediately.

    ``answer`` carries what the user said when the continuation came from the chat
    rather than an Allow button — a reply to an ``ask_user`` question, or a
    redirect after "Chat about this".
    """
    from . import tasks

    tid = str(task_id or "")
    task = tasks.get(tid)
    if task is None:
        return {"ok": False, "reason": "unknown_task"}
    # A discussion pause and a question pause are *meant* to be resumable from
    # the chat; only a genuinely finished task is not.
    if task.is_terminal():
        return {"ok": False, "reason": f"task_{task.state}"}

    with _LOCK:
        if _IN_FLIGHT.get(tid):
            return {"ok": False, "reason": "already_resuming"}
        _IN_FLIGHT[tid] = True

    thread = threading.Thread(
        target=_work,
        args=(tid, task.contract.goal, str(request_id or f"sc-resume-{tid}"),
              dict(config or {}), str(answer or "")),
        name=f"sc-resume-{tid[:8]}",
        daemon=True,
    )
    thread.start()
    return {"ok": True, "task_id": tid}


def continue_from_chat(text: str, *, config: Optional[Dict[str, Any]] = None,
                       request_id: str = "") -> Optional[Dict[str, Any]]:
    """Resume the awaiting task with what the user typed, IN THIS TURN.

    Runs synchronously, unlike :func:`after_allow`. The user just typed something and
    is looking at the chat, so the report belongs in the reply to that message
    rather than arriving later as a loose assistant line.

    Returns the report, or None when there is nothing to resume — the caller then
    treats the message as an ordinary new request.
    """
    from . import loop, tasks

    record = awaiting()
    tid = str(record.get("task_id") or "")
    if not tid:
        return None
    task = tasks.get(tid)
    if task is None or task.is_terminal():
        clear_awaiting()
        return None

    with _LOCK:
        if _IN_FLIGHT.get(tid):
            return None
        _IN_FLIGHT[tid] = True
    clear_awaiting()

    kind = str(record.get("kind") or WAIT_QUESTION)
    prefix = {
        WAIT_QUESTION: "You asked the user a question. They answered",
        WAIT_DISCUSSION: "The user paused you to talk it through. They now say",
        WAIT_REDIRECT: ("The user declined the action you asked about — you may NOT retry "
                        "it, by any route. They suggest instead"),
    }.get(kind, "The user says")
    answer = f"{prefix}: {str(text or '').strip()}"

    try:
        report = loop.run(task.contract.goal, task_id=tid,
                          request_id=str(request_id or f"sc-chat-resume-{tid}"),
                          config=dict(config or {}), resuming=True, answer=answer)
        report.setdefault("task_id", tid)
        return report
    except Exception as exc:
        obs.report("supercomputer.resume.from_chat", exc, task_id=tid)
        with obs.swallow("supercomputer.resume.from_chat_failed", task_id=tid):
            tasks.block_remaining(tid, detail=type(exc).__name__)
            tasks.set_state(tid, tasks.STATE_FAILED, detail=type(exc).__name__)
        return {
            "task_id": tid, "answer": "", "done": [], "declined": [], "notices": [],
            "question": "",
            "not_done": [f"picking that task back up — it failed with {type(exc).__name__}"],
        }
    finally:
        with _LOCK:
            _IN_FLIGHT.pop(tid, None)
        with obs.swallow("supercomputer.resume.from_chat_push", task_id=tid):
            from . import hud_api

            hud_api.push_state(config)


def in_flight(task_id: str = "") -> bool:
    with _LOCK:
        return bool(_IN_FLIGHT.get(str(task_id))) if task_id else bool(_IN_FLIGHT)


def _reset_for_tests() -> None:
    with _LOCK:
        _IN_FLIGHT.clear()
    clear_awaiting()


__all__ = ["after_allow", "after_decline", "after_discuss", "in_flight",
           "await_reply", "awaiting", "clear_awaiting", "continue_from_chat",
           "WAIT_QUESTION", "WAIT_DISCUSSION", "WAIT_REDIRECT"]
