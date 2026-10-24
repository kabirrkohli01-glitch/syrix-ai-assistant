"""The agent loop. One of them, for every mode.

Rounds go: ask the lane, run whatever tools it asked for, hand the results
back, ask again. It ends when the lane stops asking for tools, when the
round budget is spent, or when the deadline passes.

The rules that make this different from what it replaces:

**Tools are native.** ``tools`` goes out on the request and ``tool_calls``
comes back on the response. The old loop asked the model to write
``<action>{"tool": ...}</action>`` into its prose and regexed it back out,
which is why tool markup reached the screen and why whole rounds were spent
retrying malformed tags.

**A deadline never produces an apology.** When time runs out the loop stops
gathering and asks for the answer from what it already has. A partial answer
that says what is missing beats "that took longer than I'm willing to wait",
which is what the measured baseline shipped for a question as simple as the
capital of France after sixty seconds.

**One mode, one turn.** Nothing here starts a different mode's work, and
nothing runs a second engine alongside the first to see which finishes.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from . import check, gateway, polish, wire
from .budget import Budget
from .tools import ToolContext, run_round, schemas
from .tools import names as _registered

# When the lane answers with neither text nor a tool call there is nothing to
# continue from; one more round with tools withdrawn asks it plainly.
_EMPTY_REPLY_RETRIES = 1

# No model call is given less than this, whatever the deadline says. The
# deadline bounds how much gathering a turn does; it must never cut off the
# call that is writing the answer. At four seconds, a Spark turn that spent
# its window searching left the answer call too little time to wait out a
# busy lane's minute, and the turn reported every lane at its limit while two
# of them were healthy — measured on the sixth question of a paced run.
_MIN_CALL_SECONDS = 25.0


@dataclass
class LoopResult:
    text: str = ""
    sources: List[Dict[str, Any]] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    rounds: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    deadline_hit: bool = False
    tools_withdrawn: bool = False
    repaired: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


# Below this share of the original length, a rewrite has not been corrected —
# it has been gutted.
_DEGENERATE_SHARE = 0.45
# A citation list masquerading as an answer: a "Sources" heading with almost no
# prose above it. This is what a weak lane produced when told to attribute its
# figures.
_JUST_CITATIONS = re.compile(
    r"\A\s*(?:\*\*|#+\s*)?sources?\b\s*[:\-]?", re.I)


def _is_degenerate(rewrite: str, original: str) -> bool:
    """Has the rewrite thrown the answer away instead of fixing it?"""
    new, old = rewrite.strip(), original.strip()
    if not new:
        return True
    if _JUST_CITATIONS.match(new) and not _JUST_CITATIONS.match(old):
        return True
    return len(old) >= 400 and len(new) < len(old) * _DEGENERATE_SHARE


class _Streamer:
    """Forwards the answer to the chat as it is written — once per turn.

    The first forty characters are held back, because a round that ends in
    a tool call sometimes opens with a line of preamble, and a preamble
    streamed to the screen and then abandoned reads as a false start. Only
    the first round that writes an answer streams: a repair rewrite replaces
    the text when the turn ends rather than appending to what was shown.
    """

    _HOLD = 40

    def __init__(self, request_id: str):
        self.request_id = request_id
        self.held = ""
        self.live = False
        self.finished = False
        self.shown = 0

    def __call__(self, piece: str) -> None:
        if self.finished or not piece:
            return
        if self.live:
            wire.stream(piece, request_id=self.request_id)
            self.shown += len(piece)
            return
        self.held += piece
        if len(self.held.strip()) >= self._HOLD:
            self.live = True
            wire.stream(self.held, request_id=self.request_id)
            self.shown += len(self.held)
            self.held = ""

    def round_over(self, *, wrote_answer: bool) -> None:
        """Close streaming after the first answer, whatever it contained."""
        if self.finished:
            return
        if wrote_answer:
            if self.held and not self.live:
                wire.stream(self.held, request_id=self.request_id)
            self.finished = True
        elif self.live:
            # Text went out and then the round asked for a tool. Nothing more
            # is appended to it; the finished answer replaces it at the end.
            self.finished = True
        self.held = ""


def _final_instruction(reason: str) -> Dict[str, str]:
    return {
        "role": "system",
        "content": (
            f"{reason} Write the answer now from what you already have. "
            "Say plainly what you could not check and answer the rest — do "
            "not apologise, and do not offer to try again instead of "
            "answering."
        ),
    }


def run(
    messages: List[Dict[str, Any]],
    *,
    budget: Budget,
    request_id: str,
    question: str = "",
    session_id: str = "default",
    tool_names: Optional[Sequence[str]] = None,
    started_at: Optional[float] = None,
    had_evidence: bool = False,
    stream: bool = True,
) -> LoopResult:
    """Drive one turn to an answer.

    ``stream=False`` shows nothing until the answer is final, for a turn
    whose answer may be corrected after the model has written it.
    """
    start = started_at if started_at is not None else time.monotonic()
    deadline_at = (start + budget.deadline_s) if budget.deadline_s else 0.0
    # Tools stop early so the call that writes the answer still has room.
    gather_until = (
        max(start, deadline_at - budget.answer_reserve_s) if deadline_at else 0.0
    )

    tctx = ToolContext(
        request_id=request_id,
        budget=budget,
        session_id=session_id,
        question=question,
        deadline_at=gather_until,
    )
    tool_schemas = schemas(tool_names)
    convo: List[Dict[str, Any]] = list(messages)
    out = LoopResult()
    streamer = _Streamer(request_id)
    streamer.finished = not stream
    empty_retries = 0
    # Whatever the calculator returned this turn, so the check can tell an
    # answer that used the figures from one that asked for them and then wrote
    # its own.
    computed: List[str] = []
    # The answer as first written, so a repair that makes things worse can be
    # thrown away rather than shipped.
    before = ""

    def _time_left() -> float:
        """Against the turn's own deadline, not the shorter gathering one."""
        if not deadline_at:
            return float("inf")
        return max(0.0, deadline_at - time.monotonic())

    def _gathering_over() -> bool:
        return bool(gather_until) and tctx.remaining() <= 0.25

    def _call_timeout() -> Optional[float]:
        """How long one model call may take.

        The deadline decides whether to do MORE work, not whether the work in
        flight may finish. Handing a call the remaining turn budget means a
        five-second turn can never make a call that legitimately takes four
        and a half — the pool reads it as a network failure, rotates through
        every lane on the same shrinking clock, and reports the tier as
        exhausted. A cold TLS handshake on the first call of a process is
        enough to trigger it.
        """
        if not deadline_at:
            return None
        return max(_MIN_CALL_SECONDS, _time_left() + 1.0)

    def _step(kind: str, label: str, detail: str = "", provider: str = "") -> None:
        wire.step(kind, label, request_id=request_id, detail=detail, provider=provider)

    for round_index in range(1, budget.max_rounds + 1):
        out.rounds = round_index
        last_round = round_index >= budget.max_rounds
        expired = _gathering_over()

        if expired and not out.deadline_hit:
            out.deadline_hit = True
            convo.append(_final_instruction("The gathering time for this turn is up."))
            _step(wire.KIND_INFO, "Time is up — answering from what arrived")
        elif last_round and tool_schemas:
            convo.append(_final_instruction("This is the last round."))

        offer_tools = bool(tool_schemas) and not last_round and not out.deadline_hit
        if not offer_tools:
            out.tools_withdrawn = True

        # The progress panel says what is happening, not which model is doing
        # it: which lane answered, and which refused first, belongs in the
        # per-answer Inspect view, not in front of every answer.
        if round_index == 1:
            _step(wire.KIND_BRAIN_ROUTE, "Reasoning")
        else:
            _step(wire.KIND_SYNTHESIS, "Working it through")

        try:
            completion = gateway.complete(
                convo,
                tier=budget.tier,
                tools=tool_schemas if offer_tools else None,
                max_tokens=budget.max_tokens,
                temperature=budget.temperature,
                prompt_chars=budget.prompt_chars,
                timeout_s=_call_timeout(),
                reasoning=budget.reasoning or ("medium" if budget.effort == "hard" else ""),
                on_delta=None if streamer.finished else streamer,
            )
        except gateway.NoLaneAvailable as exc:
            out.notes.append(f"no lane available: {exc}")
            _step(wire.KIND_ERROR, "No model lane available", detail=str(exc)[:160])
            break

        out.model_calls += 1
        out.provider = completion.provider or out.provider
        out.model = completion.model or out.model

        streamer.round_over(wrote_answer=bool((completion.text or "").strip())
                            and not (completion.tool_calls and offer_tools))
        if completion.tool_calls and offer_tools:
            out.tool_calls += len(completion.tool_calls)
            convo.append({
                "role": "assistant",
                "content": completion.text or "",
                "tool_calls": [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {"name": call["name"],
                                     "arguments": call.get("arguments") or "{}"},
                    }
                    for call in completion.tool_calls
                ],
            })
            # Only tools offered this turn run. A model can still name one it
            # was not given (it knows "search" exists from other turns); on a
            # question about SYRIX that call would reach the web it was kept
            # from. It is answered, as every call must be, but not executed.
            # A name that is no tool at all still goes through, so the
            # runner can say "no tool called …".
            offered = set(tool_names) if tool_names is not None else None
            withheld = (lambda name: offered is not None and name in _registered()
                        and name not in offered)
            calls = [c for c in completion.tool_calls if not withheld(c["name"])]
            for call in completion.tool_calls:
                if withheld(call["name"]):
                    convo.append({
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": "That tool is not available for this question. "
                                   "Answer from what you already have.",
                    })
            results = run_round(
                calls, tctx,
                on_step=lambda kind, label, detail: _step(kind, label, detail),
            ) if calls else []
            for call_id, name, result in results:
                convo.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": result.content[:20000],
                })
                if name == "calculate" and result.ok:
                    computed.append(result.content)
                if result.sources:
                    out.sources.extend(result.sources)
                    wire.sources(result.sources[:12], request_id=request_id)
                if not result.ok:
                    out.notes.append(f"{name}: {result.detail or 'returned nothing'}")
            continue

        text = (completion.text or "").strip()
        if text:
            # Narration about the search is removed in code before the check
            # sees it; a repair round for it cost 13s and did not work.
            text = polish.drop_narration(text)[0]
            out.text = text
            # One deterministic pass comparing the answer to the question.
            # It fires rarely; when it does, the model is told exactly what
            # is missing and writes the answer again. Nothing here edits the
            # text, and it runs once per turn however many rules matched.
            if not out.repaired:
                verdict = check.run(
                    question, text,
                    had_evidence=had_evidence,
                    cited=len(out.sources),
                    tools_used=out.tool_calls,
                    computed="\n".join(computed),
                )
                if not verdict.ok:
                    out.repaired = list(verdict.problems)
                    before = text
                    _step(wire.KIND_INFO, "Checking the answer against the question",
                          detail="; ".join(verdict.problems)[:160])
                    convo.append({"role": "assistant", "content": text})
                    convo.append(check.repair_message(verdict))
                    tool_schemas = []
                    continue
            elif before:
                # A repair is not automatically an improvement, and on a weak
                # lane it sometimes is not one at all. Measured: told to
                # attribute its figures, a lane replaced a full physics
                # explanation with two citation lines and nothing else. The
                # rewrite is kept only when it is still a whole answer.
                if _is_degenerate(text, before):
                    out.notes.append(
                        "the rewrite came back worse than the original; kept "
                        "the original")
                    out.text = before
                    _step(wire.KIND_INFO, "Kept the first answer",
                          detail="the rewrite dropped most of it")
                else:
                    # Length was the only thing guarded, and it is not the only
                    # way a repair goes backwards. A repair note is one more
                    # instruction to agree with, and a small lane agrees:
                    # measured on the local model, notes about a denied
                    # capability produced replies opening "Got it." that then
                    # restated the false premise more firmly than the original
                    # had. A rewrite that still fails the same check is not an
                    # improvement, so the original stands.
                    again = check.run(
                        question, text,
                        had_evidence=had_evidence,
                        cited=len(out.sources),
                        tools_used=out.tool_calls,
                        computed="\n".join(computed),
                    )
                    if not again.ok:
                        out.notes.append(
                            "the rewrite did not clear the check either; kept "
                            "the original")
                        out.text = before
                        _step(wire.KIND_INFO, "Kept the first answer",
                              detail="the rewrite did not fix what was flagged")
            break

        # No text and no tool call. Ask once more with tools withdrawn so the
        # lane has nothing to do but answer.
        if empty_retries < _EMPTY_REPLY_RETRIES:
            empty_retries += 1
            out.notes.append("empty reply; asked again with tools withdrawn")
            convo.append(_final_instruction("That response was empty."))
            tool_schemas = []
            continue
        out.notes.append("the lane returned nothing twice")
        break

    # Deduplicate the evidence list by URL, keeping first-seen order so the
    # citations follow the order the answer was built in.
    seen = set()
    unique: List[Dict[str, Any]] = []
    for item in out.sources:
        url = str((item or {}).get("url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        unique.append(item)
    out.sources = unique
    return out
