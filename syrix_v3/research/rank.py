"""Deciding which sources are worth the question, and which to open first.

Ordering by which lane found something is not ranking. Left at that, an
encyclopaedia entry outranks a live quote page because the encyclopaedia
lane carries a higher prior — and SYRIX opened "History of Bitcoin" and
"Bitcoin Cash" in answer to "what is the price of Bitcoin right now", then
correctly reported that neither contained a price. The retrieval was honest
and the ranking was useless.

Four signals, each cheap and each explainable:

**Relevance.** How much of the question appears in the title and the
snippet. Titles count for more than snippets because a page about the
subject says so in its title.

**The lane's prior.** An API with an extracted-content index is a better bet
than a scraped result page. It breaks ties; it does not decide.

**Recency fit.** A question about right now cannot be answered by a source
with no date, and it is actively misled by one that is years old.

**Staleness by nature.** Some sources are excellent and structurally
incapable of carrying today's number. An encyclopaedia article is the
clearest case: authoritative about what a thing is, silent on what it costs
this afternoon.
"""
from __future__ import annotations

import math
import re
import time
from email.utils import parsedate_to_datetime
from typing import Iterable, List, Sequence

from .schemas import Source

_WORD = re.compile(r"[A-Za-z0-9$£€.+#-]{2,}")

_STOPWORDS = frozenset(
    "a an the is are was were be been being do does did what whats who whom "
    "whose which when where why how to of for in on at by with from and or "
    "but if then than that this these those it its me my your you i we us "
    "can could would should will shall may might must about into over under "
    "right now tell give show explain latest current today please".split()
)

# Authoritative about what a thing is; structurally unable to carry what it
# costs right now. Downranked only when the question is time-bound.
_ENCYCLOPAEDIC = ("wikipedia.org", "wikiwand.com", "britannica.com",
                  "simple.wikipedia.org", "dbpedia.org")

# Platforms whose pages match a query's wording without carrying a checkable
# fact. Demoted for every question, not only current ones — a league table does
# not become more reliable for being on TikTok.
_SOCIAL = ("tiktok.com", "youtube.com", "youtu.be", "instagram.com",
           "facebook.com", "pinterest.com", "x.com", "twitter.com",
           "threads.net", "snapchat.com", "quora.com")

# A section front rather than a story. Asked what the news was, SYRIX
# returned "AI News & Artificial Intelligence – TechCrunch", "AI (artificial
# intelligence) - The Guardian" and "Artificial intelligence – Latest News
# & Updates – BBC News" — three index pages whose titles are the name of a
# section and whose bodies are lists of other headlines. They rank well
# because the query terms are in the title, and they carry no story at all.
# Path segments that only ever appear on a listing.
_INDEX_SEGMENTS = frozenset(
    "category categories tag tags topic topics section sections archive "
    "archives latest index feed rss author authors".split()
)
_YEAR = re.compile(r"^(?:19|20)\d{2}$")
_HAS_DIGITS = re.compile(r"\d")


def looks_like_an_index(source: Source) -> bool:
    """Whether this is a section front rather than a story.

    Decided on the URL's shape rather than a list of publishers, because
    every publisher invents its own. A story is identifiable: its address
    carries a date, or a slug made of the words in its headline, or a
    numeric id. A section front carries the name of the section and
    nothing else — ``/technology/artificialintelligenceai`` and
    ``/category/artificial-intelligence/`` are both pages of links.
    """
    from urllib.parse import urlsplit
    try:
        path = urlsplit(source.url).path or "/"
    except Exception:
        return False

    segments = [p for p in path.split("/") if p]
    if not segments:
        return True
    if any(s.lower() in _INDEX_SEGMENTS for s in segments):
        return True
    if any(_YEAR.match(s) for s in segments):
        return False   # dated, so it is an article

    last = segments[-1]
    # A headline slug: three or more words joined by hyphens.
    if last.count("-") >= 2:
        return False
    # An article id: long and containing digits.
    if len(last) >= 8 and _HAS_DIGITS.search(last):
        return False
    return True


def terms(text: str) -> List[str]:
    return [w.lower() for w in _WORD.findall(str(text or ""))
            if w.lower() not in _STOPWORDS]


def _overlap(query_terms: Sequence[str], text: str) -> float:
    """Share of the question's terms that appear in ``text``."""
    if not query_terms:
        return 0.0
    haystack = set(terms(text))
    if not haystack:
        return 0.0
    hits = sum(1 for t in query_terms if t in haystack)
    return hits / len(query_terms)


