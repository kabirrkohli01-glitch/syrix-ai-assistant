"""Gathering evidence before the model is asked anything.

The measured failure this exists to fix: with retrieval behind a tool call,
Spark spends round one deciding to search and round two writing the answer,
which leaves no round for opening a page. Asked what Nvidia was trading at,
it held nineteen search results including the Yahoo Finance quote page,
opened none of them, and answered with a closing price from 2024.

Making retrieval a round trip is the problem. Here it is not one: triage
runs in code at t=0, gathering starts immediately, and the evidence arrives
with the question. The common research turn becomes one model call holding a
full pack rather than two holding snippets.

The search tool stays registered. Triage is a fast heuristic and will miss
cases; when it does, the model can still go and look.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional

from .. import ranked_lists
from ..budget import Budget
from . import gather as _gather
from . import profiles as _profiles
from . import rewrite as _rewrite
from . import site as _site
from . import social as _social
from .gather import Evidence, Source
from .pack import build, citations
from .triage import PROBLEM_REASON, SELF_REASON, SOCIAL_REASON, THREAD_REASON, Need, assess

# `gather` is deliberately NOT re-exported as a name here. Binding the
# function over the submodule of the same name made `from ..research import
# gather` hand callers the function, and `gather.search(...)` then failed at
# runtime inside a tool, where the error was caught and reported to the model
# as "the search tool is broken".
__all__ = [
    "Destination", "Evidence", "Need", "PROBLEM_REASON", "SOCIAL_REASON", "Source", "SELF_REASON",
    "THREAD_REASON", "reworks",
    "assess", "build", "citations", "destination", "pack_chars", "prepare",
    "social_note", "understand",
]


@dataclass
class Destination:
    """A named place to go — a profile or a website — rather than a question
    to research. Empty when the message names neither."""

    profile: Optional[_profiles.ProfileAsk] = None
    visit: Optional[_site.Visit] = None

    def __bool__(self) -> bool:
        return bool(self.profile or self.visit)

    @property
    def label(self) -> str:
        if self.profile:
            return f"{self.profile.platform} profile {self.profile.handle}"
        return self.visit.label if self.visit else ""


def destination(question: str, subject: str = "") -> Destination:
    """Where the message says to go, if anywhere.

    Checked before triage and before the live-data detectors, because both
    misread a destination. Triage exempted "can u open nvidia.com and tell me
    what they do" as a question about SYRIX's abilities and fetched nothing;
    anything that reached the search engines was searched as one sentence.

    The message as typed is read first, since it carries any link verbatim;
    the self-contained rewrite is read second, since it is what turns "and
    their careers page?" into a named site.
    """
    for text in (question, subject):
        if not text:
            continue
        profile = _profiles.detect(text)
        if profile:
            return Destination(profile=profile)
    for text in (question, subject):
        if not text:
            continue
        visit = _site.detect(text)
        if visit:
            return Destination(visit=visit)
    return Destination()


def _visit(
    where: Destination,
    subject: str,
    budget: Budget,
    *,
    request_id: str,
    deadline_s: float,
    on_step: Optional[Callable[[str, str, str], None]],
) -> tuple[Need, Evidence]:
    """Go to the destination; search only if it cannot be reached.

    A search fallback is still evidence about the right thing — the site's
    own pages or the profile's public trace — and the problems that forced
    it travel with it, so the answer can say what could not be opened.
    """
    import time as _time

    started = _time.monotonic()
    web_mode = budget.mode if budget.mode in ("spark", "zenith") else "spark"
    if where.profile:
        ask = where.profile
        need = Need(True, f"a {ask.platform} profile", _profiles.search_queries(ask), 0)
        evidence = _profiles.read(ask, request_id=request_id, deadline_s=deadline_s,
                                  on_step=on_step)
    else:
        ask = where.visit
        label = ask.label
        section = ask.section or ""
        queries = [f"{label} {section}".strip() + " official site", subject]
        need = Need(True, f"a visit to {label}", queries, 0)
        evidence = _site.visit(ask, request_id=request_id, deadline_s=deadline_s,
                               web_mode=web_mode, on_step=on_step)
    if evidence.sources:
        return need, evidence

    left = deadline_s - (_time.monotonic() - started)
    if left < 1.2:
        return need, evidence
    fallback = _gather.gather(
        need.queries, web_mode=web_mode, request_id=request_id,
        max_sources=budget.max_sources, max_open=_pages_to_open(budget, subject),
        deadline_s=left, on_step=on_step)
    fallback.problems = evidence.problems + fallback.problems
    fallback.profile = evidence.profile
    if where.profile:
        fallback.sources = _profiles.about_the_profile(where.profile, fallback.sources)
    return need, fallback


def reworks(question: str) -> bool:
    """Is this a request to redo or reshape the last answer?"""
    return _social.reworks_last_answer(question)


def social_note() -> str:
    """What the model is told when the message is conversation, not a question."""
    return _social.SOCIAL_NOTE


def understand(question: str, *, history: Optional[List[dict]] = None) -> _rewrite.Rewritten:
    """The message resolved against the thread, when it leans on one.

    Run before anything else in a turn, because what the message is about
    decides both what to search for and whether a live data source answers
    it outright. Costs nothing when the message stands on its own.
    """
    return _rewrite.run(question, history=history, fallback=[])

# What the rest of the prompt needs before evidence gets anything: the persona
# is a little over 6,000 characters and the history plus the question another
# 1,200 or so.
#
# This was a flat 0.6 of `prompt_chars`, which ignored everything else in the
# prompt and so overcommitted it. The evidence is assembled last, so the
# overrun was resolved by the transport truncating the prompt — from the END,
# which is exactly where the newest sources sit. Sizing the pack to what is
# genuinely left means the evidence that arrives is the evidence the answer
# was written against.
_NON_EVIDENCE_CHARS = 8200


def _drop_excluded(evidence: Evidence, avoid: List[str]) -> int:
    """Remove sources about something the question ruled out.

    Enforced here rather than trusted to the engine. Asked for news
    "outside ai and tech", a search engine reads those words as topics to
    match and returns the AI section of three publishers — which is not a
    near miss, it is the opposite of the request.
    """
    if not avoid:
        return 0
    needles = [a for a in (s.strip().lower() for s in avoid) if len(a) >= 2]
    if not needles:
        return 0

    kept = []
    dropped = 0
    for source in evidence.sources:
        haystack = " ".join((source.title, source.snippet, source.url,
                             source.domain)).lower()
        if any(n in haystack for n in needles):
            dropped += 1
            continue
        kept.append(source)

    # Never strip the evidence to nothing: an answer with imperfect sources
    # and a caveat beats one with none.
    if kept:
        evidence.sources = kept
        if dropped:
            evidence.problems.append(
                f"{dropped} result(s) dropped for being about "
                + ", ".join(needles[:3])
            )
    return dropped


def _pages_to_open(budget: Budget, question: str) -> int:
    """How many pages this turn spends its read window on.

    A flat count per mode assumes every question needs the same breadth, and a
    ranked list is the case where it plainly does not: no single page carries
    ten researched entries, so the work is opening several and merging them.
    Measured on the flat count, "top 10 unis in the uk" opened two pages and
    answered from one of them.

    Reading is concurrent, so a higher ceiling costs a wave rather than a page
    at a time, and the gathering deadline still bounds the whole thing — pages
    the window does not reach are reported rather than dropped.
    """
    base = 6 if budget.mode == "spark" else 16
    entries = ranked_lists.entries_requested(question)
    if entries >= ranked_lists.BREADTH_ENTRIES:
        # Two pages per five entries asked for, so a top-10 opens enough to
        # cross-check a ranking rather than transcribe whichever page ranked
        # first, and never fewer than the mode's ordinary breadth.
        return max(base, min(2 * entries // 5 + 8, 14))
    return base


def prepare(
    question: str,
    budget: Budget,
    *,
    request_id: str,
    deadline_s: float,
    history: Optional[List[dict]] = None,
    has_attachment: bool = False,
    on_step: Optional[Callable[[str, str, str], None]] = None,
    rewritten: Optional[_rewrite.Rewritten] = None,
    skip_web: bool = False,
    where: Optional[Destination] = None,
) -> tuple[Need, Optional[Evidence]]:
    """Decide whether to gather, and gather if so.

    Returns the triage decision either way, so the caller can tell "nothing
    was needed" apart from "something was needed and none of it arrived" —
    they read identically in the answer and mean opposite things.
    """
    # Triage reads the self-contained form when there is one: "and in
    # Paris?" carries none of the words that mark a weather question, and
    # the question it stands for carries all of them.
    subject = (rewritten.standalone if rewritten and rewritten.standalone
               else question)
    if where is None:
        where = destination(question, subject)
    if where:
        return _visit(where, subject, budget, request_id=request_id,
                      deadline_s=deadline_s, on_step=on_step)
    need = assess(subject, max_queries=budget.max_queries,
                  has_attachment=has_attachment,
                  last_reply=_social.last_reply(history))
    if not need.wanted or deadline_s <= 0.4 or skip_web:
        return need, None

    # A referential or excluding question is rewritten against the thread
    # first. Everything else keeps the deterministic queries and pays
    # nothing for the privilege.
    if rewritten is None:
        rewritten = _rewrite.run(question, history=history, fallback=need.queries)
    elif not rewritten.queries:
        rewritten.queries = list(need.queries)
    if rewritten.queries:
        need.queries = rewritten.queries
    # Never search for the owner by name; see owner.py. If that leaves
    # nothing to look up, the turn is answered from what SYRIX knows.
    from .. import owner as _owner
    need.queries, _held = _owner.withhold(need.queries, question)
    if _held and not need.queries:
        need.wanted = False
        need.reason = "about the owner, answered from what SYRIX knows"
        return need, None
    if rewritten.recency_days:
        need.recency_days = rewritten.recency_days
    if rewritten.used_model:
        need.reason += f" · rewritten in {rewritten.took_ms:.0f}ms"
        if on_step:
            on_step("plan", "Working out what to look for",
                    "; ".join(need.queries)[:160])

    evidence = _gather.gather(
        need.queries,
        web_mode=budget.mode if budget.mode in ("spark", "zenith") else "spark",
        request_id=request_id,
        max_sources=budget.max_sources,
        max_open=_pages_to_open(budget, subject),
        # The rewrite's cost is NOT taken out of the gathering window.
        # Subtracting it left "news outside ai and tech" with six hundred
        # milliseconds to search after a good rewrite had worked out exactly
        # what to search for, and it found nothing at all. Planning well and
        # then having no time to act on it is the worst of both; the rare
        # rewritten turn simply runs a second longer.
        deadline_s=deadline_s,
        recency_days=need.recency_days,
        on_step=on_step,
    )
    _drop_excluded(evidence, rewritten.avoid)
    evidence.excluded = list(rewritten.avoid)
    return need, evidence


def pack_chars(budget: Budget) -> int:
    """How much of the prompt the gathered evidence may occupy."""
    return max(2500, int(budget.prompt_chars) - _NON_EVIDENCE_CHARS)
