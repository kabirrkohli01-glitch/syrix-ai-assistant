"""Supercomputer turn front door.

``syrix_core_v2.runtime_spine`` hands a turn here and takes whatever comes back
verbatim. There is no path from here into SPARK/AUTO/ZENITH: this module either
answers, or says plainly what it could not do. Returning an empty or
``NEEDS_LEGACY_FALLBACK`` envelope would send the turn down the legacy chain and
produce an ordinary answer that looks like completed agentic work, so neither is
ever produced.

Phase 2 scope: ordinary conversation inside the mode, plus an honest report for
requests that need tools the later phases install. The answer-vs-action split
and the completion-report shape are already the ones Phase 6 will fill in, so
the contract does not move under the workspace UI.
"""
from __future__ import annotations

import re
import sys
from typing import Any, Dict, List, Optional

import syrix_obs as obs

from syrix_core_v2.response_bus import ReplyEnvelope, ReplyStatus

from . import MODEL_ID

# Verbs that mean "do something out there", as opposed to "tell me something".
# A false negative answers from the model when it could have looked; a false
# positive spins up a browser task for a question that did not need one. Both
# are recoverable, so this leans towards *doing*: Supercomputer exists to act,
# and "I could have checked but didn't" is the worse failure in this mode.
_ACTION_RE = re.compile(
    r"\b("
    r"open|go to|navigate|browse|visit|click|type into|fill (?:in|out)|submit|"
    r"log ?in|sign ?in|download|upload|send|email|e-mail|message|post|publish|reply to|"
    r"delete|remove|unsubscribe|book|order|buy|purchase|pay|"
    r"build|create|make me|generate|draft|write me|"
    r"scrape|check|look up|find|search|research|compare|verify|fact.?check|"
    r"read (?:my|the|this)|summari[sz]e|analyse|analyze|watch|"
    r"latest|current|today|right now|price of|news (?:on|about)"
    r")\b",
    re.IGNORECASE,
)

# "What's on my screen" — in this mode that means the embedded browser, and it
# can only ever mean that. There is no capture of the desktop, of another
# window, or of anything outside the HUD, so the honest reading of the question
# is "look at the page you have open". Routed as an action so it goes through
# the loop and actually looks, rather than being answered from memory.
_SCREEN_RE = re.compile(
    # "what's" / "whats" / "what is" — people type it every way, and losing the
    # match means answering from memory instead of looking at the page.
    r"\b(what(?:'?s| is| are)?\s+(?:on|in)\s+(?:my|the|this)\s+(?:screen|display|browser|page|tab)"
    r"|look at (?:my|the)\s+(?:screen|page|tab|browser)"
    r"|what (?:am i|do you) (?:looking at|see)"
    r"|read (?:my|the) screen"
    r"|describe (?:this|the) (?:page|screen|tab))\b",
    re.IGNORECASE,
)

# Questions that are about SYRIX or the conversation, never about the world.
# These must NOT open a browser: "what can you do" answered by a web search is
# the exact "work theater" failure this mode is supposed to end.
_SELF_RE = re.compile(
    r"^\s*(?:who are you|what are you|what can you do|what all can you do|"
    r"how do you work|are you (?:there|working|ok)|hello|hi|hey|thanks|thank you|"
    r"what did you just|what was that|explain that|say that again)\b",
    re.IGNORECASE,
)

