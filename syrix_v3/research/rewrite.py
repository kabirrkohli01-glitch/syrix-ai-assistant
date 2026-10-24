"""Turning what the user said into what to search for.

Most questions are their own best query and go straight to the lanes. Two
shapes are not, and both were shipped wrong:

**A follow-up.** "news outside ai and tech" searched for exactly that
string, which no index understands, and came back with the AI sections of
TechCrunch, the Guardian and the BBC — the opposite of what was asked, and
plainly so. The constraint lives in the words "outside ai and tech" and the
subject lives in the turn before it; neither survives being handed to a
search box verbatim.

**An exclusion.** "not X", "other than Y", "apart from Z" all mean the
results must NOT be about something, and a search engine reads those words
as topics to match.

So when a question is referential or carries a constraint, one fast model
call rewrites it against the thread into plain queries plus an explicit
exclusion list, and the exclusion is enforced in code afterwards rather
than trusted to the engine. Everything else skips this entirely and pays
nothing.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .. import creator, gateway
from . import social as _social

# The fast lane. This call decides what to look for before anything else can
# start, so it has to cost well under a second. On the answer tier it was
# given three seconds to share with a lane that was often refilling its
# minute, timed out, and fell back to the literal words: "can u find me 2026
# rankings pls", asked straight after a question about UK universities, was
# searched as "2026 rankings" and answered with the World Happiness Report.
_TIER = "T1_FAST"
_BUDGET_S = 3.0
_MAX_QUERIES = 5

# A question that cannot stand on its own.
_REFERENTIAL = re.compile(
    r"^\s*(?:and|but|so|ok|okay|also)?\s*"
    r"(?:what about|how about|and the|any (?:other|more)|more of|"
    r"outside|other than|apart from|besides|instead|except|"
    r"same (?:for|with)|do it for|now do)\b",
    re.I,
)
_PRONOUN_START = re.compile(
    r"^\s*(?:it|its|it's|they|them|that|those|these|this|he|she|him|her|"
    r"the same|another|one more)\b",
    re.I,
)
_EXCLUSION = re.compile(
    r"\b(?:not|outside|other than|apart from|besides|except|excluding|"
    r"without|no)\s+(?:the\s+)?([a-z0-9 &/-]{2,40})",
    re.I,
)
# With a conversation behind it, a message this short is almost always leaning
# on it ("and in Paris?", "what about 2026", "why is that").
_SHORT = 10   # words

# A first message typed the way people type: chat shorthand, and usually typos
# with it. Searched as written, "best way to become a cae piloit in the uk for
# a 17 year old his next steps" put the misspelling and the filler into every
# query, and the answer was built on generic results. The rewrite costs one
# fast call and returns clean queries and the question with its typos fixed.
_CASUAL = re.compile(
    r"\b(?:u|ur|r|pls|plz|thx|wat|wot|wht|hw|abt|bc|cuz|coz|rn|idk|tbh|ngl|smth|"
    r"sth|ppl|yr|yrs|ya|bro|bruh|gonna|wanna|lemme|dont|doesnt|cant|wont|im|ive|"
    r"whats|hows|wheres|ti|teh|adn|taht|hte)\b", re.I)
_RAMBLING = 18   # words

_PROMPT = """Rewrite the user's message into web search queries.

You are given the recent conversation and the new message. The new message
may depend on the conversation, and may exclude things rather than ask for
them.

Rules:
- Write queries a search engine can answer. No question marks, no filler.
- Resolve pronouns and references from the conversation.
- If the message EXCLUDES something, do not put that thing in the queries,
  and list it under "avoid".
- 2 to 4 queries, each a different angle. Never repeat one phrasing.
- If the message asks about news or what is happening, write queries that
  find ARTICLES, not the news section of a website.

Return JSON only:
{"standalone": "...", "queries": ["...", "..."], "avoid": ["..."], "recency_days": 0}

standalone: the new message rewritten as one self-contained question that
makes sense with no conversation — same intent, references resolved, typos
fixed. If it already stands alone, repeat it with typos fixed.

