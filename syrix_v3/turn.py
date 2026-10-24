"""One turn, start to finish. The single entry point into v3.

``handle(text)`` is what typed chat, Voice Mode and the probe all call. It
keeps the signature the previous spine exposed so the host can be repointed
at it without the caller changing, and it returns the same ``ReplyEnvelope``
every surface downstream already reads.

The order is fixed and short: resolve the mode, build the context, compose
the prompt, run the loop, record the turn, return the envelope. There is no
pre-dispatch cascade of fast paths, canned branches and deterministic
answerers ahead of it, and no post-processing stack behind it re-reading what
came out. Those were the two halves of the forty seconds of non-model time in
the baseline.
"""
from __future__ import annotations

import concurrent.futures as cf
import time
import uuid
from typing import Any, Dict, List, Optional

from . import budget as budgets
from . import attachments, compute, context, creator, form, live, loop, media, owner, panels, persona, polish, research, router, status, subagents, wire
from .tools import look as look_tool
from .tools import names as tool_names
from .tools import pages as page_store
import syrix_obs

# The tools that reach the open web. Withheld on a question about SYRIX
# itself, where the web only knows other things called Syrix.
_WEB_TOOLS = frozenset({"search", "read", "find_on_page"})


# Live readings run beside web gathering, so they need their own threads.
_LIVE_POOL = cf.ThreadPoolExecutor(max_workers=2, thread_name_prefix="v3-live")
_LIVE_WAIT_S = 5.0

_LIVE_HEADER = (
    "LIVE DATA — read directly from the source moments ago. These figures are "
    "authoritative and current: use them exactly, say where and when they were "
    "read, and prefer them over anything in the web results below."
)


def _collect(future: Optional["cf.Future"]) -> Optional["live.Fact"]:
    if future is None:
        return None
    try:
        return future.result(timeout=_LIVE_WAIT_S)
    except Exception:
        return None


def _local_lane_note() -> str:
    """What the local fallback is doing, so the sentence above is true."""
    try:
        from . import local_model
        state = local_model.status()
    except Exception:
        return ""
    if state.running:
        return (" The local model is up and it could not answer either — "
                "that is worth a look.")
    return (" The local model is the lane that never runs out, and it did "
            "not come up this time.")


def _no_answer_message(notes: List[str]) -> str:
    """What to say when no lane would answer.

    "I could not reach a model lane" tells the reader nothing they can do
    anything about. Every free lane runs to a published per-minute and
    per-day allowance and they refill; the local model is the floor that
    does not, and it is only a floor while it is running. Saying which of
    those is the case is the difference between a dead end and a next step.
    """
    joined = " ".join(notes).lower()
    if "no lane available" in joined or "exhausted" in joined:
        return (
            "Every free lane is at its limit for the moment — they run to a "
            "per-minute allowance and refill within the minute, so this "
            "usually clears if you ask again shortly."
        ) + _local_lane_note()
    return ("I did not get an answer out of that one. Ask again and I will "
            "take another run at it.")




def _destination_only(evidence) -> bool:
    """Is every source a page of the site or profile the user named?"""
    return bool(evidence and (evidence.site or evidence.profile) and evidence.sources
                and all(s.provider in ("site", "profile") for s in evidence.sources))


def _send_card_early(future: "cf.Future", rid: str) -> None:
    try:
        fact = future.result()
        card = getattr(fact, "card", None)
        if card:
            wire.media(rid, card=dict(card))
    except Exception as exc:
        syrix_obs.report("v3.turn.card_early", exc, level="debug", rid=rid)


