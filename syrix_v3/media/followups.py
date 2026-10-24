"""Three questions worth asking next, offered as chips under the answer.

One call on the fast lane, started with the image work as soon as the
evidence is in and written from the question and what the search found, so
it runs while the answer is being written. Started after the answer instead,
the finished line waited for it — up to three seconds on a busy lane, and
measured timing out with nothing to show. Skipped for chit-chat, where "ask
me something else" chips would be noise.
"""
from __future__ import annotations

import json
import re
from typing import List

_PROMPT = """Suggest the three follow-up questions this user is most likely to ask next.

Each must be something the answer did not already cover, answerable by an
assistant with web access, written the way the user would type it, and at
most ten words. No numbering, no quotes around them.

Return a JSON array of three strings and nothing else."""

_PROMPT_FROM_SEARCH = """Suggest the three follow-up questions this user is most likely to ask next,
given their question and what a web search turned up for it.

Each must go beyond what was asked, be answerable by an assistant with web
access, be written the way the user would type it, and be at most ten words.
No numbering, no quotes around them.

Return a JSON array of three strings and nothing else."""

_CHAT = re.compile(r"^\s*(?:hi|hey|hello|yo|thanks|thank you|cheers|ok|okay|cool|nice|lol|bye)\b", re.I)


def wanted(question: str, answer: str) -> bool:
    text = " ".join(str(question or "").split())
    return bool(text) and not _CHAT.match(text) and len(str(answer or "")) >= 120


def wanted_early(question: str) -> bool:
    """Worth starting before the answer exists: anything but chit-chat."""
    text = " ".join(str(question or "").split())
    return bool(text) and not _CHAT.match(text)


def suggest(question: str, answer: str, *, timeout_s: float = 3.0, found: str = "") -> List[str]:
    from .. import gateway  # noqa: WPS433 — imported late, as every caller of the lanes is
    if found and not answer:
        system, body = _PROMPT_FROM_SEARCH, f"Question: {question[:600]}\n\nFound:\n{found[:1800]}"
    else:
        system, body = _PROMPT, f"Question: {question[:600]}\n\nAnswer:\n{answer[:1800]}"
    try:
        got = gateway.complete(
            [{"role": "system", "content": system},
             {"role": "user", "content": body}],
            tier="T1_FAST", max_tokens=220, temperature=0.4, prompt_chars=4000,
            timeout_s=timeout_s, allow_local_fallback=False,
        )
    except Exception:
        return []
    match = re.search(r"\[.*\]", got.text or "", re.S)
    if not match:
        return []
    try:
        rows = json.loads(match.group(0))
    except ValueError:
        return []
    out: List[str] = []
    for row in rows if isinstance(rows, list) else []:
        text = " ".join(str(row or "").split()).strip(" \"'")
        if 4 <= len(text) <= 90 and text.lower() not in (o.lower() for o in out):
            out.append(text)
    return out[:3]
