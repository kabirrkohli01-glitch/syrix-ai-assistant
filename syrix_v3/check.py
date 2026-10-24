"""Did this actually answer the question?

The question this module answers: is the reply about what was asked, and does
it resolve it? Nothing in the previous runtime did that. It had fourteen modules re-reading every reply
and none of them compared the reply to the question; they checked grounding,
coverage, consistency, freshness and tone while an answer that echoed the
question back went straight to the screen.

The constraints are what keep this from becoming that stack again:

* **Detection is deterministic.** No model call to decide whether something
  is wrong. Every rule below is a shape mismatch between the question and
  the answer, checkable in microseconds, and each one corresponds to a
  failure that was actually shipped.
* **At most one repair round**, and only when a rule fires.
* **It never edits the answer.** A failing check produces an instruction
  back to the model saying what is missing. A regex that rewrites an answer
  cannot tell a good one from a bad one.
* **It never removes a conclusion, a view, or a length.** Those are the
  answer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from . import form, ranked_lists

_WORD = re.compile(r"[A-Za-z0-9']+")

# "just say yes or no", "yes or no:", "one word answer"
_WANTS_YES_NO = re.compile(
    r"\b(just )?(say |answer |tell me )?\b(yes or no|yes/no)\b", re.I)

# The commitment has to be the answer, not a word that happens to occur in
# it. A reply explaining that the sources do not provide a yes or no
# contains both words and commits to neither, which is how the live failure
# passed a naive check: it must lead with the decision.
_COMMITS_YES_NO = re.compile(
    r"^[\s\W]{0,12}(yes|no|yeah|yep|nope|probably not|probably)\b", re.I)
_HEDGES_YES_NO = re.compile(
    r"\b(?:cannot|can't|could not|couldn't|not able to|do(?:es)? not|don't)\b"
    r"[^.]{0,60}\b(?:give|provide|say|offer|make)\b[^.]{0,40}"
    r"\b(?:yes|no|recommendation|answer)\b",
    re.I,
)

# Questions whose answer has to contain a figure.
_WANTS_NUMBER = re.compile(
    r"\b(how (?:much|many|old|far|long|tall|big)|what (?:is the )?(?:price|cost|"
    r"value)|trading at|worth|percentage|percent|rate|score|"
    r"what time|when (?:is|was|does|did))\b",
    re.I,
)
_HAS_NUMBER = re.compile(r"\d")

# An explicit, reasoned "I could not find it". Not a hedge and not a
# refusal — it is the correct answer when the sources did not carry it.
_DECLINES = re.compile(
    r"\b(could not|couldn't|cannot|can't|was(?:n't| not) able to|"
    r"no (?:live|current|up-to-date|real-time))\b"
    r"[^.]{0,80}\b(find|found|get|reach|retrieve|quote|price|figure|"
    r"number|data|source)\b",
    re.I,
)

# A view was asked for explicitly.
_WANTS_VIEW = re.compile(
    r"\b(would you|do you think|your (?:opinion|view|take|thoughts)|"
    r"what do you (?:think|reckon)|which (?:would|do) you|"
    r"be (?:straight|honest) with me)\b",
    re.I,
)
_DEFLECTS = re.compile(
    r"\bas an ai\b|\bas a language model\b|\bi(?:'m| am) an ai\b|"
    r"i (?:don't|do not|cannot|can't) have (?:personal )?"
    r"(?:opinions|preferences|views|feelings|beliefs)",
    re.I,
)

# A clause whose subject is the retrieval rather than the subject matter.
#
# The most-repeated failure of the session, four times in one conversation:
# "the sources do not provide a direct recommendation on whether to buy",
# "the search returned only titles ... none of those pages were opened, so no
# substantive information is available". Saying which facts the evidence did
# not cover is sanctioned and useful; making the state of the search into the
# answer is not. What separates them is position — a caveat rides alongside
# an answer, while this arrives instead of one — so it is only a fault in the
# opening.
_RETRIEVAL_AS_SUBJECT = re.compile(
    r"\b(?:the |these |those |my )?"
    r"(?:sources?|search results?|search|snippets?|gathered sources?|"
    r"retrieved (?:sources?|pages?)|available information|"
    r"information available|evidence)\s+"
    r"(?:do|does|did|don't|doesn't|didn't|returned?|provide[sd]?|"
    r"contain(?:s|ed)?|says?|said|only|collectively|suggests?|"
    r"(?:was|were|is|are) (?:not|only))\b"
    r"|\bnone of (?:the|those|these) (?:pages|sources|results|links)\b"
    r"|\bno substantive information\b"
    r"|\bthis (?:decision|answer) is based on the available\b"
    # The commonest opener of the lot, and the emptiest: it tells the reader
    # only that a search happened, which they did not ask about and can see.
    r"|^\s*based on (?:the |recent |my )?"
    r"(?:sources?|searches|search results?|evidence|information)"
    r"(?: gathered| provided| available| i (?:found|gathered))?\b",
    re.I,
)

# The same habit as a trailing aside, which the opening-only rule above cannot
# see. Measured live: a good physics answer ended "No other opened source
# provided additional detail", and a self-reflection answer ended "(Only one
# source was opened for this answer; the reasoning is based on that article.)".
# How many pages opened is a fact about the machinery; the reader already has
# the source list. Anywhere in the answer, this is plumbing rather than prose.
_NARRATES_THE_MACHINERY = re.compile(
    r"\bno (?:other )?(?:opened |retrieved )?sources?\b[^.?!]{0,40}"
    r"\b(?:provided|added|gave|contained|covered|opened|read)\b"
    r"|\bonly (?:one|two|three|a single|\d+) sources?\b[^.?!]{0,40}"
    r"\b(?:opened|read|available)\b"
    r"|\b(?:sources?|pages?|articles?|links?)\b[^.?!]{0,40}\b(?:was|were) (?:fully )?opened\b"
    r"|\bbased on (?:that|this|the one) article\b"
    r"|\b(?:limitations?|state) of the evidence\b"
    r"|\bcomes? (?:solely|only) from the\b"
    r"|\b(?:the )?(?:snippet|search snippet)s? only\b",
    re.I,
)

# Declining to come down on either side, in the opening, where the answer
# should be. Shipped live against "is it worth it": "the exact decision
# depends on your specific needs and budget constraints" — which is true of
# every decision ever made and settles nothing.
_NO_COMMITMENT = re.compile(
    r"\b(?:it|that|this|the (?:exact )?(?:decision|answer|choice))\s+"
    r"(?:depends|will depend|would depend|is based)\b"
    r"|\bdepends (?:entirely |really )?on (?:your|his|the user's|what you)\b"
    r"|\bcomes down to (?:your|personal|individual)\b",
    re.I,
)

# A recommendation was asked for. Distinct from _WANTS_VIEW, which is about
# being asked for an opinion outright; this is the ordinary shape of the
# question — should I, is it worth it, do I need one.
_WANTS_A_VERDICT = re.compile(
    r"\b(?:should i|should we|shud i|is it worth|worth it|wroth it|"
    r"worth (?:buying|getting|upgrading|doing|it)|do i need|"
    r"do(?: you|es it)? (?:recommend|make sense)|would you recommend|"
    r"what would you (?:do|pick|choose|get|buy)|"
    r"(?:is|are) it (?:good|better|smart|wise|worth) to|"
    r"good (?:idea|time) to|which (?:should|would) i|"
    r"(?:should|shall) i (?:upgrade|buy|get|switch|wait))\b",
    re.I,
)

# A question that sets two things against each other, or asks which to have.
_COMPARES = re.compile(
    r"\b(?:compare|comparison|versus|vs\.?|which (?:is|one|of|should|would)|"
    r"better (?:than|option|choice|buy|for)|"
    r"(?:the )?best (?:one|option|choice|for))\b",
    re.I,
)

# Coming down somewhere, in any of the forms a person writes it. Detected
# POSITIVELY and across the whole answer, unlike the evasions above, because a
# comparison's verdict legitimately arrives last and because the ways of
# ducking one are endless while the ways of making one are not.
#
# Measured live: a full spec table for two phones, asked which was better for
# photography, closed with "it does not reveal which device actually produces
# better photos in practice" — a table is not a verdict, and the question was
# which to buy.
_TAKES_A_POSITION = re.compile(
    r"\bi(?:'d| would)? (?:pick|choose|go|take|buy|use|recommend|say|lean|"
    r"start|stick|avoid)\b"
    r"|\b(?:go|start|stick) with\b"
    r"|\bmy (?:call|pick|take|view|advice|answer)\b"
    r"|\bthe winner\b|\bwins?\b|\bedges? (?:it|out)\b|\bbeats\b"
    r"|\b(?:is|are) the better\b|\bbetter (?:buy|choice|option) (?:is|here)\b"
    r"|\bi recommend\b|\brecommend(?:ing)? (?:the|that|you)\b"
    r"|\bif (?:it were|i were|i had to|you (?:only|mainly|mostly))\b"
    r"|\b(?:not )?worth it\b|\bdon'?t bother\b"
    r"|\bunless you\b|\bfor (?:your|this) (?:case|use|purpose)\b"
    r"|^\s*(?:yes|no|not yet|neither|either)\b",
    re.I,
)

# The heading a model puts above a citation list.
_SOURCE_HEADING = re.compile(
    r"^\s*(?:\*\*|#+\s*)?sources?\b\s*:?\s*(?:\*\*)?\s*$", re.I | re.M)

# Asserting that something does not exist. The training cutoff makes this the
# characteristic wrong answer about anything recent: asked to compare an
# iPhone 13 with an 18, the reply opened "there isn't a model called iPhone
# 18" and then compared it with a 14 instead. Only a fault when nothing was
# cited — having read sources and concluding a thing is not real is a finding,
# whereas concluding it from memory alone is the cutoff talking.
_DENIES_EXISTENCE = re.compile(
    r"\b(?:there (?:is|are) no|there isn't|there aren't|isn't a|is not a|"
    r"no such)\b[^.?!]{0,60}\b(?:called|named|exists?|model|product|"
    r"version|device|phone|release)\b"
    r"|\b(?:does|do|did) not (?:yet )?exist\b"
    r"|\b(?:has|have) not (?:yet )?been (?:released|announced|launched)\b",
    re.I,
)

# Saying where something came from, in any of the forms a person writes it.
# Used to tell an answer that rests on evidence apart from one that merely
# contains numbers.
_ATTRIBUTES = re.compile(
    r"\b(?:per|according to|via|reported by|cited by|sourced from|"
    r"source|sources)\b"
    r"|\[\d{1,2}\]"
    r"|\b[a-z0-9][a-z0-9-]{1,30}\.(?:com|co\.uk|org|net|io|gov|edu|ac\.uk|"
    r"uk|de|fr)\b"
    # A reference into the user's own attachment is attribution, and the most
    # checkable kind there is.
    r"|\bon page \d+|\bp\.\s?\d+|\b(?:page|sheet|slide|tab) \d+"
    r"|\.(?:pdf|docx?|xlsx?|pptx?|csv|txt|png|jpe?g)\b"
    # Naming the publication inline was not recognised, so an answer that had
    # already attributed its figures — "(CEO Today, 2026-05-20)", "first in
    # the Times Good University Guide 2027" — was told to attribute them and
    # paid a repair round, which can gut an answer. Both forms below carry
    # their own evidence of being a reference: a parenthetical containing a
    # year, or a named table.
    r"|\([^)]{2,48}(?:19|20)\d{2}[^)]{0,12}\)"
    r"|\bthe [\w'&./-]{2,40}(?:\s+[\w'&./-]{2,40}){0,4}\s+"
    r"(?:guide|league table|index|rankings?|report|survey)\b"
    # 【1†L1-L3】 (shown as [1]) and "NVIDIA's About page describes…" both attribute.
    r"|【\d{1,2}(?:†[^】]{0,40})?】|\b(?:site|website|homepage|page|profile|channel)(?:'s)?\s+"
    r"(?:says|states|describes|lists|shows|notes|explains|claims|highlights|mentions)\b",
    re.I,
)

# Declaring an inability without having tried. Shipped live: asked whether it
# could see the user's portfolio and say which universities the user could get into, it
# replied that it had no access to the portfolio and offered generic advice on
# assessing university chances — while holding a documents tool that reads the user's
# files and a web tool that reads entry requirements. Not calling a tool and
# then reporting its result is the same defect as inventing a figure.
_DISCLAIMS_ACCESS = re.compile(
    r"\bi (?:do not|don't|cannot|can't) (?:have |get )?"
    r"(?:access to|see|read|open|reach|view|look at)\b"
    r"|\bi (?:am|'m) (?:not able|unable) to (?:access|see|read|open|reach)\b"
    r"|\bi (?:do not|don't) have (?:the ability|any way|a way) to\b",
    re.I,
)

# Denying a capability this build has. Separate from the rule above because it
# is false regardless of whether a tool was called: the web tool, the document
# reader, the vision lane, the calculator and the memory store all exist.
# Measured live across eleven phrasings, five were wrong — "I don't have live
# internet access at this moment", "I cannot access external web content or
# perform real-time internet searches", "I don't have direct internet access".
# A general model without tools is right to say these; this one is not, and it
# says them from habit while a working tool sits unused.
_DENIES_A_CAPABILITY = re.compile(
    r"\bi (?:do not|don't|cannot|can't|am not able to|'m not able to)\b"
    r"[^.?!]{0,40}\b(?:internet|web|online|browse|browsing|search the|"
    r"real[- ]?time|live data)\b"
    r"|\bno (?:live|direct|real[- ]?time|external) (?:internet|web|"
    r"access|connection|data)\b"
    r"|\bi have no (?:internet|web|browsing|memory|access)\b"
    r"|\brestricted to (?:local|pre[- ]?loaded)\b"
    r"|\bi (?:cannot|can't) (?:access|retrieve) external\b"
    r"|\bmy (?:knowledge|training) (?:has a |cut[- ]?off|is limited to)\b"
    # NOT widened to "I don't HAVE access to…", though that is the form the
    # local lane most often produces. Catching it was measured and made the
    # behaviour WORSE: concessions went from 2 of 5 to 3 of 5, and every reply
    # then opened with "Got it." — the 7B answering the repair note rather
    # than the question, and restating the false premise more firmly. The
    # detection was right and the response to it was not; a repair round is
    # not a lever on a model this small. See the handoff, fallback parity.
    # Denying the DOCUMENT capability, which the build has and which this rule
    # did not cover: every alternative above needs an internet word, so
    # "Correct, I cannot read or access any of your personal files" passed
    # untouched. Measured on the local lane, agreeing with a false premise put
    # to it as a statement.
    r"|\bi (?:do not|don't|cannot|can't|am not able to) "
    r"(?:read|access|open|see|view)\b[^.?!]{0,40}"
    r"\b(?:files?|documents?|attachments?|uploads?|pdfs?)\b",
    re.I,
)

# Any figure in a line of text, commas and decimals included.
_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")

_STOPWORDS = frozenset(
    "a an the is are was were be do does did what whats who which when where "
    "why how to of for in on at by with from and or if that this it you i me "
    "my your just say tell give show can could would should will".split()
)


@dataclass
class Verdict:
    """What, if anything, is wrong with the answer."""

    problems: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def instruction(self) -> str:
        """What to tell the model so it can put it right."""
        return (
            "That reply does not answer the question yet:\n- "
            + "\n- ".join(self.problems)
            + "\n\nWrite the answer again, properly. Keep everything that was "
            "already right and fix only what is listed. Do not apologise and "
            "do not mention this note."
        )


def _content_words(text: str) -> List[str]:
    return [w.lower() for w in _WORD.findall(text or "")
            if w.lower() not in _STOPWORDS and len(w) > 2]


def _echoes_the_question(question: str, answer: str) -> bool:
    """The reply is the question wearing a hat.

    Shipped live: "just say yes or no would u buy btc right now" was
    answered with "Yes or no would u buy btc right now".
    """
    q, a = _content_words(question), _content_words(answer)
    if not q or not a or len(" ".join(a)) > len(" ".join(q)) * 1.6:
        return False
    # An echo adds nothing of its own. "Canberra is the capital of Australia"
    # contains the whole question and was flagged, buying a repair round; the
    # word the question lacked is the answer.
    return set(a) <= set(q)


def _opening(answer: str, sentences: int = 1) -> str:
    """The lead sentence, which is where the answer is supposed to be.

    Two rules below are about position rather than presence, and the window
    is deliberately one sentence. Naming what the evidence did not cover is
    sanctioned and useful — the evidence pack asks for it — so "it is a small
    London consultancy trading since 2019. The sources do not give a
    headcount." is a good answer with a fair caveat, and a two-sentence
    window flagged it. Only the lead can be a non-answer, because only the
    lead arrives instead of one.
    """
    parts = re.split(r"(?<=[.!?])\s+", answer.strip())
    return " ".join(parts[:max(1, sentences)])


def _computed_figures(computed: str) -> List[float]:
    """The results the calculator returned, from its ``expr = value`` lines.

    Only values of one or more are kept. A rate like 0.0035 is an intermediate
    step a correct answer need not quote, so requiring it would fire on good
    work.
    """
    out: List[float] = []
    for line in str(computed or "").splitlines():
        if "=" not in line:
            continue
        for match in _NUMBER.finditer(line.split("=", 1)[1]):
            try:
                value = float(match.group(0).replace(",", ""))
            except ValueError:
                continue
            if abs(value) >= 1:
                out.append(value)
    return out


def _quotes_any(answer: str, figures: List[float]) -> bool:
    """Does the answer contain any of these numbers, at any sane rounding?"""
    present = {m.group(0).replace(",", "") for m in _NUMBER.finditer(answer)}
    for value in figures:
        for places in (0, 1, 2):
            if f"{value:.{places}f}" in present:
                return True
            # A trailing ".0" is written as an integer by most models.
            if places == 0 and f"{value:.0f}" in present:
                return True
    return False


def _is_mostly_a_source_list(answer: str) -> bool:
    """A citation list with a sentence balanced on top of it.

    Shipped live, in full: one line saying the sources did not provide a
    recommendation, then "Sources:", then three publishers each annotated
    with what they were about. Every source named and not one fact from any
    of them.
    """
    heading = _SOURCE_HEADING.search(answer)
    if not heading:
        return False
    prose = answer[:heading.start()].strip()
    return len(prose) < 220


def _is_a_bare_headline(answer: str) -> bool:
    """One line, no sentence, reading like the title of an article.

    Shipped live: asked what was happening with Bitcoin, the reply was
    "Kevin O'Leary Names the One Thing Standing Between Bitcoin and $1
    Million" — a title, which names no thing.
    """
    body = answer.strip()
    if not body or "\n" in body or len(body) > 160:
        return False
    if body.endswith((".", "!", "?", ":")):
        return False
    words = body.split()
    if len(words) < 5:
        return False
    capitalised = sum(1 for w in words if w[:1].isupper())
    return capitalised >= max(3, len(words) // 2)


# A "does the answer cover both sides of the comparison" rule was written
# here and removed. Extracting the two subjects from free text is not
# reliable enough: "compare rust and go for backend work" splits on "and",
# and the second side reduces to "backend" because a two-letter language
# name is indistinguishable from a stopword. It fired on correct answers,
# and a check that fires on correct answers costs a model call every turn
# and stops being believed. The failure it was aimed at — a comparison
# invented without looking anything up — is fixed where it belongs, in
# triage, which now always gathers evidence for a comparison.


def run(
    question: str,
    answer: str,
    *,
    had_evidence: bool = False,
    cited: int = 0,
    tools_used: int = 0,
    computed: str = "",
) -> Verdict:
    """Compare the answer to the question. Deterministic, microseconds."""
    verdict = Verdict()
    q = str(question or "")
    a = str(answer or "").strip()

    if not a:
        verdict.problems.append("It is empty.")
        return verdict

    if _echoes_the_question(q, a):
        verdict.problems.append(
            "It repeats the question back instead of answering it.")

    # A form the user set is the whole brief: a one-word or table answer is
    # not also judged on attribution or on picking a side.
    if form.requested(q):
        verdict.problems.extend(form.problems(q, a))
        return verdict

    padded = ranked_lists.padded_entries(q, a)
    if padded:
        verdict.problems.append(
            f"{padded} ranked entries say in their own wording that the "
            "sources do not place them there. Give only the entries the "
            "evidence supports, then say how many of the requested positions "
            "could be established and what is missing. A short list that is "
            "true beats a padded one that is complete.")

    if _is_a_bare_headline(a):
        verdict.problems.append(
            "It is a headline, not an answer. Say what actually happened — "
            "who did what, when, and the numbers — not the title of a page "
            "about it.")

    if _WANTS_YES_NO.search(q) and (
            _HEDGES_YES_NO.search(a) or not _COMMITS_YES_NO.match(a)):
        verdict.problems.append(
            "A yes or no was asked for and the reply does not start with "
            "one. Open with the word — yes or no — then give the reason. "
            "Sources never contain a recommendation; being asked is what "
            "your judgement is for.")

    # Saying plainly that the figure could not be found IS an answer to a
    # figure question, and the better one when it is true. Only silence
    # about it counts as a miss.
    if (_WANTS_NUMBER.search(q) and not _HAS_NUMBER.search(a)
            and not _DECLINES.search(a)):
        verdict.problems.append(
            "The question asks for a figure and there is no number in the "
            "reply. Give the figure, or say plainly that it could not be "
            "found and what you did look at.")

    if _WANTS_VIEW.search(q) and _DEFLECTS.search(a):
        verdict.problems.append(
            "A view was asked for and the reply deflects instead of giving "
            "one. Take a position and say why.")

    opening = _opening(a)

    if _NARRATES_THE_MACHINERY.search(a):
        verdict.problems.append(
            "It tells him about the retrieval — how many sources opened, or "
            "that nothing else added anything. That is machinery, not an "
            "answer, and the source list is already on screen. Cut the "
            "aside; keep the substance.")

    if _RETRIEVAL_AS_SUBJECT.search(opening):
        verdict.problems.append(
            "It opens by describing the search rather than answering. What "
            "the sources did or did not contain is not the subject — he "
            "asked about the subject. Lead with what you do know, then name "
            "in one line what you could not confirm. Never make the state "
            "of the evidence the answer.")

    if _WANTS_A_VERDICT.search(q) and _NO_COMMITMENT.search(opening):
        verdict.problems.append(
            "He asked for a recommendation and the reply does not make one. "
            "Open with the call — worth it, or not — then the conditions "
            "that would change it. \"It depends on your needs\" is true of "
            "every decision and settles nothing; sources never carry a "
            "recommendation, and being asked is what your judgement is for.")

    if _COMPARES.search(q) and not _TAKES_A_POSITION.search(a):
        verdict.problems.append(
            "He set two things against each other and the reply never comes "
            "down on one. Lay out the differences, then say which you would "
            "take and for whom — and if the honest answer is that it depends "
            "on one thing, name that thing and decide for the likely case. A "
            "specification table is not a verdict.")

    if _is_mostly_a_source_list(a):
        verdict.problems.append(
            "It is a list of sources with almost nothing above it. Citations "
            "support an answer, they are not one — put the facts, figures and "
            "conclusion in the body and keep the sources as attribution.")

    # The calculator ran and the answer does not contain a single figure it
    # returned. Measured live, repeatedly: the reply wrote "Using a calculator
    # to find (1.0035)^84: ≈ 1.32796" — having just been handed 1.3410953 —
    # and carried the invented factor through to a final value £164 out. A
    # tool result that is fetched and then overwritten is worse than no tool,
    # because the working now claims an authority it does not have.
    figures = _computed_figures(computed)
    if figures and not _quotes_any(a, figures):
        verdict.problems.append(
            "You called the calculator and then wrote different numbers. The "
            "figures it returned are the figures — "
            + ", ".join(f"{f:,.2f}".rstrip("0").rstrip(".") for f in figures[:4])
            + ". Use them exactly, and if one of them is not what you meant to "
            "compute, calculate the right expression instead of adjusting the "
            "result by hand.")

    if _DENIES_A_CAPABILITY.search(a):
        verdict.problems.append(
            "It denies something this build can do. You have live web search "
            "and page reading, a document reader for PDF and Office files, a "
            "vision lane for images, an exact calculator, and a memory that "
            "persists between conversations. Say what you can do and then do "
            "it. If a particular attempt failed, say which attempt — never "
            "that the capability does not exist.")

    if tools_used == 0 and not had_evidence and _DISCLAIMS_ACCESS.search(a):
        verdict.problems.append(
            "It says it cannot see or reach something, without having tried. "
            "You have tools for his documents, his screen, the filesystem and "
            "the web — use them and answer from what they return. If you do "
            "check and it genuinely is not reachable, say what you tried and "
            "what would make it reachable.")

    if not _ATTRIBUTES.search(a) and _DENIES_EXISTENCE.search(a):
        verdict.problems.append(
            "It states that something does not exist, without citing "
            "anything. Your training stopped before today, so not "
            "recognising a product, version or event is not evidence that "
            "it is not real — it is the likeliest sign that it came out "
            "after you. Go and look, answer from what you find, and if you "
            "genuinely cannot confirm it say that instead.")

    # Whether the answer says where anything came from, read off the text.
    #
    # This used to be `cited == 0`, where `cited` counts the sources the model
    # fetched through a tool — which measures the retrieval, not the answer.
    # Evidence is normally PREFETCHED and arrives with the question, so a
    # model that needed no tool call had `cited == 0` however carefully it
    # attributed, and the rule fired on any answer containing a digit. That
    # is a repair round on nearly every research turn, which is the cost this
    # module exists to avoid. Attribution is a property of the prose, so it
    # is read from the prose.
    if had_evidence and _HAS_NUMBER.search(a) and not _ATTRIBUTES.search(a):
        verdict.problems.append(
            "It states figures but never says where any of them came from, "
            "while sources were gathered for this turn. Attribute them — the "
            "outlet, the page, or the date you read it.")

    return verdict


def repair_message(verdict: Verdict) -> Optional[dict]:
    """The system turn that asks for the answer again. None when it is fine."""
    if verdict.ok:
        return None
    return {"role": "system", "content": verdict.instruction()}
