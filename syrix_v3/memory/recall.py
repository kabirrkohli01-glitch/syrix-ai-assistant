"""Choosing which of what SYRIX knows belongs in this particular prompt.

Lexical, not vector, and that is a decision rather than a shortcut. A
personal assistant's fact store is tens to low hundreds of statements about
one person; at that size an embedding model costs seconds to load and
milliseconds of accuracy, and it puts a dependency and a download between
SYRIX and remembering that its owner takes the user's coffee black.

What matters more than the similarity function is restraint. Everything
recalled displaces something else in a finite prompt, so only what survives
scoring goes in — the smallest set of high-signal facts, not the most
facts.
"""
from __future__ import annotations

import re
import time
from typing import List, Sequence, Tuple

from . import store
from .store import Fact

_WORD = re.compile(r"[A-Za-z0-9'£$€.+#-]+")

_STOPWORDS = frozenset(
    "a an the is are was were be been do does did what whats who which when "
    "where why how to of for in on at by with from and or if that this it "
    "you i me my your we us they them can could would should will just say "
    "tell give show about into over under so then than there here".split()
)

# Below this a fact is noise for the question being asked.
_FLOOR = 0.18
# Facts always worth carrying regardless of the question: who the user is and how
# the user wants to be dealt with. Small by design.
_ALWAYS = (store.KIND_PERSON, store.KIND_PREFERENCE)
_ALWAYS_LIMIT = 6


def _terms(text: str) -> List[str]:
    return [w.lower() for w in _WORD.findall(str(text or ""))
            if w.lower() not in _STOPWORDS and len(w) > 1]


# Two words count as the same word when they share this much of a prefix.
# "trade"/"trades"/"trading" all agree on "trad"; "open"/"opened" on "open".
# Without it, asking "when do i trade" failed to recall "The user trades the
# London open" — which is most of what lexical recall is for.
_PREFIX = 4


def _matches(term: str, fact_terms: Sequence[str]) -> bool:
    if term in fact_terms:
        return True
    if len(term) < _PREFIX:
        return False
    head = term[:_PREFIX]
    return any(other.startswith(head) for other in fact_terms if len(other) >= _PREFIX)


def score(fact: Fact, query_terms: Sequence[str]) -> float:
    """How much this fact bears on this question."""
    if not query_terms:
        return 0.0
    fact_terms = list(set(_terms(fact.text)))
    if not fact_terms:
        return 0.0

    hits = sum(1 for t in query_terms if _matches(t, fact_terms))
    if not hits:
        return 0.0
    # Against the question's length, so a long question is not automatically
    # a better match for every fact.
    relevance = hits / len(set(query_terms))

    total = relevance
    # A fact that has proved useful before is more likely to be useful now.
    total += min(0.12, 0.03 * fact.uses)
    # Recency as a tiebreak only — something learned last year about the user's
    # setup is not less true than something learned today.
    total += 0.08 * (1.0 / (1.0 + fact.age_days() / 90.0))
    return round(total, 4)


def relevant(question: str, *, limit: int = 8) -> List[Tuple[Fact, float]]:
    """Facts worth putting in front of the model for this question."""
    facts = store.all_facts()
    if not facts:
        return []
    query_terms = _terms(question)

    scored = [(f, score(f, query_terms)) for f in facts]
    chosen = [(f, s) for f, s in scored if s >= _FLOOR]
    chosen.sort(key=lambda pair: -pair[1])
    chosen = chosen[:limit]

    # Who the user is and how the user likes things done apply to every turn, so they
    # ride along even when the question does not mention them.
    already = {f.id for f, _ in chosen}
    standing = [f for f in facts if f.kind in _ALWAYS and f.id not in already]
    standing.sort(key=lambda f: -f.uses)
    chosen.extend((f, 0.0) for f in standing[:_ALWAYS_LIMIT])
    return chosen


def block(question: str, *, limit: int = 8) -> str:
    """The memory section of the prompt, or an empty string.

    Facts are stated flatly with their age, so the model can weigh "said
    three minutes ago" differently from "said in June" without being told
    how.
    """
    chosen = relevant(question, limit=limit)
    if not chosen:
        return ""

    lines: List[str] = []
    for fact, _ in chosen:
        age = fact.age_days()
        if age < 1:
            when = "today"
        elif age < 14:
            when = f"{int(age)}d ago"
        else:
            when = time.strftime("%b %Y", time.localtime(fact.learned_at))
        lines.append(f"- {fact.text} ({when})")

    store.mark_used([f.id for f, _ in chosen])
    return (
        "\n".join(lines)
        + "\n\nThese are things you already know about him. Use them without "
        "announcing that you remembered them, and if something here is "
        "contradicted by what he says now, he is right and the note is out "
        "of date."
    )