recency_days: 1 for today, 7 for this week, 30 for this month, 0 for no
preference."""


@dataclass
class Rewritten:
    queries: List[str] = field(default_factory=list)
    # The message as a question that stands on its own. Empty when no model
    # call was made, in which case the message itself is the best version.
    standalone: str = ""
    avoid: List[str] = field(default_factory=list)
    recency_days: int = 0
    used_model: bool = False
    took_ms: float = 0.0

    def __bool__(self) -> bool:
        return bool(self.queries)


def needed(question: str, *, has_history: bool = True, last_reply: str = "") -> bool:
    """Whether this question is worth a rewrite call.

    With no conversation there is nothing to resolve a reference against, so
    only an exclusion — which a search engine misreads on its own — earns
    the call.
    """
    text = " ".join(str(question or "").split())
    if not text:
        return False
    # Conversation and requests to rework the last answer are answered from
    # the thread and never searched, so there is nothing to rewrite — and the
    # rewrite is what turned "thx bro apprecate it" into a question about the
    # meaning of "thx". Consent after a question ("ok", "sure") still earns
    # the call: it may carry the next thing to look up.
    if (_social.is_social(text) and not _social.asked_a_question(last_reply)) \
            or (has_history and _social.reworks_last_answer(text)):
        return False
    # "You" in a question about who made SYRIX is SYRIX. Resolved against a
    # thread about AI assistants it became ChatGPT, and the answer credited
    # OpenAI.
    if creator.asks(text):
        return False
    if _EXCLUSION.search(text):
        return True
    if not has_history:
        return bool(_CASUAL.search(text)) or len(text.split()) >= _RAMBLING
    if _REFERENTIAL.match(text) or _PRONOUN_START.match(text):
        return True
    return len(text.split()) <= _SHORT


def exclusions(question: str) -> List[str]:
    """Things the question says it does not want, read without a model."""
    out: List[str] = []
    for match in _EXCLUSION.finditer(str(question or "")):
        phrase = match.group(1).strip().lower()
        # "not sure", "no idea" are not exclusions of a topic.
        if phrase in ("sure", "idea", "problem", "worries", "one", "longer"):
            continue
        for part in re.split(r"\band\b|\bor\b|,|/", phrase):
            cleaned = part.strip()
            if len(cleaned) >= 2:
                out.append(cleaned)
    return out[:6]


_YEAR = re.compile(r"\b(20[12]\d)\b")


def current_year(query: str, question: str) -> str:
    """A query dated to a past year the user never mentioned, redated to now.

    A model writing search queries dates them to its own training year:
    "best phones 2024" asked in 2026 finds two-year-old rankings, and the
    answer then reads as if it were 2024. A year the user wrote is theirs and
    is kept; one the model added is replaced with the current year.
    """
    now = time.localtime().tm_year
    asked = set(_YEAR.findall(str(question or "")))

    def _fix(match: "re.Match[str]") -> str:
        year = match.group(1)
        if year in asked or not (now - 4 <= int(year) < now):
            return year
        return str(now)

    return _YEAR.sub(_fix, str(query or ""))


def _history_block(history: Sequence[Dict[str, str]], limit: int = 4) -> str:
    recent = [t for t in history if t.get("content")][-limit:]
    return "\n".join(
        f"{t.get('role', 'user')}: {str(t.get('content'))[:400]}" for t in recent
    )


def run(
    question: str,
    *,
    history: Optional[Sequence[Dict[str, str]]] = None,
    fallback: Optional[List[str]] = None,
) -> Rewritten:
    """Rewrite when it is worth it, otherwise hand back the fallback."""
    plain = Rewritten(queries=list(fallback or []),
                      avoid=exclusions(question))

    if not needed(question, has_history=bool(history),
                  last_reply=_social.last_reply(history)):
        return plain

    payload = (
        f"Today is {time.strftime('%d %B %Y')}.\n\n"
        + (f"Conversation so far:\n{_history_block(history or [])}\n\n"
           if history else "")
        + f"New message: {question}"
    )

    started = time.perf_counter()
    try:
        completion = gateway.complete(
            [{"role": "system", "content": _PROMPT},
             {"role": "user", "content": payload}],
            tier=_TIER, max_tokens=300, temperature=0.1,
            prompt_chars=6000, timeout_s=_BUDGET_S,
            allow_local_fallback=False,
        )
    except Exception:
        return plain

    took = round((time.perf_counter() - started) * 1000.0, 1)
    match = re.search(r"\{.*\}", completion.text or "", re.S)
    if not match:
        plain.took_ms = took
        return plain
    try:
        parsed = json.loads(match.group(0))
    except ValueError:
        plain.took_ms = took
        return plain

    queries: List[str] = []
    for item in (parsed.get("queries") or []):
        cleaned = current_year(" ".join(str(item).split()), question)
        if cleaned and cleaned.lower() not in {q.lower() for q in queries}:
            queries.append(cleaned)
        if len(queries) >= _MAX_QUERIES:
            break
    if not queries:
        plain.took_ms = took
        return plain

    avoid = list(plain.avoid)
    for item in (parsed.get("avoid") or []):
        cleaned = " ".join(str(item).split()).lower()
        if cleaned and cleaned not in avoid:
            avoid.append(cleaned)

    try:
        recency = int(parsed.get("recency_days") or 0)
    except (TypeError, ValueError):
        recency = 0

    standalone = " ".join(str(parsed.get("standalone") or "").split())[:400]

    return Rewritten(queries=queries, avoid=avoid[:6], standalone=standalone,
                     recency_days=max(0, min(365, recency)),
                     used_model=True, took_ms=took)