def handle(
    text: str,
    *,
    source: str = "chat",
    append_chat: bool = True,
    speak: bool = False,
    session_id: str = "default",
    request_id: str = "",
    doc_ids: Optional[List[str]] = None,
    images: Optional[List[Dict[str, Any]]] = None,
    **_ignored: Any,
):
    """Answer one message.

    ``append_chat`` and ``speak`` are the host's concerns — writing the chat
    line and deciding whether to voice it — and are accepted so the call
    signature matches what already calls into the runtime. The envelope
    carries everything either of them needs.
    """
    started = time.monotonic()
    rid = str(request_id or uuid.uuid4().hex[:12])
    raw = str(text or "")

    asked = budgets.normalise(context.read_marker(raw))
    deep_asked = context.deep_research_requested(raw)
    # Effort is read for every Spark turn, not only an Auto one. Picking
    # SPARK by hand asks for the fast expert, not for a fixed amount of
    # work, and a hard question deserves room whichever way it arrived.
    routed = router.decide(context.strip_markers(raw))
    if deep_asked:
        # The Deep Research checkbox is its own control and can be ticked
        # alongside any switcher position. Ticking it is an explicit request
        # for depth, so it settles the mode.
        mode, effort = budgets.ZENITH, budgets.NORMAL
    elif asked == budgets.AUTO:
        mode, effort = routed.mode, routed.effort
    else:
        mode = asked
        effort = routed.effort if mode == budgets.SPARK else budgets.NORMAL
    plan = budgets.for_mode(mode, effort)

    if deep_asked:
        wire.step(wire.KIND_MODE, "Deep research", request_id=rid,
                  detail="asked for explicitly")
    elif asked == budgets.AUTO:
        wire.step(wire.KIND_MODE, f"Auto chose {mode}",
                  request_id=rid, detail=routed.why()[:160])
    wire.step(wire.KIND_INTENT, "Reading the question", request_id=rid, detail=mode)

    ctx = context.build(
        raw,
        mode=mode,
        session_id=session_id,
        request_id=rid,
        voice=bool(speak) or source == "voice",
        doc_ids=doc_ids,
        images=images,
    )
    # Pasted images are held for the life of the turn so the look tool can
    # reach them by number, and released afterwards so one turn's
    # screenshot can never answer the next turn's question.
    look_tool.register_images(rid, images or [])

    # Who made SYRIX has one answer. Recognised here, on the message as typed,
    # so the rewrite and the web never see it; see creator.py.
    creator_lang = None if (doc_ids or images) else creator.asks(ctx.text)
    # Questions about him are answered from what SYRIX knows about him, not a
    # web search for the name; see owner.py.
    _last_reply = next((str(t.get("content") or "") for t in reversed(ctx.history or [])
                        if t.get("role") == "assistant"), "")
    owner_asked = (not doc_ids and not images) and owner.asks(ctx.text, last_reply=_last_reply)

    # Gathering starts before the model is asked anything. With retrieval
    # behind a tool call, a Spark turn spends one round deciding to search
    # and the next writing the answer, which leaves no round for opening a
    # page — measured, and it answered a "what is it trading at" question
    # from a two-year-old closing price while holding the live quote page in
    # its search results.
    # What the user attached is read first, and it decides whether the web is worth
    # searching at all. A question about a file the user has just sent is answered
    # from that file; searching as well filled the thinking panel with
    # unrelated queries and blended a general page into the answer.
    _step = lambda kind, label, detail: wire.step(  # noqa: E731
        kind, label, request_id=rid, detail=detail)

    # A follow-up is resolved against the thread before anything decides
    # what it is about: "and in Paris?" names no subject on its own.
    understood = research.understand(ctx.text, history=ctx.history)
    subject = understood.standalone or ctx.text
    # A follow-up that the rewrite resolved to him ("tell me more about him"
    # → "... Kabirr Kohli") is a question about him too.
    if not owner_asked and not doc_ids and not images and subject != ctx.text:
        owner_asked = owner.asks(subject)
    # Named anywhere in the question, however it is worded, he comes with
    # what SYRIX knows about him — and research.prepare will not search him.
    if not owner_asked and not doc_ids and not images and not owner.wants_web(ctx.text):
        owner_asked = owner.mentions(ctx.text) or owner.mentions(subject)
    if understood.used_model:
        _step(wire.KIND_PLAN, "Reading it in context", subject[:160])

    # A named place to go — a website, a profile — is visited rather than
    # researched. Its window is a floor rather than the mode's usual share:
    # finding the site, opening its homepage and following one link is three
    # dependent fetches, and Spark's four seconds was measured running out on
    # the third, which is the page that was asked for.
    where = research.destination(ctx.text, subject)
    if where:
        from dataclasses import replace as _replace
        plan = _replace(plan, prefetch_s=max(plan.prefetch_s, 7.0),
                        deadline_s=(max(plan.deadline_s, 15.0) if plan.deadline_s else 0.0))
        _step(wire.KIND_PLAN, f"Going to {where.label}",
              "a named destination, opened directly rather than searched for")

    # Questions with one exact answer at a known source — the weather, a
    # share price, an exchange rate, the time somewhere — are read from that
    # source. When the reading is the whole answer it is taken first and the
    # web is not searched at all; when the question also wants context ("how
    # is NVDA doing") it runs beside the search.
    ask = None if (doc_ids or images or where) else live.detect(subject)
    live_future = _LIVE_POOL.submit(live.fetch, ask) if ask is not None else None
    if live_future is not None:
        # The card is shown the moment its reading lands, not with the
        # finished answer: a quote is in within half a second and the answer
        # takes several more.
        live_future.add_done_callback(lambda f: _send_card_early(f, rid))
    live_fact = _collect(live_future) if (ask is not None and ask.complete) else None
    doc_share, web_share = attachments.budget(
        research.pack_chars(plan), with_web=False)
    reading = attachments.read(
        doc_ids or [], ctx.text,
        request_id=rid, max_chars=doc_share, on_step=_step,
    ) if doc_ids else attachments.Reading()

    need, evidence = research.prepare(
        ctx.text, plan,
        request_id=rid,
        deadline_s=plan.prefetch_s,
        history=ctx.history,
        has_attachment=bool(doc_ids or images),
        on_step=_step,
        rewritten=understood,
        skip_web=bool(live_fact and live_fact.complete) or owner_asked,
        where=where,
    )
    if live_fact is None and live_future is not None:
        live_fact = _collect(live_future)
    if need.reason == research.PROBLEM_REASON:
        # Worked, not looked up: think at full effort, with room in the reply
        # allowance for the thinking — a reasoning model's hidden working is
        # charged against max_tokens, and at 1,600 a hard problem could spend
        # it all before writing the answer.
        from dataclasses import replace as _replace
        plan = _replace(plan, reasoning="high", max_tokens=max(plan.max_tokens, 4096))
        _step(wire.KIND_PLAN, "Working it out", "a problem to reason through, not look up")
        computed = compute.solve(subject)
        if computed.ok:
            computed_block = computed.as_evidence()
            _step(wire.KIND_TOOL_CALL, "Ran a program to compute it",
                  f"{computed.output.splitlines()[-1][:120]} · {computed.took_ms:.0f}ms")
        else:
            computed_block = ""
            _step(wire.KIND_INFO, "Reasoning it through", computed.problem[:120])
    else:
        computed_block = ""
    if live_fact is not None:
        _step(wire.KIND_TOOL_CALL, live_fact.label,
              f"{live_fact.took_ms:.0f}ms · {live_fact.sources[0].get('title', '')}")
    if evidence and reading:
        # Both arrived, so the budget is split rather than each assuming it
        # has the whole prompt to itself.
        doc_share, web_share = attachments.budget(
            research.pack_chars(plan), with_web=True)
    web_block = (
        research.build(evidence, max_chars=web_share or research.pack_chars(plan),
                       avoid=evidence.excluded)
        if evidence else ""
    )
    live_block = f"{_LIVE_HEADER}\n{live_fact.block}" if live_fact else ""
    evidence_block = "\n\n".join(
        part for part in (live_block, computed_block, reading.block[:doc_share], web_block)
        if part)

    # A question about SYRIX gets the runtime's own facts, for the same reason
    # an attachment gets read first: a model left to answer from its priors
    # about what "an AI" can do reports that it has no internet access while
    # the web tool sits registered and working.
    # Nor may the model search for itself while answering one. Asked "tell me
    # more about yourself", it searched for SYRIX mid-answer and cited
    # syrix.io and other unrelated products; nothing on the open web is about
    # this build. Every other tool stays, including its own runtime and source.
    turn_tools = tool_names()
    if need.reason == research.SELF_REASON:
        turn_tools = [t for t in turn_tools if t not in _WEB_TOOLS]
        try:
            evidence_block = "\n\n".join(
                part for part in (status.capability_block(), evidence_block)
                if part)
            _step(wire.KIND_BRAIN_ROUTE, "Checking what I can actually do",
                  "read from the running system")
        except Exception as exc:
            syrix_obs.report("v3.turn.handle", exc, level="debug")

    # Zenith only: a question carrying several independent investigations is
    # worked in parallel and assembled from condensed findings, rather than
    # one prompt holding everything each part read.
    # Pictures for the answer are chosen and fetched while the rest of the
    # turn runs, so they are started before any sub-agent is.
    social_turn = need.reason == research.SOCIAL_REASON
    # A rework of the last answer ("make it shorter") gets no suggestions
    # either: measured, its chips offered to shorten it even more.
    # Nor does a creator question: pictures and chips chosen for "who made
    # you" were about AI companies.
    quiet = social_turn or bool(creator_lang) or owner_asked or (
        need.reason == research.THREAD_REASON and research.reworks(ctx.text))
    pending_media = (media.Pending(question=subject, voice=ctx.voice, quiet=True)
                     if quiet else media.begin(
                         subject, evidence, has_card=live_fact is not None,
                         has_attachment=bool(doc_ids or images), voice=ctx.voice,
                         request_id=rid))

    # A failure in that machinery costs the parallel parts, not the turn: the
    # question is still answered from the evidence already gathered.
    findings = []
    found_block = ""
    try:
        parts = subagents.split(ctx.text, budget=plan)
        if parts:
            findings = subagents.investigate_all(parts, plan, request_id=rid)
            found_block = subagents.as_evidence(findings)
    except Exception as exc:
        findings = []
        syrix_obs.report("v3.turn.subagents", exc, level="warning", rid=rid)
    if findings:
        if found_block:
            evidence_block = (
                "\n\n".join([evidence_block, found_block]) if evidence_block
                else found_block
            )

    # A stated form — one word, a table, bullet points, a length — goes last,
    # right before the question, where it is least likely to be overridden.
    asked_form = form.brief(ctx.text)
    if social_turn:
        # Thanks, praise, hello, goodbye. Said to the model in so many words,
        # because given only the persona a lane answered "thx bro apprecate
        # it" with a usage note on the abbreviation.
        asked_form = "\n\n".join(part for part in (asked_form, research.social_note()) if part)
    if creator_lang:
        asked_form = "\n\n".join(part for part in (asked_form, creator.note()) if part)
    if owner_asked:
        asked_form = "\n\n".join(part for part in (owner.note(), asked_form) if part)
    if asked_form:
        evidence_block = (evidence_block + "\n\n" + asked_form) if evidence_block else asked_form

    system = persona.system_prompt(
        plan,
        tool_names=turn_tools,
        memory_block=ctx.memory_block,
        voice_turn=ctx.voice,
    )
    messages = context.messages(ctx, system, evidence_block=evidence_block)

    # The addresses this turn may open: what the user wrote, now or earlier in
    # the thread, the site they named, and whatever the gathering found. An
    # address the model composes itself is refused by the read tool.
    page_store.admit_text(rid, ctx.text)
    for past in ctx.history:
        if past.get("role") == "user":
            page_store.admit_text(rid, past.get("content", ""))
    if evidence:
        page_store.admit(rid, [s.url for s in evidence.sources])
        # In the order the prompt numbers them, so "page 2" can mean [2].
        page_store.number_evidence(rid, [row["url"] for row in research.citations(evidence)])
        page_store.admit(rid, [url for _, url in evidence.site_sections])
        if evidence.site:
            page_store.admit(rid, [f"https://{evidence.site}/"], whole_site=True)

    try:
        result = loop.run(
            messages,
            budget=plan,
            request_id=rid,
            question=ctx.text,
            session_id=ctx.session_id,
            tool_names=turn_tools,
            started_at=started,
            # Web evidence only. An answer built from the user's own attachment is
            # already attributed — it says which file and which page — and
            # counting that as "sources were gathered" made the attribution
            # check demand a publisher for a figure that came out of the user's PDF,
            # costing a repair round on every document turn.
            # Nor for a site or profile the user sent SYRIX to: the source is
            # the subject, named in the question. Measured on a GitHub profile,
            # the rule bought a repair round that only added "[1]" to each line.
            had_evidence=bool(web_block) and not _destination_only(evidence),
            # A creator answer can be replaced after it is written, so it is
            # not shown until then.
            stream=not creator_lang,
        )
    finally:
        look_tool.release(rid)
        page_store.release(rid)

    if not result.text and creator_lang:
        # The answer is known without a lane.
        result.text = creator.reply(creator_lang)
    if not result.text:
        # The notes name lanes and providers; they go to diagnostics (the
        # failure reason below), not onto the screen.
        wire.step(wire.KIND_ERROR, "No answer produced", request_id=rid)
        return wire.failure(
            _no_answer_message(result.notes),
            request_id=rid,
            mode=mode,
            reason="; ".join(result.notes) or "no_answer",
        )

    answer_text, removed = polish.clean(result.text)
    answer_text, corrected = creator.enforce(ctx.text, answer_text, creator_lang)
    if corrected:
        removed = list(removed) + [corrected]
    speak_text = polish.for_speech(answer_text) if ctx.voice else answer_text

    context.append_turn(ctx.session_id, "user", ctx.text)
    context.append_turn(ctx.session_id, "assistant", answer_text)
    # Fold the older turns into a summary once the thread outgrows the prompt.
    # After the answer, so it costs this turn nothing, and only on the turn that
    # crosses the threshold. Without it the transport drops the oldest turns to
    # fit — which is how a pasted letter vanished and SYRIX reported never
    # having seen it.
    context.compact_if_needed(ctx.session_id)

    elapsed_ms = round((time.monotonic() - started) * 1000.0, 1)
    wire.step(wire.KIND_DONE, "Done", request_id=rid,
              detail=f"{elapsed_ms:.0f}ms · {result.model_calls} model calls",
              provider=result.provider)
    # Refresh what the interface shows about the runtime. Lane budgets move
    # with every turn, so a status read between turns is the only one that
    # is true when it is read.
    status.publish()

    # Prefetched evidence is cited alongside anything the model went and
    # fetched itself, prefetch first because it is what the answer was
    # written against.
    # Attachment passages are cited first: they are what the user handed over, and
    # a page reference into the user's own file is the most checkable citation there
    # is.
    # Web sources lead and carry the numbers the answer cites them by, so a
    # "[2]" in the text opens the second source in the list under it.
    cited = ((research.citations(evidence) if evidence else [])
             + (list(live_fact.sources) if live_fact else [])
             + list(reading.citations)
             + subagents.all_sources(findings) + result.sources)
    if creator_lang:
        # The creator is not sourced from anywhere; a page the model opened
        # while answering would sit under the answer as if it were.
        cited = []

    # The card is built from the same live data the answer stands on, and is
    # decoration on top of it rather than a substitute for it.
    card = (dict(live_fact.card) if live_fact
            else panels.build(ctx.text, answer_text, sources=cited))
    extras = media.finish(pending_media, answer_text)
    card = media.attach(card, extras)

    return wire.answer(
        answer_text,
        request_id=rid,
        mode=mode,
        provider=result.provider,
        model=result.model,
        speak_text=speak_text,
        found=cited,
        follow_ups=extras.follow_ups,
        panel=card,
        diagnostics={
            "polished": removed,
            "mode_asked": asked,
            "effort": plan.effort,
            "routed_because": routed.why(),
            "elapsed_ms": elapsed_ms,
            "rounds": result.rounds,
            "model_calls": result.model_calls,
            "tool_calls": result.tool_calls,
            "deadline_hit": result.deadline_hit,
            "repaired": result.repaired,
            "sub_agents": [f.question for f in findings],
            "attachments": {
                "read": reading.names,
                "chars": len(reading.block),
                "problems": reading.problems,
                "took_ms": reading.took_ms,
            },
            "prefetch": {
                "wanted": need.wanted,
                "reason": need.reason,
                "queries": need.queries,
                "found": len(evidence.sources) if evidence else 0,
                "opened": len(evidence.opened) if evidence else 0,
                "searched_ms": evidence.searched_ms if evidence else 0,
                "read_ms": evidence.read_ms if evidence else 0,
            },
            "notes": result.notes,
        },
    )