# Identity questions, answered deterministically before any model sees them.
# Normal chat has four separate fast paths for these (intent_planner's
# _RE_IDENTITY, deterministic_answers, runtime_identity, identity_adapter) —
# and every one of them lives BELOW the branch in runtime_spine that hands a
# Supercomputer turn to this module. So "who made you" reached the raw model
# here, and the model answered with the name of whoever trained it.
_IDENTITY_RE = re.compile(
    r"^\s*(?:so\s+|and\s+|but\s+)?(?:"
    r"who\s+(?:made|created|built|programmed|designed|developed|owns|runs|controls)\s+you"
    r"|who(?:'s| is)\s+your\s+(?:creator|maker|developer|owner|builder|father|dad)"
    r"|who\s+(?:were|are)\s+you\s+(?:made|built|created|designed)\s+for"
    r"|who\s+(?:are|r)\s+(?:you|u)\b"
    r"|what\s+(?:are|r)\s+(?:you|u)\b"
    r"|what\s+is\s+syrix"
    r"|who(?:'s| is)\s+kabirr"
    r"|are\s+you\s+(?:jarvis|chatgpt|claude|gemini|gpt|glm|zai|z\.ai|qwen|llama)\b"
    r"|which\s+(?:model|ai|llm)\s+are\s+you"
    r"|what\s+model\s+are\s+you"
    r")\b",
    re.IGNORECASE,
)


def _envelope(
    rid: str,
    text: str,
    *,
    status: str = ReplyStatus.SUCCESS,
    route: str = "supercomputer_chat",
    provider: str = "",
    error_safe: Optional[str] = None,
    diagnostics: Optional[Dict[str, Any]] = None,
) -> ReplyEnvelope:
    diag = {"syrix_model": MODEL_ID}
    if diagnostics:
        diag.update(diagnostics)
    return ReplyEnvelope(
        status=status,
        intent="supercomputer",
        route=route,
        provider=provider,
        chat_text=text,
        speak_text=text,
        error_safe=error_safe,
        request_id=rid,
        diagnostics=diag,
    )


# An instruction to actually transact, as opposed to "should I buy?" or
# "analyse whether to buy" — those are analysis requests and run untouched.
_TRADE_ORDER_RE = re.compile(
    r"\b(?:buy|sell|short|long)\b[^.?!]{0,40}\b(?:for me|now|right now|immediately|"
    r"my|asap|before it|at market)\b"
    r"|\b(?:place|submit|execute|confirm|put in)\s+(?:the\s+|a\s+|my\s+)?"
    r"(?:order|trade|buy|sell|position)\b"
    r"|\b(?:go|get me)\s+(?:long|short)\b"
    r"|\b(?:buy|sell)\s+(?:me\s+)?\d",
    re.IGNORECASE,
)


def _asks_to_place_a_trade(text: str) -> bool:
    return bool(_TRADE_ORDER_RE.search(str(text or "")))


def looks_like_screen_question(text: str) -> bool:
    """True for "what's on my screen" and its relatives."""
    return bool(_SCREEN_RE.search(str(text or "")))


def looks_like_action(text: str, config: Optional[Dict[str, Any]] = None) -> bool:
    """True when the request asks for something to be done, not explained.

    The regex is the fast path and settles the overwhelming majority. What it
    cannot settle is phrasing that names no verb from the list — "sort out my
    CV", "my downloads folder is a mess", "I need something for the Tuesday
    meeting". Those fell through to ordinary chat, which in an agentic mode is
    the worst possible answer: SYRIX explained how the user could do it himself.

    So an unmatched request that is not self-referential gets one cheap
    classifier call before it is written off as conversation. The call defaults
    to *acting*, because in this mode "I could have checked but didn't" is the
    worse failure.
    """
    body = str(text or "")
    if looks_like_screen_question(body):
        return True
    stripped = body.strip()
    if _SELF_RE.match(stripped):
        return False
    if _ACTION_RE.search(body):
        return True
    # No config means "just the predicate" — pure, deterministic, no model call.
    # The semantic fallback is a property of a live turn, which always has one.
    if config is None:
        return False
    cfg = config
    if not bool(cfg.get("supercomputer_semantic_router_enabled", True)):
        return False
    # Very short fragments with no verb are usually conversation ("thanks",
    # "nice one"). Not worth a model call, and acting on them is jarring.
    if len(stripped.split()) < 3:
        return False
    return _classify_action(stripped, cfg)


