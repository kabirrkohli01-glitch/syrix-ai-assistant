"""Turning gathered sources into the block the model reads.

Three rules, each of which the previous runtime broke somewhere.

**Every entry carries its date and publisher.** A snippet with no date is
how an answer ends up quoting a 2024 closing price as today's. The model
cannot weigh what it cannot see, so the timestamp travels with the claim,
and where a source carries no date that absence is stated rather than left
blank.

**Opened pages come before snippets.** A page that was actually read is
better evidence than a search summary of it, and the order is what the model
reads first.

**The pack is sized to the lane it is about to be sent to.** Assembling
forty thousand characters for a provider that will silently drop half of
them wastes the fetch and, worse, drops it from the end — where the newest
sources are.
"""
from __future__ import annotations

import re
from typing import List, Optional

from .gather import Evidence, Source

_MIN_BODY_SHARE = 0.65   # of the pack budget, reserved for opened pages
_MIN_BODY_CHARS = 400    # below this an excerpt says nothing useful

_EXCERPT_WORD = re.compile(r"[A-Za-z0-9']+")
# A ranked row: a position, then the thing holding it. What a league table is
# made of, and the shape that survives worst when a page is cut from the top.
_RANKED_ROW = re.compile(r"(?m)^\s*(?:#\s*)?\d{1,3}[.)\]]?\s+\S")
_EXCERPT_STOPWORDS = frozenset(
    "a an the is are was were be of in on for to and or with what which who "
    "how why when where best top most this that it its from by at as".split()
)


