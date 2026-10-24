"""Which mode an Auto turn runs as.

Decided once, in code, before any model call, and then it holds for the
whole turn. That timing is the point. The runtime this replaces decided
late and then ran the deep engine in-band after the fast one had already
answered, shipping whichever finished first — so a fast turn paid a deep
turn's latency, a deep turn could be beaten to the screen by a shallow
answer, and neither was reliable. Thirteen model calls went into one
two-subject comparison that way.

The signals are structural rather than a list of impressive-sounding
words. "Deep dive" is a cue and it is included, but so are: how many
separate things are being asked, whether an answer has to weigh one thing
against another, and whether the question is the kind whose answer is a
document rather than a paragraph. Each contributes a weight, the weights
are summed, and the total is compared to one threshold — so a decision can
always be explained by naming which signals fired.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Tuple

from . import budget as budgets
from . import ranked_lists
from .budget import SPARK, ZENITH

# Asked for depth outright.
_EXPLICIT_DEPTH = re.compile(
    r"\b(deep[\s-]?dive|in depth|in-depth|thorough(?:ly)?|comprehensive|"
    r"exhaustive|research|investigate|full picture|complete picture|"
    r"write (?:me )?a (?:report|brief|analysis|paper|summary of)|"
    r"break (?:it|this) down (?:fully|properly)|everything (?:about|on)|"
    r"all the details|pros and cons of)\b",
    re.I,
)

# Asked for speed outright. Overrides everything.
_EXPLICIT_SPEED = re.compile(
    r"\b(quick(?:ly)?|just (?:tell|say|give)|one (?:line|word|sentence)|"
    r"briefly|short answer|tl;?dr|in a (?:line|sentence)|yes or no|"
    r"fast|asap|right now quickly)\b",
    re.I,
)

# The answer has to weigh things against each other rather than state one.
_TRADE_OFF = re.compile(
    r"\b(should i|worth it|better (?:to|option|choice)|which (?:one|should)|"
    r"trade[\s-]?offs?|advantages and|compare|versus|vs\.?|"
    r"recommend|advise|what would you do)\b",
    re.I,
)

# Asks for causes or consequences, which need more than a lookup.
_CAUSAL = re.compile(
    r"\b(why (?:did|does|is|are|has|have|would)|what caused|"
    r"how did .{0,40} (?:happen|come about)|implications|consequences|"
    r"what does (?:this|that|it) mean for|impact on|knock[\s-]?on)\b",
    re.I,
)

# A document-shaped request.
_DOCUMENT_SHAPED = re.compile(
    r"\b(plan|strategy|roadmap|proposal|outline|guide|walkthrough|"
    r"step[\s-]by[\s-]step|curriculum|syllabus|checklist)\b",
    re.I,
)

_SPLITTER = re.compile(r"[?;]|\band also\b|\bas well as\b|\bplus\b", re.I)

# Counting where a new question starts, rather than splitting on
# punctuation. "why did iren move, what caused it, and what does it mean for
# the sector" is three questions joined by commas and no question mark, and
# a splitter on punctuation reads it as one.
#
# The head must OPEN a clause. Matching an interrogative word anywhere
# counted the middle of a sentence as a fresh question: "can u see my
# portfolio and tell me what unis i can get in" scored three — "can u",
# "what unis", "can get" — and three is the structural hand-over to Zenith,
# so one ordinary question was routed to the deep engine. A modal after a
# pronoun subject ("i can get") and an interrogative after a verb ("tell me
# what") are parts of a clause, not the start of one, so the head is
# anchored to the start of the text, a sentence break, or a conjunction.
_ASK_HEAD = re.compile(
    r"(?:^|[,;?.!]\s*|\b(?:and|or|but|also|then|plus)\s+)"
    r"(?:what|why|how|when|where|which|who|whose|"
    r"is|are|was|were|does|do|did|can|could|should|would|will)\s+\w+",
    re.I,
)

# Above this an Auto turn hands over to Zenith.
#
# Raised from 1.0 on 2026-09-20. At 1.0, "compare rust and go for a backend
# and tell me which you would pick" and "why is my python script leaking
# memory with a shared queue" both went deep — and both are exactly what a
# fast expert should handle. Zenith is not for hard questions, it is for
# questions Spark cannot finish properly: several independent
# investigations, or an explicit request for depth. Everything between
# _HARD_ABOVE and here is "hard, and Spark takes its time".
_THRESHOLD = 1.5


@dataclass
class Decision:
    mode: str
    score: float = 0.0
    reasons: List[str] = field(default_factory=list)
    # How much room Spark should be given. Ignored for Zenith, which is
    # already "as long as it needs".
    effort: str = budgets.NORMAL

    def why(self) -> str:
        if not self.reasons:
            return "nothing suggested it needed depth"
        return ", ".join(self.reasons)


# Below this a question is a lookup or a chat turn and gathering is waste.
_LIGHT_BELOW = 0.2
# Above this Spark takes its time rather than handing over — the band
# between here and the Zenith threshold is "hard, but Spark can do it".
_HARD_ABOVE = 0.5


def _effort_for(score: float, text: str) -> str:
    """How much room the question needs, inside Spark.

    Not a second router. The same signals that decide the mode also say how
    hard the question looked, and using them twice costs nothing.
    """
    entries = ranked_lists.entries_requested(text)
    if entries >= ranked_lists.BREADTH_ENTRIES:
        # No single page reliably carries ten researched entries, so this
        # turn's cost is in opening several and merging them. That is bought
        # with deadline, sources and rounds — exactly what HARD scales.
        return budgets.HARD
    if score >= _HARD_ABOVE:
        return budgets.HARD
    if entries:
        # A shorter list still needs more than one page, so it may not be the
        # lightest turn available — but it does not need the hard budget.
        return budgets.NORMAL
    if score <= _LIGHT_BELOW and len(text.split()) <= 14:
        return budgets.LIGHT
    return budgets.NORMAL


def _sub_questions(text: str) -> int:
    """How many separate things are being asked.

    The larger of two readings: how the sentence is punctuated, and how
    many times it starts asking. Either alone misses a common shape —
    punctuation misses commas, interrogative heads miss "do a deep dive on
    X and on Y".
    """
    by_punctuation = len([p for p in _SPLITTER.split(text) if len(p.strip()) > 12])
    by_heads = len(_ASK_HEAD.findall(text))
    return max(1, by_punctuation, by_heads)


# A later part pointing back at the subject ("why is it good, and how do I
# build it") makes the parts one investigation, however many there are.
# Counted as several, "show me the best TH12 base, why it is good and how to
# build it" went to the deep engine and its sub-agents, and took over a
# minute to answer what one search covers.
_BACK_REFERENCE = re.compile(
    r"\b(?:it|its|them|they|those|these|that one|this one|the same one)\b", re.I)
# ...unless a part widens the scope past that subject: "what caused it, and
# what does it mean for the rest of the sector" needs the sector's evidence
# as well as the company's.
_WIDENS = re.compile(
    r"\b(?:sector|market|industry|economy|the rest of|other (?:companies|firms|countries|players)|"
    r"competitors?|rivals?|peers|compared? (?:to|with)|versus|vs\.?)\b", re.I)


def decide(question: str) -> Decision:
    """Spark or Zenith, with the reasons that produced it."""
    text = " ".join(str(question or "").split())
    if not text:
        return Decision(SPARK, 0.0, [], effort=budgets.LIGHT)

    # Asking for it quickly settles it. Nothing below can override an
    # explicit instruction about how long to take.
    speed = _EXPLICIT_SPEED.search(text)
    if speed:
        # Asked for it quickly settles both the mode and the effort.
        return Decision(SPARK, 0.0, [f"asked for it quickly ({speed.group(0)})"],
                        effort=budgets.LIGHT)

    signals: List[Tuple[float, str]] = []

    match = _EXPLICIT_DEPTH.search(text)
    if match:
        signals.append((1.2, f"asked for depth ({match.group(0)})"))

    parts = _sub_questions(text)
    one_subject = (parts >= 2 and bool(_BACK_REFERENCE.search(text))
                   and not _WIDENS.search(text))
    if one_subject:
        signals.append((0.2, f"{parts} parts about one subject"))
    elif parts >= 3:
        signals.append((0.9, f"{parts} separate questions"))
    elif parts == 2:
        signals.append((0.5, "two separate questions"))

    if _TRADE_OFF.search(text):
        signals.append((0.6, "needs one thing weighed against another"))

    if _CAUSAL.search(text):
        signals.append((0.5, "asks why, not what"))

    if _DOCUMENT_SHAPED.search(text):
        signals.append((0.6, "the answer is a document, not a paragraph"))

    words = len(text.split())
    if words >= 45:
        signals.append((0.6, f"{words} words of question"))
    elif words >= 25:
        signals.append((0.3, "a long question"))

    total = round(sum(weight for weight, _ in signals), 2)
    reasons = [reason for _, reason in signals]

    # Two structural hand-overs, plus the score for everything else.
    #
    # A score alone cannot separate these: the Spark cases land at 1.0-1.1
    # and the Zenith ones at 1.2-1.4, which is inside the noise of a
    # hand-weighted sum. What actually distinguishes them is not how hard
    # the question is — Spark is expected to handle hard — but whether it
    # can be finished in one investigation.
    if match:
        # The user asked for depth in as many words. That is not a signal to
        # weigh, it is an instruction.
        mode = ZENITH
    elif one_subject:
        # One subject is one investigation, which is what Spark is for at
        # any difficulty; the score still sets how much room it gets.
        mode = SPARK
    elif parts >= 3:
        # Three or more separate questions is three investigations, and
        # that is the one thing Spark structurally cannot do well: they
        # need their own evidence, and one prompt holding all of it
        # crowds each out.
        mode = ZENITH
    else:
        mode = ZENITH if total >= _THRESHOLD else SPARK

    effort = budgets.NORMAL if mode == ZENITH else _effort_for(total, text)
    if mode == SPARK and effort != budgets.NORMAL:
        reasons.append(f"{effort} effort")
    return Decision(mode, total, reasons, effort=effort)