def _classify_action(text: str, cfg: Dict[str, Any]) -> bool:
    """Does this need doing, or only answering? Defaults to doing."""
    from . import models

    with obs.swallow("supercomputer.spine.classify_action"):
        reply, _ = models.chat(
            models.ROLE_PLAN,
            [{"role": "user", "content":
                f"A user said this to an assistant that has a real web browser and can "
                f"make files:\n\n{text[:600]}\n\n"
                f"Does this need the assistant to DO something — browse, look something "
                f"up, make a file, act on the web — or is it just conversation it can "
                f"answer from what it already knows?\n"
                f"Reply with exactly one word: DO or TALK."}],
            config=cfg, max_tokens=8, timeout_s=10.0,
            grade=models.GRADE_ROUTINE, purpose="classify_action",
        )
        verdict = str(reply or "").strip().upper()[:24]
        if "TALK" in verdict:
            return False
        if re.search(r"\bDO\b", verdict):
            return True
    # No usable verdict. Fall back to the regex's answer — which is False, since
    # this only runs when the regex found nothing. Deliberately NOT "act
    # anyway": an unparseable reply is not evidence, and degrading to exactly
    # the previous behaviour is the only change here that is guaranteed safe.
    return False


def handle_turn(
    text: str,
    *,
    request_id: str = "",
    source: str = "hud_typed",
    config: Optional[Dict[str, Any]] = None,
) -> ReplyEnvelope:
    """Handle one Supercomputer turn. Never raises, never falls back."""
    rid = str(request_id or "")
    cfg = config or {}
    question = str(text or "").strip()

    if not question:
        return _envelope(
            rid,
            "I did not catch a question there. Say what you would like me to do.",
            route="supercomputer_empty",
        )

    # Private mode promises nothing leaves the PC, and this mode's job is to
    # browse. The workspace will not open while it is on; this refuses a turn
    # that reaches here anyway.
    private = sys.modules.get("syrix_private")
    if private is not None and private.active():
        return _envelope(
            rid,
            "Supercomputer browses the web, so it is off while Private mode is on. "
            "Turn Private mode off to use it. Nothing was opened, changed or sent.",
            status=ReplyStatus.CLARIFY,
            route="supercomputer_private",
        )

    # Supercomputer runs ONLY inside its own workspace. A session is opened by
    # `supercomputer_enter`, which the workspace sends on mount — and by nothing
    # else. A marked turn arriving from normal HUD chat is refused here rather
    # than quietly opening a session and running a browser task behind a chat
    # window that shows none of it.
    #
    # This used to call session.enter() as a fallback, which meant normal chat
    # could execute agentic work with no workspace, no Steps panel and no
    # visible browser. Spark, Zenith and Auto stay exactly as they were; this is
    # the line that keeps them that way.
    from . import session as _session

    if not _session.is_active():
        return _envelope(
            rid,
            "I can do that, but not from here — Supercomputer is its own mode. "
            "Pick **Supercomputer** from the model menu and ask me there, and I "
            "will do it in the browser where you can watch and stop it.\n\n"
            "Nothing was opened, changed or sent.",
            status=ReplyStatus.CLARIFY,
            route="supercomputer_not_open",
        )

    remember("user", question)

    # Is a task sitting there waiting on exactly this message? If the agent asked
    # a question, or the user pressed "Chat about this" or Decline, the reply belongs
    # to that task — resumed with its memory intact — not to a brand-new one.
    envelope = _identity_answer(rid, question, cfg)
    if envelope is None:
        envelope = _maybe_continue(rid, question, cfg)
    if envelope is None:
        envelope = (_handle_action(rid, question, cfg) if looks_like_action(question, cfg)
                    else _handle_question(rid, question, cfg))
    remember("assistant", envelope.chat_text)
    return envelope


