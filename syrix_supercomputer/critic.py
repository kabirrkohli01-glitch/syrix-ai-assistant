"""A second brain reads the answer before the user does.

Why a separate model at all
---------------------------
:func:`syrix_supercomputer.loop._verify` already catches the *mechanical*
failures — an empty answer, a file claimed but never written, figures stated
with no page read, a give-up that was not earned. Those are cheap regex and
bookkeeping checks and they work.

What they cannot catch is the semantic failure: an answer that is fluent,
grounded and about **the wrong question**. Asked for the five biggest movers and
a tradeable setup, a model will happily return five company descriptions and
stop. Nothing in `_verify` fires, because every individual check passes.

A model is a poor judge of its own omission — it re-reads its answer and sees
what it meant to say. A fresh reader, given the goal and the answer and nothing
else, catches it immediately. Since the brain pool rotates providers on every
call, this review usually lands on a genuinely different model, which is the
cheap version of "ask someone else to look at it".

Why it can never cost a task
----------------------------
This runs *after* the answer exists. Everything about it is therefore biased
towards accepting:

* one pushback per task, tracked by the caller — a critic that can block twice
  is a critic that can loop;
* it is skipped entirely in the closing-margin steps, where the loop has already
  decided an imperfect answer beats none;
* every failure path — no model, empty reply, unparseable verdict, exception —
  returns ``accept``. The answer was already good enough to reach here.

It is also the wrong tool for judging truth. It never sees the pages, so it is
asked only about coverage and attribution: does this answer the question, and is
anything asserted that the run has no business asserting. Truth is `_verify`'s
grounding check and the escalation ladder's job.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import syrix_obs as obs

from . import models

# The review is a judgement on ~2 paragraphs, not a rewrite. A tight budget
# keeps it off the critical path — this sits between the agent finishing and
# the user seeing the answer, so every second is felt.
_MAX_TOKENS = 320
_TIMEOUT_S = 20.0
# Long answers are judged on their opening and closing; the middle of a 6k-char
# report is rarely where "you answered the wrong question" hides.
_ANSWER_HEAD = 2600
_ANSWER_TAIL = 900

_PROMPT = """You are reviewing another assistant's finished answer before the user sees it.

THE USER ASKED FOR:
{goal}

THE ANSWER GIVEN:
{answer}

{evidence}

Judge only two things:
1. COVERAGE — does this actually answer what was asked? Every part of it? If the \
request had several parts (analyse AND make a file AND compare), all of them?
2. ATTRIBUTION — does it state specific facts (prices, dates, figures, names) that \
the pages listed above could not have supplied?

Do NOT judge writing style, length, or whether you would have said it differently. \
Do NOT ask for more detail on something already answered. An imperfect answer that \
covers the question is ACCEPT.

Reply with one JSON object and nothing else:
{{"verdict": "accept"}}
or
{{"verdict": "revise", "problem": "one sentence naming exactly what is missing or unsupported"}}"""

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class Verdict:
    accept: bool
    pushback: str = ""
    provider: str = ""


def enabled(config: Optional[Dict[str, Any]] = None) -> bool:
    return bool((config or {}).get("supercomputer_critic_enabled", True))


def _clip_answer(answer: str) -> str:
    body = str(answer or "").strip()
    if len(body) <= _ANSWER_HEAD + _ANSWER_TAIL:
        return body
    return f"{body[:_ANSWER_HEAD]}\n\n[…middle omitted…]\n\n{body[-_ANSWER_TAIL:]}"


def _evidence_block(ctx: Any) -> str:
    """The sources the run actually read, so attribution can be judged."""
    urls: List[str] = []
    with obs.swallow("supercomputer.critic.evidence"):
        from . import memory, results

        task_id = str(getattr(ctx, "task_id", "") or "")
        mem = memory.get(task_id)
        if mem is not None:
            urls = list(mem.pages_read)
        if not urls:
            urls = [r["location"] for r in results.for_task(task_id)
                    if r["kind"] == "source" and r.get("location")]
    if not urls:
        return "PAGES THE ASSISTANT READ: none — it read no source at all."
    return "PAGES THE ASSISTANT READ:\n" + "\n".join(f"  - {u}" for u in urls[:12])


def _parse(reply: str) -> Optional[Dict[str, Any]]:
    body = str(reply or "").strip()
    if not body:
        return None
    body = re.sub(r"^```(?:json)?\s*", "", body)
    body = re.sub(r"\s*```$", "", body).strip()
    try:
        payload = json.loads(body)
    except Exception:
        match = _JSON_RE.search(body)
        if not match:
            return None
        try:
            payload = json.loads(match.group(0))
        except Exception as exc:
            obs.report("supercomputer.critic.parse", exc, level="debug")
            return None
    return payload if isinstance(payload, dict) else None


def review(goal: str, answer: str, *, ctx: Any = None, request_id: str = "",
           config: Optional[Dict[str, Any]] = None) -> Verdict:
    """Review one finished answer. Never raises; defaults to accepting."""
    cfg = config or {}
    if not enabled(cfg):
        return Verdict(True)
    body = str(answer or "").strip()
    if len(body) < 40:
        # Too short to have a coverage problem worth a whole model call; the
        # `empty` check in _verify already owns this case.
        return Verdict(True)

    prompt = _PROMPT.format(
        goal=str(goal or "")[:1200],
        answer=_clip_answer(body),
        evidence=_evidence_block(ctx),
    )
    try:
        reply, provider = models.chat(
            models.ROLE_PLAN,
            [{"role": "user", "content": prompt}],
            config=cfg, request_id=request_id,
            max_tokens=_MAX_TOKENS, timeout_s=_TIMEOUT_S,
            grade=models.GRADE_HARD, purpose="critic",
            routing_text=goal,
        )
    except Exception as exc:
        obs.report("supercomputer.critic.chat", exc, level="debug")
        return Verdict(True)

    payload = _parse(reply)
    if payload is None:
        # A critic that cannot state a verdict has no verdict. Accept.
        return Verdict(True, provider=provider)

    verdict = str(payload.get("verdict") or "").strip().lower()
    if verdict != "revise":
        return Verdict(True, provider=provider)

    problem = str(payload.get("problem") or payload.get("reason") or "").strip()
    if not problem:
        return Verdict(True, provider=provider)

    return Verdict(
        False,
        pushback=(
            f"A second review of your answer says: {problem[:400]}\n\n"
            f"Fix exactly that and call finish again with the full answer rewritten. "
            f"If the review is wrong — you did cover it, or the fact does come from a "
            f"page you read — call finish again unchanged and say so in one line. Do "
            f"not start new research for this."
        ),
        provider=provider,
    )


__all__ = ["Verdict", "review", "enabled"]
