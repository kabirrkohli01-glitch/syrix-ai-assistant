"""Facts only the user can supply: ask for them, never invent them.

The failure this closes
-----------------------
"Read my CV and my grades, improve the CV, check my UCAS points, find
universities I could realistically get into." If the CV cannot be found and the
grades were never read, there are exactly two honest moves: find them, or ask.
There is a third move a language model will take unprompted — write a plausible
CV with plausible grades and carry the whole task forward on them. Every later
stage then inherits the invention, the shortlist is built on fictional points,
and the application is filled with them.

That is the single most damaging thing this mode could do, because it looks
like success the whole way through.

How it is caught
----------------
Structurally, in two halves, and neither is a hedging-phrase regex.

* :func:`required_inputs` reads the GOAL for possessive references to the user's
  own data — "my CV", "my grades", "my qualifications". These are things that
  exist only in his files or his accounts.
* :func:`unsourced` asks whether the run ever actually obtained them: did it
  read one of his files, or reach a signed-in page, for that input. If not, and
  the answer states the datum anyway, it was invented.

The remedy is never a refusal. It is a question — the one thing the mode is
allowed to interrupt for, and the thing a real assistant would do.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# Nouns that name something only the user holds. Kept concrete: each is a thing
# that lives in his files or behind his login, never on the open web.
_PERSONAL_NOUNS: Dict[str, Tuple[str, ...]] = {
    "cv": ("cv", "resume", "résumé", "curriculum vitae"),
    "grades": ("grade", "grades", "results", "marks", "predicted grades",
               "a-level", "a level", "gcse", "btec", "transcript"),
    "qualifications": ("qualification", "qualifications", "certificate",
                       "certificates", "diploma", "degree"),
    "personal details": ("address", "postcode", "date of birth", "dob",
                         "phone number", "national insurance", "passport"),
    "portfolio": ("portfolio", "cover letter", "references", "reference letter"),
    "course": ("course", "module", "assignment", "coursework", "timetable"),
}

# "my CV", "my grades", "his own results" — a possessive in front of one of
# those nouns is a claim that the thing belongs to the user.
_POSSESSIVE = r"\b(?:my|mine|our)\b[^.?!]{0,30}?"

# Tool calls that genuinely obtain the user's own data. Anything else — a web
# search, a public page — cannot.
SOURCING_ACTIONS = frozenset({
    "read_my_file", "list_my_files", "attach_file",
    # A signed-in account page is his data too: UCAS, Canvas, a university
    # portal. It reaches it through use_account, which is card-gated.
    "open_service",
})

# Shapes that look like a personal datum stated as fact. Deliberately narrow:
# the cost of a false positive is one question, the cost of a false negative is
# an application submitted with invented grades.
_GRADE_SHAPES = (
    re.compile(r"\b(?:A\*|A|B|C|D|E)\s*(?:,\s*(?:A\*|A|B|C|D|E)\s*){1,}\b"),
    re.compile(r"\b\d{2,3}\s*(?:UCAS\s*)?(?:tariff\s*)?points\b", re.I),
    re.compile(r"\bgrade[sd]?\s*(?:of|:)?\s*(?:A\*|A|B|C|D|E)\b", re.I),
    re.compile(r"\b(?:distinction|merit|pass)\b\s*(?:\*|,|\band\b)", re.I),
)


def required_inputs(goal: str) -> List[str]:
    """Which of the user's own things this request depends on, by friendly name."""
    text = " ".join(str(goal or "").lower().split())
    if not text:
        return []
    found: List[str] = []
    for label, nouns in _PERSONAL_NOUNS.items():
        for noun in nouns:
            if re.search(_POSSESSIVE + re.escape(noun) + r"\b", text):
                if label not in found:
                    found.append(label)
                break
    return found


def was_sourced(performed_actions: Iterable[str]) -> bool:
    """Did the run actually go and get the user's own data?

    One sourcing action is enough — this is asking whether the run reached his
    side of the wall at all, not auditing which file it opened.
    """
    return bool(SOURCING_ACTIONS.intersection(
        {str(a or "").split(" (", 1)[0].strip() for a in performed_actions or []}))


def states_personal_data(text: str) -> bool:
    """Does this answer assert a grade, a tariff score or similar as fact?"""
    body = str(text or "")
    return any(shape.search(body) for shape in _GRADE_SHAPES)


def unsourced(goal: str, answer: str,
              performed_actions: Iterable[str]) -> List[str]:
    """Personal inputs the request needs that the run never obtained.

    Returns [] when nothing personal was asked for, when the run did reach his
    files or accounts, or when the answer makes no factual claim about them —
    reporting "I could not find your CV" is honest and must not be pushed back.
    """
    needed = required_inputs(goal)
    if not needed:
        return []
    if was_sourced(performed_actions):
        return []
    if not states_personal_data(answer):
        return []
    return needed


def question_for(inputs: Sequence[str]) -> str:
    """The question to put to the user. One question, specific, and answerable."""
    items = [str(i) for i in inputs if str(i).strip()]
    if not items:
        return ""
    if len(items) == 1:
        subject = items[0]
    else:
        subject = ", ".join(items[:-1]) + " and " + items[-1]
    return (f"I need your {subject} before I can do this properly, and I could "
            f"not find them. Tell me where to look — a file in Downloads, "
            f"Documents or Desktop — or paste the details here, and I will "
            f"carry on from there. I am not going to guess at them.")


def pushback(inputs: Sequence[str]) -> str:
    """What the completion gate says to the agent that invented them."""
    items = ", ".join(str(i) for i in inputs)
    return (f"You have stated the user's {items} as fact, and this run never opened "
            f"one of his files or a signed-in page — so that came from nowhere. "
            f"Those are his to tell you and yours to ask for. Either go and read "
            f"the real source with list_my_files / read_my_file now, or call "
            f"ask_user and ask him for them. Do NOT write a plausible version: "
            f"every later step would be built on it.")


def guard_enabled(config: Optional[Dict[str, Any]] = None) -> bool:
    return bool((config or {}).get("supercomputer_personal_data_guard_enabled", True))


__all__ = [
    "SOURCING_ACTIONS", "required_inputs", "was_sourced", "states_personal_data",
    "unsourced", "question_for", "pushback", "guard_enabled",
]