def _identity_answer(rid: str, text: str, cfg: Dict[str, Any]) -> Optional[ReplyEnvelope]:
    """Answer "who made you" here, from the same source normal chat uses.

    Deliberately reuses :func:`syrix_core_v2.adapters.identity_adapter.
    canned_answer` rather than restating the facts: two copies of who built
    SYRIX is exactly how they drift apart. Nothing else about this mode
    changes — a question that is not about identity falls straight through.
    """
    if not bool(cfg.get("supercomputer_identity_fastpath_enabled", True)):
        return None
    if _IDENTITY_RE.match(str(text or "")):
        try:
            from syrix_core_v2.adapters import identity_adapter as _identity

            answer = str(_identity.canned_answer(text, config=cfg) or "").strip()
        except Exception as exc:
            obs.report("supercomputer.identity", exc, level="debug", rid=rid)
            return None
        if not answer:
            return None
        return _envelope(rid, answer, route="supercomputer_identity",
                         provider="identity_adapter")

    # The shared self-knowledge service covers public interface questions
    # (modes, widgets, connectors and settings) as well as protected identity
    # boundaries.  It must run before this mode's model gets a chance to invent
    # private implementation detail.
    try:
        from syrix_core_v2 import runtime_identity as _runtime_identity
        report = _runtime_identity.try_answer(text, config=cfg)
        if report and str(report.get("chat") or "").strip():
            return _envelope(
                rid, str(report["chat"]), route="supercomputer_identity",
                provider="runtime_identity",
            )
    except Exception as exc:
        obs.report("supercomputer.runtime_identity", exc, level="debug", rid=rid)
    return None


def _maybe_continue(rid: str, text: str, cfg: Dict[str, Any]) -> Optional[ReplyEnvelope]:
    """Resume a task awaiting a typed reply, or None to treat this as new.

    The judgement call is whether the user is answering or changing the subject. The
    cost is asymmetric: resuming a task with an unrelated message wastes a run on
    the wrong goal, while starting fresh on what was meant as an answer merely
    loses context. So this leans towards resuming only when it is fairly sure —
    a short reply, a continuation phrase, or a classifier that says so.
    """
    if not bool(cfg.get("supercomputer_resume_from_chat_enabled", True)):
        return None
    from . import resume as _resume

    record = _resume.awaiting()
    if not record.get("task_id"):
        return None
    if not _reads_as_reply(text, str(record.get("question") or ""), cfg, rid=rid):
        # A genuinely new request. Let the old task go rather than leaving it
        # parked forever, and say so, so nothing silently disappears.
        _resume.clear_awaiting()
        with obs.swallow("supercomputer.spine.abandon_paused"):
            from . import tasks as _tasks

            tid = str(record.get("task_id") or "")
            task = _tasks.get(tid)
            if task is not None and not task.is_terminal():
                _tasks.block_remaining(tid, detail="superseded_by_new_request")
                _tasks.set_state(tid, _tasks.STATE_STOPPED, detail="superseded")
        return None

    report = _resume.continue_from_chat(text, config=cfg, request_id=rid)
    if report is None:
        return None

    from . import agent

    body = agent.format_report(report)
    waiting = bool(report.get("awaiting_permission"))
    failed = not report.get("answer") and not report.get("question") and not waiting
    status = ReplyStatus.SUCCESS
    if report.get("question") or waiting:
        status = ReplyStatus.CLARIFY
    elif failed:
        status = ReplyStatus.FAILED
    return _envelope(
        rid, body,
        status=status,
        route="supercomputer_continued",
        error_safe="continuation_produced_nothing" if failed else None,
        diagnostics={"sc_task_id": report.get("task_id", ""),
                     "sc_steps": report.get("steps_used", 0),
                     "sc_continued": True},
    )


