"""The person who built SYRIX: what SYRIX knows about him, and a web guard.

"Who is Kabirr Kohli", "tell me about him", "what do you know about me" went
to the web, which returned whichever other Kabirr Kohli the search engines
knew — SYRIX described a stranger when asked about its own creator and user.
These questions are recognised on the message as typed, kept off the web,
and answered from knowledge/owner_profile.md (plus anything in long-term
memory), which he can edit. Anything the profile does not hold is said to be
unknown and asked about, never filled in.

The root of the problem was that his name could be searched at all: every
web result for it is about somebody else. So besides recognising questions
about him, ``withhold`` is applied to every search SYRIX makes — the queries
gathered before the answer and the ones the model asks for while answering —
and a query naming him is never sent. Whatever the wording or the
conversation around it, the web is not asked who he is.

A request that explicitly asks for the web ("search online for Kabirr
Kohli", "what does Google say about me") still goes to the web.
"""
from __future__ import annotations

import os
import re
from typing import Optional

from .persona import CREATOR

# His name as a person — "Kabirr", or "Kabir(r) Kohli" in any common
# misspelling — not part of a file name, address or site ("kabirr-kohli.html",
# "kabirrkohli.com"). A bare "Kohli" or "Kabir" is somebody else: Virat Kohli,
# Kabir the poet.
_NAME = re.compile(
    r"(?<![\w/.@-])(?:kab+i+r{2,}(?:\s+koh+l(?:i|y|ee))?|kab+i+r+\s+koh+l(?:i|y|ee))(?![\w.@/-])",
    re.I,
)
# Going somewhere is a visit, not a question about him.
_VISIT = re.compile(r"\b(?:open|go to|visit|load|launch|browse|site|website|url|link|page|repo)\b", re.I)
_ABOUT_ME = re.compile(
    r"\b(?:who am i|what do (?:you|u) know about me|what (?:do|can) (?:you|u) tell me about me(?:self)?|"
    r"tell me about (?:me|myself)|describe me|what am i (?:into|like)|what (?:do|did) i build|"
    r"what have i (?:built|made)|what are my (?:interests|hobbies|projects))\b",
    re.I,
)
_WANTS_WEB = re.compile(
    r"\b(?:search|google|online|on the (?:web|internet)|linkedin|instagram|tiktok|youtube|"
    r"github|twitter|news about|articles?)\b",
    re.I,
)

_PROFILE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "knowledge", "owner_profile.md")


# "Tell me more about him" right after SYRIX has talked about him names no
# one; the reply before it does.
_POINTS_AT_HIM = re.compile(r"\b(?:him|he|his|he's|hes)\b", re.I)


def asks(text: str, last_reply: str = "") -> bool:
    """True when the message asks about the owner rather than the world.

    ``last_reply`` is SYRIX's previous answer: a "him" or "his" that follows
    an answer naming the owner is about the owner.
    """
    t = " ".join(str(text or "").split())
    if not t or len(t) > 220 or _WANTS_WEB.search(t):
        return False
    if _ABOUT_ME.search(t):
        return True
    if _NAME.search(t) and not _VISIT.search(t):
        return True
    return bool(last_reply and _POINTS_AT_HIM.search(t) and _NAME.search(str(last_reply))
                and not _VISIT.search(t))


def mentions(text: str) -> bool:
    """Whether the text names him as a person."""
    return bool(_NAME.search(str(text or "")))


def wants_web(text: str) -> bool:
    """Whether the message itself asks for the web ("search online for ...")."""
    return bool(_WANTS_WEB.search(str(text or "")))


def withhold(queries, question: str = ""):
    """Split search queries into (to run, withheld). A query naming him is
    withheld — the web knows other people by his name, not him — unless the
    message asked for the web in so many words."""
    if wants_web(question):
        return list(queries or []), []
    kept, held = [], []
    for q in queries or []:
        (held if mentions(q) else kept).append(q)
    return kept, held


def profile() -> str:
    """The profile file's facts, without its own editing instructions."""
    try:
        with open(_PROFILE_PATH, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError:
        return ""
    lines = raw.splitlines()
    # The paragraph under the title tells a person how to edit the file; the
    # model needs the facts.
    kept, in_intro = [], True
    for line in lines:
        if line.startswith("## "):
            in_intro = False
        if not in_intro or line.startswith("# "):
            kept.append(line)
    return "\n".join(kept).strip()


def note(profile_text: Optional[str] = None) -> str:
    """The evidence for a question about the owner."""
    facts = profile() if profile_text is None else profile_text
    return (
        f"ABOUT {CREATOR.upper()} — the person asking is {CREATOR} himself, or someone "
        f"asking about him. He built you. This is what you know about him:\n\n"
        f"{facts or '(nothing written down yet)'}\n\n"
        f"Answer from this and from what you remember about him — never from a web "
        f"search, which finds other people with the same name. Speak about him warmly "
        f"and with real pride: he made you, and it shows. Talk to him as \"you\" when he "
        f"is asking about himself. Where this does not cover what was asked — which games "
        f"he plays, for instance — say you don't know yet and ask him; never invent it."
    )
