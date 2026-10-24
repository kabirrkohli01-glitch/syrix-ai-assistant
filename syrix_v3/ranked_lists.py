"""Recognising a question that asks for a ranked list, and an answer that pads one.

One shape, needed in two places, so it lives in neither of them. The router
needs the SIZE of the list to decide how much gathering the turn needs; the
check needs to know a list was asked for before it can judge whether the answer
invented part of it. Written twice, the two would drift, and the failure that
produces is silent: the router stops treating a phrasing as a list question
while the check goes on policing it, or the reverse.

The defect behind both, measured on a live turn: "top 10 unis in the uk" is six
words, so it scored as the lightest turn available — 0.7x the deadline and 0.6x
the sources — while the answer it asks for is ten separately-evidenced entries.
That turn found twelve pages, opened two before the gathering window closed,
and then filled the remaining eight ranks from the model's own memory, each
hedged in its own line ("not explicitly listed as top 10 in all sources",
"often within the top 10"). Question length is the router's proxy for how much
work an answer needs, and for this one shape the proxy inverts.
"""
from __future__ import annotations

import re

# The list, and how many entries it asks for. The count matters because the
# work scales with it: five entries is an ordinary turn, ten is a breadth
# problem no single page covers.
_LIST_SHAPED = re.compile(
    r"\b(?:top|best|worst|biggest|largest|richest|highest[- ]?(?:paid|rated)|"
    r"most (?:popular|valuable|expensive)|leading)\s+(\d{1,3})\b"
    r"|\b(\d{1,3})\s+(?:biggest|largest|best|worst|top|leading|most)\b"
    r"|\b(?:top|best)\s+(ten|twenty|five)\b",
    re.I,
)

_WORD_COUNTS = {"five": 5, "ten": 10, "twenty": 20}

# A ranked list of at least this many entries cannot be covered by one page.
BREADTH_ENTRIES = 8

_NUMBERED_ENTRY = re.compile(r"(?m)^\s*\d{1,2}[.)]\s+(.+)$")

# Scoped to the text of ONE entry, never to a closing caveat about the answer
# as a whole — a turn that lists what it found and then explains the limits of
# the ranking methodology is doing the right thing and must not be repaired.
_ENTRY_DISCLAIMS_ITS_RANK = re.compile(
    r"\bnot (?:explicitly |specifically )?(?:listed|named|mentioned|included|"
    r"ranked|confirmed)\b"
    r"|\b(?:often|sometimes|typically|usually|generally|frequently) "
    r"(?:within|among|in|ranks in) the top\b"
    r"|\bnot (?:in|on) (?:the|this|these) (?:list|source|sources|table)\b"
    r"|\bfrom (?:my own|general|prior) (?:memory|knowledge)\b",
    re.I,
)


def entries_requested(text: str) -> int:
    """How many ranked entries the question asks for, or 0 if it asks for none."""
    match = _LIST_SHAPED.search(str(text or ""))
    if not match:
        return 0
    for group in match.groups():
        if not group:
            continue
        return int(group) if group.isdigit() else _WORD_COUNTS.get(group.lower(), 0)
    return 0


def padded_entries(question: str, answer: str) -> int:
    """Ranked entries whose own wording admits the sources do not place them.

    Counted only when a ranked list was actually asked for: the same hedging
    inside an ordinary answer is caution, not padding.
    """
    if not entries_requested(question):
        return 0
    return sum(1 for m in _NUMBERED_ENTRY.finditer(str(answer or ""))
               if _ENTRY_DISCLAIMS_ITS_RANK.search(m.group(1)))