# Phrases that are unambiguously "carry on with what you were doing".
_CONTINUE_RE = re.compile(
    r"^\s*(?:yes|yeah|yep|ok(?:ay)?|sure|go|go ahead|do it|carry on|continue|please do|"
    r"correct|that'?s right|the (?:first|second|third|last) one|use the|"
    r"no|nope|not that|don'?t|skip it|instead)\b",
    re.IGNORECASE,
)
# Words that mean a genuinely different job, not an answer to a question.
_NEW_TASK_RE = re.compile(
    r"^\s*(?:now |next |also |then )?(?:open|go to|search for|find me|make me|build|"
    r"write me|create a|download|check the|look up)\b",
    re.IGNORECASE,
)


def _reads_as_reply(text: str, question: str, cfg: Dict[str, Any], *, rid: str = "") -> bool:
    body = str(text or "").strip()
    if not body:
        return False
    if _NEW_TASK_RE.match(body):
        return False
    if _CONTINUE_RE.match(body):
        return True
    # Short answers to a direct question are answers. "Modern, navy blue" is not
    # a new task by any reading.
    if len(body.split()) <= 12:
        return True
    if not question or not bool(cfg.get("supercomputer_semantic_router_enabled", True)):
        return False
    return _classify_reply(question, body, cfg, rid=rid)


def _classify_reply(question: str, text: str, cfg: Dict[str, Any], *, rid: str = "") -> bool:
    """One cheap call for the genuinely ambiguous middle. Defaults to False."""
    from . import models

    with obs.swallow("supercomputer.spine.classify_reply", rid=rid):
        reply, _ = models.chat(
            models.ROLE_PLAN,
            [{"role": "user", "content":
                f"An assistant asked the user:\n{question[:400]}\n\n"
                f"The user then said:\n{text[:600]}\n\n"
                f"Is the user answering that question (or adjusting the same job), or "
                f"asking for something completely different?\n"
                f"Reply with exactly one word: ANSWER or NEW."}],
            config=cfg, request_id=rid, max_tokens=8, timeout_s=10.0,
            grade=models.GRADE_ROUTINE, purpose="classify_reply",
        )
        return str(reply or "").strip().upper().startswith("ANSWER")
    return False


# Conversation memory for this session only. Held in memory, capped, and wiped
# on mode exit — "deleting the chat removes temporary task data" has to include
# what was said, not only what was browsed.
_HISTORY_TURNS = 12
_history: list = []


def remember(role: str, text: str) -> None:
    body = str(text or "").strip()
    if not body:
        return
    _history.append({"role": str(role or "user"), "content": body[:4000]})
    del _history[: max(0, len(_history) - _HISTORY_TURNS * 2)]


def forget() -> int:
    n = len(_history)
    _history.clear()
    return n


def history() -> list:
    return list(_history)


# How many earlier turns and finished tasks a new task is told about. Small on
# purpose: this is context, not a transcript, and it is prepended to a system
# prompt that already carries the tool list and the plan.
_CONTEXT_TURNS = 6
_CONTEXT_TASKS = 3
_CONTEXT_CHARS = 2000


def _conversation_context(cfg: Dict[str, Any]) -> str:
    """What has already happened in this session, for the agent to work against.

    Until 2026-07-26 the loop was handed one sentence and nothing else, so the
    single most natural thing to say in a working session — "go back to the first
    one and change the title" — could not work at all. The loop had no idea a
    first one existed.

    Two sources, both cheap: the last few chat turns, and a one-line digest of
    each finished task's memory brief (what it was for, what it found, what it
    made). Neither is authority — the GOAL for this task is still only what the user
    just said.
    """
    parts: List[str] = []

    with obs.swallow("supercomputer.spine.context_tasks"):
        from . import memory as _memory
        from . import tasks as _tasks

        digests: List[str] = []
        for task in list(_tasks.recent(_CONTEXT_TASKS * 3))[-_CONTEXT_TASKS:]:
            line = _memory.summarise(task.task_id)
            if line:
                digests.append(f"  - {line}")
        if digests:
            parts.append("Tasks already done in this session:\n" + "\n".join(digests))

    turns = history()[-(_CONTEXT_TURNS * 2):]
    if turns:
        said = "\n".join(
            f"  {'The user' if t.get('role') == 'user' else 'You'}: {str(t.get('content') or '')[:300]}"
            for t in turns
        )
        parts.append("Recent conversation:\n" + said)

    return "\n\n".join(parts)[:_CONTEXT_CHARS]


