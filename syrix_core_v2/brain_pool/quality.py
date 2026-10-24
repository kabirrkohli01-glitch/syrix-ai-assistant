"""Quality layer — the answer-quality reasoning pipeline.

Phase 1:   context budgeting + one persona across every lane.
Phase 1.5: response blueprints (the answer is PLANNED before it's written)
           and the self-review pass (a fast model critiques the draft and
           repairs vague/incomplete answers before the user sees them).
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional, Tuple


# One persona block shared by every provider so SYRIX never drifts when the
# pool rotates lanes mid-conversation.
PERSONA_BLOCK = (
    "You are SYRIX, Kabirr's personal AI assistant. Be direct, complete and"
    " practical — answer the actual question fully, and match depth to the"
    " question: brief for a single fact, thorough for anything involving"
    " reasoning. SHOW YOUR WORKING on maths/calculations/quantitative answers"
    " (steps first, then the final result) and give the mechanism on 'how/why'"
    " questions — never a bare result with no path to it. Confident but never"
    " arrogant; if you are unsure, say so plainly instead of guessing. Never"
    " mention which model or provider you run on. Plain conversational text"
    " unless code, a list, or worked steps make the answer clearer."
)


def _content_chars(m: Dict[str, Any]) -> int:
    c = m.get("content")
    if isinstance(c, str):
        return len(c)
    if isinstance(c, list):
        return sum(len(str(b.get("text") or "")) for b in c if isinstance(b, dict))
    return 0


def budget_messages(
    messages: List[Dict[str, Any]],
    *,
    max_prompt_chars: int = 18000,
) -> List[Dict[str, Any]]:
    """Trim history to fit the smallest free-tier TPM budget.

    Keeps every system message and the final user turn untouched; drops the
    OLDEST intermediate turns first. 18k chars ≈ 5k tokens — safely inside a
    6k-TPM fast lane alongside the reply budget.
    """
    msgs = list(messages or [])
    total = sum(_content_chars(m) for m in msgs)
    if total <= max_prompt_chars:
        return msgs

    system = [m for m in msgs if m.get("role") == "system"]
    rest = [m for m in msgs if m.get("role") != "system"]
    keep_tail: List[Dict[str, Any]] = []
    budget = max_prompt_chars - sum(_content_chars(m) for m in system)
    # Walk backwards keeping the most recent turns that fit.
    for m in reversed(rest):
        cost = _content_chars(m)
        if budget - cost < 0 and keep_tail:
            break
        budget -= cost
        keep_tail.append(m)
    keep_tail.reverse()
    out = system + keep_tail
    return out if out else msgs[-2:]


def ensure_persona(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Prepend the persona block when no system message carries it yet."""
    msgs = list(messages or [])
    for m in msgs:
        if m.get("role") == "system":
            return msgs  # caller supplied a system prompt — respect it
    return [{"role": "system", "content": PERSONA_BLOCK}] + msgs


# ────────────────────────────────────────────────────────────────────────────
# Phase 1.5 — Response blueprints: plan the answer's shape before writing it.
# Injected into the system prompt for the matching intent; casual chat gets
# NO structure on purpose (structured small talk reads like a press release).
# ────────────────────────────────────────────────────────────────────────────

_BLUEPRINTS: Dict[str, str] = {
    "trade_analysis": (
        "Response blueprint — cover, in this order, briefly per section:\n"
        "1. Read (one-line direction + conviction)\n"
        "2. Fundamentals that matter right now\n"
        "3. Technicals (trend, key levels)\n"
        "4. Sentiment/flows\n"
        "5. Bull case vs Bear case (2 lines each)\n"
        "6. Invalidation — what proves this read wrong\n"
        "Never a naked Buy/Sell; state data limits honestly."
    ),
    "educational_explanation": (
        "Response blueprint: lead with the core idea in one plain sentence,"
        " then build up with a concrete example, then the one nuance people"
        " usually get wrong. No filler, no 'great question'."
    ),
    "planning_advice": (
        "Response blueprint: 1) the recommendation up front, 2) the 2-4 steps"
        " to execute it, 3) the main risk or trade-off, 4) what you'd do"
        " instead if the user's constraint changes."
    ),
    "live_research": (
        "Response blueprint: state the verified finding first, cite which"
        " source supports each claim, surface any disagreement between"
        " sources explicitly, end with a confidence note (solid / mixed /"
        " thin evidence)."
    ),
    "general_knowledge": (
        "Response blueprint: lead with the direct answer in the FIRST"
        " sentence, then give the supporting detail the answer actually needs"
        " — the why, the how, a concrete example, or the numbers behind it."
        " Keep it tight for a single fact; go deeper when the question carries"
        " reasoning. If the question is ambiguous, answer the most likely"
        " reading and note the alternative in one clause."
    ),
}

_CODING_BLUEPRINT = (
    "Response blueprint (code question): 1) the working code first,"
    " 2) one line on why/how it works, 3) the edge case or pitfall that"
    " bites people. Match the user's language/version. No boilerplate"
    " disclaimers."
)