def _age_days(published: str) -> float:
    """Age in days, or -1 when the source carries no usable date."""
    raw = str(published or "").strip()
    if not raw:
        return -1.0
    for parse in (_parse_rfc2822, _parse_iso):
        stamp = parse(raw)
        if stamp is not None:
            return max(0.0, (time.time() - stamp) / 86400.0)
    return -1.0


def _parse_rfc2822(raw: str):
    try:
        return parsedate_to_datetime(raw).timestamp()
    except Exception:
        return None


def _parse_iso(raw: str):
    from datetime import datetime
    cleaned = raw.replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d", "%Y/%m/%d", "%d %B %Y", "%B %d, %Y"):
        try:
            if fmt is None:
                return datetime.fromisoformat(cleaned).timestamp()
            return datetime.strptime(raw[:len(time.strftime(fmt))], fmt).timestamp()
        except Exception:
            continue
    return None


_WANTS_ARTICLES = re.compile(
    r"\b(news|headlines?|happening|happened|going on|stories|"
    r"announced|announcement|latest on|developments?|updates?)\b", re.I)


def wants_articles(query: str) -> bool:
    """Whether a listing page is the wrong shape of answer for this question.

    It usually is, for "what is happening" — a section front is a page of
    links. It is not for "what is the price": a quote page is structurally
    a listing and is exactly the right answer.
    """
    return bool(_WANTS_ARTICLES.search(str(query or "")))


def score(source: Source, *, query_terms: Sequence[str], recency_days: int,
          penalise_index: bool = False) -> float:
    title_fit = _overlap(query_terms, source.title)
    snippet_fit = _overlap(query_terms, source.snippet)
    relevance = 0.70 * title_fit + 0.30 * snippet_fit

    # Relevance leads. A source that does not mention what was asked about
    # cannot be rescued by the reputation of the lane that found it.
    total = 0.62 * relevance + 0.38 * min(1.0, source.score)

    if recency_days:
        age = _age_days(source.published)
        if age < 0:
            # Undated. Not disqualifying — most pages carry no date — but it
            # cannot be preferred for a question about right now.
            total -= 0.06
        else:
            # Full credit inside the window, decaying smoothly after it
            # rather than falling off a cliff.
            total += 0.18 * math.exp(-max(0.0, age - recency_days) / max(1.0, recency_days))
        if any(source.domain.endswith(d) for d in _ENCYCLOPAEDIC):
            total -= 0.30

    if any(source.domain.endswith(d) for d in _SOCIAL):
        # A short-video or social page can match a factual query's wording
        # perfectly and contain no checkable fact. Measured live: asked for the
        # top ten UK universities, "Uk Top 10 Unis in The World | TikTok"
        # ranked alongside Times Higher Education; asked why a depot missed its
        # target, four of the top six were YouTube and TikTok. Penalised rather
        # than dropped, because for a question genuinely ABOUT one of these
        # platforms it is the right source — relevance still has to carry it.
        total -= 0.40

    if not source.url:
        # A headline with no article behind it. Kept, because it still says
        # something happened, but never preferred over a page that can be
        # opened and quoted.
        total -= 0.35

    if penalise_index and looks_like_an_index(source):
        # A section front matches the query terms perfectly and contains no
        # story. Heavily penalised rather than dropped, because when a
        # publisher is the only source for something the index is still a
        # pointer worth having.
        total -= 0.45

    return round(total, 4)


def order(sources: Iterable[Source], *, query: str, recency_days: int = 0) -> List[Source]:
    """Rank in place and return best first."""
    query_terms = terms(query)
    penalise = wants_articles(query)
    ranked = list(sources)
    for source in ranked:
        source.score = score(source, query_terms=query_terms,
                             recency_days=recency_days, penalise_index=penalise)
    ranked.sort(key=lambda s: -s.score)
    return ranked


def pick_to_open(sources: Sequence[Source], limit: int) -> List[Source]:
    """Which pages to spend the read budget on.

    Best first, but never more than two from one publisher: two pages from
    the same site usually say the same thing, and the budget is better spent
    on a second opinion than on a second helping.
    """
    chosen: List[Source] = []
    per_domain: dict = {}
    for source in sources:
        if not source.url:
            continue
        seen = per_domain.get(source.domain, 0)
        if source.domain and seen >= 2:
            continue
        per_domain[source.domain] = seen + 1
        chosen.append(source)
        if len(chosen) >= limit:
            break
    return chosen