def _v3_thread_id() -> str:
    """The v3 conversation thread for this Supercomputer chat.

    Keyed by the chat's own history id, so a chat reopened from history picks
    up its thread again, and never "default" — the thread normal chat uses —
    so nothing said in one mode can surface in the other.
    """
    sid = ""
    with obs.swallow("supercomputer.spine.v3_thread_id"):
        from . import chat_thread as _chat_thread
        sid = _chat_thread.active_id()
    if not sid:
        with obs.swallow("supercomputer.spine.v3_thread_session"):
            from . import session as _session
            sid = _session.session_id()
    return "supercomputer-" + (str(sid or "") or "session")


def ask_v3(v3_turn, text: str, rid: str):
    """One question to the v3 core on this chat's own thread. Tests replace
    this function to stand in for the model."""
    return v3_turn.handle(
        text,
        source="supercomputer",
        session_id=_v3_thread_id(),
        request_id=rid,
        append_chat=False,
        speak=False,
    )


# The workspace's depth control, as the v3 model marker. Spark is v3's default
# and carries no marker.
_DEPTH_MARKER = {"zenith": "[[SYRIX_MODEL:zenith]] ", "auto": "[[SYRIX_MODEL:auto]] "}


def _handle_question(rid: str, question: str, cfg: Dict[str, Any]) -> ReplyEnvelope:
    """Ordinary conversation inside the mode — no browser, answered by v3.

    The same core as normal chat — live research, grounded answers, the
    thread's own context — on a thread of its own (see _v3_thread_id). Until
    2026-09-27 this was a single call to the v2 engine with the session
    history pasted in, which is why questions asked here read far weaker than
    the same question asked in chat.
    """
    try:
        from syrix_v3 import turn as _v3_turn
        from . import models as _models
    except Exception as exc:
        obs.report("supercomputer.spine.v3_import", exc, rid=rid)
        return _envelope(
            rid,
            "I could not reach my language model just now, so I have not answered. "
            "Nothing else was run.",
            status=ReplyStatus.UNAVAILABLE,
            route="supercomputer_brain_unavailable",
            error_safe=f"v3_import:{type(exc).__name__}",
        )

    marker = _DEPTH_MARKER.get(str(_models.depth() or ""), "")
    try:
        answer = ask_v3(_v3_turn, marker + question, rid)
    except Exception as exc:
        obs.report("supercomputer.spine.v3_turn", exc, rid=rid)
        answer = None
    text = str(getattr(answer, "chat_text", "") or "").strip()
    if not text:
        return _envelope(
            rid,
            "My language model did not come back with an answer, so I have nothing "
            "for you on that. Nothing was run.",
            status=ReplyStatus.UNAVAILABLE,
            route="supercomputer_brain_empty",
            error_safe="brain_empty_reply",
        )
    # v3's answer, sources and cards, filed as a Supercomputer reply so the
    # workspace and the history treat it as one of theirs.
    answer.intent = "supercomputer"
    answer.route = "supercomputer_chat"
    answer.request_id = rid
    answer.diagnostics = dict(answer.diagnostics or {}, syrix_model=MODEL_ID)
    return answer