def _excerpt(body: str, share: int, query: str) -> str:
    """The most relevant window of a page, not its first N characters.

    Head-truncation assumes the answer is at the top of the page, and on the
    pages where it matters least often is: a league table opens with
    navigation, methodology and cookie text, and puts the ranked rows lower
    down. Measured, a top-ten question opened seven pages and reported of four
    of them that the list "wasn't in the text I could read" — the rows had been
    cut off. Opening more pages made this worse rather than better, because
    every extra page divides the same body budget: seven pages left about 550
    characters each, which is a page's header.

    Scored on query terms and on ranked rows, so this helps a list question
    most and cannot hurt a page whose relevant part IS at the top — that window
    simply wins.
    """
    body = body or ""
    if len(body) <= share:
        return body.rstrip()

    terms = {w.lower() for w in _EXCERPT_WORD.findall(query or "")
             if len(w) > 2 and w.lower() not in _EXCERPT_STOPWORDS}
    step = max(200, share // 4)

    best_at, best_score = 0, -1.0
    for start in range(0, len(body) - share + step, step):
        window = body[start:start + share]
        # Score whole lines only. A row counts by where it STARTS, so a window
        # that clips "10. University of Bristol" after four characters scored
        # identically to one that contained it, and the tie-break toward
        # earlier text then picked the truncated window every time.
        cut = window.rfind("\n")
        scored = window[:cut] if cut > 0 else window
        lowered = scored.lower()
        score = sum(lowered.count(term) for term in terms)
        score += 2.0 * len(_RANKED_ROW.findall(scored))
        # A mild preference for earlier text breaks ties toward the lead
        # paragraph, which is the right answer when nothing else distinguishes
        # the windows.
        score -= start / max(1, len(body)) * 0.5
        if score > best_score:
            best_at, best_score = start, score

    if best_at == 0:
        return body[:share].rstrip()
    # Snap to a line start so a row is never joined to the tail of another.
    snapped = body.rfind("\n", 0, best_at)
    best_at = snapped + 1 if snapped != -1 and best_at - snapped < 200 else best_at
    return "…" + body[best_at:best_at + share].rstrip()


def _when(source: Source) -> str:
    if source.published:
        return source.published
    if source.retrieved_at:
        return f"no publication date; fetched {source.retrieved_at}"
    return "no date given"


_SITE_RULES = [
    "HOW TO USE THIS",
    "",
    "These pages are the site's own words, read moments ago, and they are "
    "what was asked for. Say what the site says — in your own words, naming "
    "the page it is on and citing it like [1]. Quote a phrase at most, never "
    "paragraphs. A site describes itself favourably: report its claims as its "
    "claims (\"Nvidia describes itself as…\"), not as settled fact. If the "
    "exact page asked for was not found, say so in one line and answer from "
    "what was read.",
    "",
]

_PROFILE_RULES = [
    "HOW TO USE THIS",
    "",
    "These figures were read from the platform moments ago: use them exactly "
    "and never estimate one the platform did not show. Asked to review or "
    "rate the profile, judge it the way a social-media strategist would — "
    "what the numbers say (likes per video, followers against following, "
    "posting rhythm from the dates), whether the bio says who it is for and "
    "what to do next, what the recent posts have in common — then give three "
    "to five specific improvements, most valuable first.",
    "",
]


def _destination_header(evidence: Evidence) -> List[str]:
    """The header for a visit to a named site or a read of a profile."""
    if evidence.profile:
        lines = [f"PUBLIC {evidence.profile.upper()} PROFILE READ FOR THIS QUESTION", ""]
    else:
        how = f" — its address from {evidence.site_how}" if evidence.site_how else ""
        lines = [f"WEBSITE VISITED FOR THIS QUESTION: {evidence.site}{how}", "",
                 f"{len(evidence.sources)} page(s) opened on the site itself."]
    if evidence.problems:
        # Unlike a search, where a page that would not open is noise, here
        # the page that could not be read may be the one asked for — so the
        # answer says so, once.
        lines.append("Could not be read, and worth one plain sentence in the answer: "
                     + "; ".join(evidence.problems[:3]))
    lines.append("")
    return lines + (_PROFILE_RULES if evidence.profile else _SITE_RULES)


def _header(evidence: Evidence) -> List[str]:
    if (evidence.site or evidence.profile) and evidence.opened and all(
            s.provider in ("site", "profile") for s in evidence.opened):
        return _destination_header(evidence)
    opened = len(evidence.opened)
    lines = [
        "EVIDENCE GATHERED FOR THIS QUESTION",
        "",
        f"Searched: {'; '.join(evidence.queries)}",
        f"{len(evidence.sources)} sources found, {opened} opened and read in full.",
    ]
    if evidence.truncated:
        lines.append(
            "Gathering was stopped by the turn's deadline. Answer from this; "
            "mention the cut-off only if it leaves part of the question "
            "unanswered — never as a closing caveat on a complete answer."
        )
    if evidence.profile and evidence.problems:
        # The profile asked about was not opened, and what follows is its
        # public trace. That is the one retrieval fact the answer must carry:
        # a summary that reads as though the profile was read is a claim the
        # evidence does not support.
        lines.append(f"THE {evidence.profile.upper()} PROFILE ITSELF WAS NOT OPENED: "
                     + evidence.problems[0] + ". Say so in one sentence, then answer "
                     "from the search results below and attribute them as such.")
    elif evidence.problems:
        lines.append("Could not be used (for your judgement only; not something to "
                     "report in the answer): " + "; ".join(evidence.problems[:6]))
    lines += [
        "",
        "HOW TO USE THIS",
        "",
        "Anything current or specific — a figure, price, date, ranking, "
        "score or quotation — comes from below, cited with its bracketed "
        "number straight after the claim, like [2]. Where the sources "
        "disagree, say so and say which you trust more and why. Where they "
        "do not cover part of the question, answer that part from your own "
        "knowledge, marking anything that may have changed as approximate — "
        "never reply that the sources do not cover it instead of answering.",
        "",
        "A HEADLINE IS NOT AN ANSWER. A title exists to make someone click "
        "it: \"the one thing standing between Bitcoin and a million dollars\" "
        "names no thing. If the question is what happened, say what happened "
        "— who did what, when, the numbers, and why it matters — from the "
        "text of the pages that were opened. Never hand back a title, a "
        "site name or a list of links as though it were the answer. If "
        "nothing was opened and you only have titles, say exactly that.",
        "",
        "IF HE ASKED WHAT IS HAPPENING, tell him what happened. Each item "
        "gets two or three sentences of substance — who did what, the "
        "numbers, and why it matters — attributed to the outlet and dated. "
        "Five things explained is an answer; ten titles listed is a search "
        "results page, and he can get one of those himself.",
        "",
        "THE JUDGEMENT IS STILL YOURS. This constrains the facts, not your "
        "opinion. If you are asked what you would do, whether something is "
        "a good idea, or for a yes or no, then decide — using the evidence "
        "as the input to that decision — and give the reason. \"The sources "
        "do not say whether to buy\" is not an answer to \"would you buy\"; "
        "sources never say that, and being asked is what the judgement is "
        "for.",
        "",
    ]
    return lines


def build(evidence: Evidence, *, max_chars: int, avoid: Optional[List[str]] = None) -> str:
    """Render the evidence block, trimmed to fit ``max_chars``."""
    if not evidence.sources:
        return ""

    head = _header(evidence)
    if avoid:
        # Filtering the sources is not enough on its own. A world-news page
        # carries thirty stories and one of them is about AI, so a page that
        # survives the filter can still put the excluded subject in front of
        # the model — and asked for news outside AI, it duly included the
        # one AI story on the page.
        head.insert(1, "")
        head.insert(2, (
            "HE EXPLICITLY EXCLUDED: " + ", ".join(avoid) + ". Nothing about "
            "those belongs in the answer, even where a source below covers "
            "it alongside other things. Leave it out rather than mentioning "
            "that you left it out."
        ))
    budget = max(600, max_chars - sum(len(line) + 1 for line in head))
    body_budget = int(budget * _MIN_BODY_SHARE)

    opened = evidence.opened
    unopened = [s for s in evidence.sources if not s.opened]

    blocks: List[str] = []
    used = 0

    if opened:
        # A visit's pages are ordered most specific first — the page asked
        # for, then its section, then the homepage — so the first is given
        # the most room. An even split left a course's entry requirements
        # the same 800 characters as the university's homepage.
        visit = bool(evidence.site or evidence.profile)
        weights = ([3.0, 1.5] + [1.0] * len(opened))[:len(opened)] if visit else [1.0] * len(opened)
        for i, source in enumerate(opened, 1):
            share = max(_MIN_BODY_CHARS, int(body_budget * weights[i - 1] / sum(weights)))
            excerpt = _excerpt(source.body, share, " ".join(evidence.queries))
            block = (
                f"[{i}] {source.title or source.url}\n"
                f"    {source.url}\n"
                f"    {source.domain or 'unknown publisher'} · {_when(source)}\n\n"
                f"{excerpt}\n"
            )
            if used + len(block) > budget:
                break
            blocks.append(block)
            used += len(block)

    if unopened:
        listing = ["Found but not opened — titles and search snippets only:", ""]
        # Numbered after every opened page, not after the ones that fitted:
        # the numbers are how the answer's citations are matched to the list
        # shown under it, which is ``citations`` order.
        for i, source in enumerate(unopened, len(opened) + 1):
            entry = (
                f"[{i}] {source.title or source.url}\n"
                f"    {source.url}\n"
                f"    {source.domain or 'unknown publisher'} · {_when(source)}\n"
            )
            if source.snippet:
                entry += f"    {source.snippet}\n"
            if used + len(entry) > budget:
                listing.append(
                    f"    ... and {len(unopened) - (i - len(opened) - 1)} more, "
                    "not listed for space."
                )
                break
            listing.append(entry)
            used += len(entry)
        blocks.append("\n".join(listing))

    if evidence.site_sections:
        # The site's own navigation, so a follow-up — "and their careers
        # page?" — can be opened from here with open_page rather than found
        # again from nothing.
        nav = "Other sections in the site's navigation (open_page can open these): " + "; ".join(
            f"{text} <{url}>" for text, url in evidence.site_sections)
        if used + len(nav) <= budget + 400:
            blocks.append(nav)

    return "\n".join(head + blocks).strip()


def citations(evidence: Evidence) -> List[dict]:
    """The source list the chat line renders, opened pages first."""
    ordered = evidence.opened + [s for s in evidence.sources if not s.opened]
    out = []
    for n, source in enumerate(ordered, 1):
        row = source.as_dict()
        row["n"] = n
        out.append(row)
    return out