_MATH_BLUEPRINT = (
    "Response blueprint (maths / quantitative question): SHOW THE WORKING."
    " 1) restate the setup and any formula you'll use, 2) work through the"
    " steps in order, showing each intermediate value, 3) state the final"
    " answer clearly on its own line (or in bold). Never give just the result"
    " with no derivation — the working IS the answer here."
)

_CODE_HINT_RE = re.compile(
    r"\b(code|python|javascript|typescript|c\+\+|rust|golang|sql|regex|"
    r"function|script|one-?liner|debug|error|traceback|compile|refactor)\b",
    re.IGNORECASE,
)

# Quantitative / worked-answer questions. Matches explicit maths verbs,
# operators, and the common quantitative domains where a bare result is a bad
# answer. Kept ahead of the generic blueprints so "calculate the position
# size" or "what is 17% of 240" always gets the show-your-working shape.
_MATH_HINT_RE = re.compile(
    r"\b(calculate|compute|solve|evaluate|derivative|integral|integrate|"
    r"differentiate|factor(?:ise|ize)?|simplify|equation|probability|"
    r"percentage|percent|compound|interest|how\s+much\s+is|"
    r"what\s+is\s+\d|sum\s+of|product\s+of|square\s+root|"
    r"logarithm|sine|cosine|standard\s+deviation|"
    r"position\s+siz|risk\s+per\s+trade)\b"
    r"|\d\s*[\+\-\*/\^×÷]\s*\d"
    r"|\b\d+\s*%",
    re.IGNORECASE,
)


def blueprint_for(intent: str, user_text: str) -> str:
    """The structure block for this intent, or "" (casual stays casual)."""
    it = str(intent or "").strip().lower()
    if it in ("chitchat", "greeting", "farewell", "identity"):
        return ""
    txt = str(user_text or "")
    # Quantitative questions get the show-your-working shape on ANY intent
    # (general_knowledge, educational, even planning) — a bare number is the
    # exact failure mode we're fixing.
    if _MATH_HINT_RE.search(txt):
        return _MATH_BLUEPRINT
    if _CODE_HINT_RE.search(txt):
        return _CODING_BLUEPRINT
    return _BLUEPRINTS.get(it, "")


# ────────────────────────────────────────────────────────────────────────────
# Phase 1.5 — Self-review: a fast lane critiques the draft and repairs it.
# One extra T1 call (~300 ms on Groq-8B / Cerebras) only for substantive
# answers — chitchat and short replies skip entirely.
# ────────────────────────────────────────────────────────────────────────────

_REVIEW_INTENTS = frozenset(
    {
        "general_knowledge",
        "educational_explanation",
        "planning_advice",
        "live_research",
        "live_general",
        "trade_analysis",
    }
)
_REVIEW_MIN_CHARS = 240
_REVIEW_PROMPT = (
    "You are reviewing a draft answer before it is sent.\n"
    "User asked: {question}\n\nDraft answer:\n{draft}\n\n"
    "If the draft directly and completely answers the user's actual question"
    " with no vagueness, reply with exactly: OK\n"
    "Otherwise reply with ONLY the corrected/completed answer (no preamble,"
    " no commentary about the draft). Keep the original's correct content and"
    " style; fix what's missing, vague, or off-topic."
)


def self_review(
    reply: str,
    user_text: str,
    intent: str,
    *,
    max_tokens: int = 900,
) -> Tuple[str, bool, int]:
    """Return (final_reply, improved, elapsed_ms). Never raises; on any
    problem the original reply comes back untouched."""
    t0 = time.perf_counter()
    try:
        draft = str(reply or "").strip()
        if len(draft) < _REVIEW_MIN_CHARS:
            return reply, False, 0
        if str(intent or "").strip().lower() not in _REVIEW_INTENTS:
            return reply, False, 0
        from . import chat as _pool_chat  # lazy: package __init__

        res = _pool_chat(
            "T1_FAST",
            [
                {
                    "role": "user",
                    "content": _REVIEW_PROMPT.format(
                        question=str(user_text or "")[:600], draft=draft[:4000]
                    ),
                }
            ],
            max_tokens=max_tokens,
            use_cache=False,
            # The review is an enhancement, never a bottleneck: if only slow
            # lanes are healthy right now, give up and ship the draft.
            timeout_s=8.0,
        )
        verdict = (res.text or "").strip()
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        if not verdict or verdict.upper().rstrip(".!") == "OK" or (
            len(verdict) < 12 and "ok" in verdict.lower()
        ):
            return reply, False, elapsed_ms
        # Sanity: a "revision" that lost most of the content is a downgrade.
        if len(verdict) < 0.4 * len(draft):
            return reply, False, elapsed_ms
        return verdict, True, elapsed_ms
    except Exception:
        return reply, False, int((time.perf_counter() - t0) * 1000)