def _handle_action(rid: str, request: str, cfg: Dict[str, Any]) -> ReplyEnvelope:
    """An action request. Hands off to the agent loop, which owns plan/act/verify.

    Destinations come from :mod:`syrix_supercomputer.destinations` — explicit
    URLs plus services the user named by name. An empty list is no longer a dead
    end: the loop searches for where to go, which is what an agent is for.
    Everything it does passes the gate, so a side effect still stops for a
    permission card, and the report states what did NOT happen as plainly as
    what did.
    """
    from . import agent, destinations
    from .browser import controller as _browser

    urls = destinations.resolve(request)

    # "What's on my screen" scopes itself to the tab that is open. Said plainly
    # in the goal so the agent reads the page rather than searching the web for
    # what a screen is, and so the answer can never be about anything outside
    # the embedded browser — which is all SYRIX can see.
    # "Buy it for me, now." SYRIX does not place trades — the gate refuses the
    # click outright — but stopping at "I'm sorry, I can't help with that" is
    # useless and, worse, it is what the planning model produces on its own when
    # handed a goal it will not carry out. What the user actually needs at that
    # moment is the analysis: the case both ways, the levels, what would
    # invalidate it, and the order left for him to place. So the request is
    # answered as the analysis it should have been, and the refusal is said
    # plainly rather than implied.
    if _asks_to_place_a_trade(request):
        request = (
            f"{request}\n\n[Scope: DO NOT place, submit or confirm any trade — that is "
            f"refused here and it is the user's click, not yours. Do the analysis instead "
            f"and do it properly: current price and where it came from, the bull case, "
            f"the bear case, support and resistance, what would invalidate the setup, "
            f"and the risk. Open the chart and read real current data — do not answer "
            f"from memory. Begin your answer by saying you do not place trades and that "
            f"this is the analysis so he can decide.]"
        )

    if looks_like_screen_question(request):
        request = (
            f"{request}\n\n[Scope: answer ONLY from the page currently open in the "
            f"Supercomputer browser. Read it, and if it needs looking at visually "
            f"use look_at_screen. Do not search the web and do not open anything new.]"
        )
        urls = []

    # When the user says HOW, that is the method, not a suggestion. "Just search
    # the DBS website", "do it in the tab already open" — ignored, the mode
    # would run nine Google searches and arrive by a route he had explicitly
    # ruled out. Silent when he only said WHAT he wants, which is the common
    # case and the one where working out the route is the whole job.
    with obs.swallow("supercomputer.spine.directives", rid=rid):
        from . import directives

        if directives.enabled(cfg):
            note = directives.scope_note(request)
            if note:
                request = f"{request}{note}"
                if directives.forbids_search(request):
                    urls = []

    context = _conversation_context(cfg)

    opened = _browser.ensure_open(config=cfg)
    if not opened.get("ok"):
        reason = str(opened.get("reason") or "unavailable")
        detail = (
            "Browser automation is switched off in Supercomputer settings, so I "
            "did not open anything."
            if reason == "browser_capability_disabled"
            else "The embedded browser could not start, so I have not opened anything."
        )
        return _envelope(
            rid,
            f"{detail}\n\nNothing was opened, changed or sent.",
            status=ReplyStatus.UNAVAILABLE,
            route="supercomputer_browser_unavailable",
            error_safe=f"browser_unavailable:{reason}",
        )

    report = agent.run(request, urls, rid=rid, cfg=cfg, context=context)
    text = agent.format_report(report)
    waiting = bool(report.get("awaiting_permission"))
    failed = not report.get("answer") and not report.get("question") and not waiting
    status = ReplyStatus.SUCCESS
    if report.get("question") or waiting:
        status = ReplyStatus.CLARIFY
    elif failed:
        status = ReplyStatus.FAILED
    return _envelope(
        rid, text,
        status=status,
        route=f"supercomputer_{report.get('kind') or 'task'}",
        error_safe="task_produced_nothing" if failed else None,
        diagnostics={"sc_task_id": report.get("task_id", ""),
                     "sc_steps": report.get("steps_used", 0)},
    )


__all__ = ["handle_turn", "looks_like_action", "looks_like_screen_question",
           "remember", "forget", "history"]
