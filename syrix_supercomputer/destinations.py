"""Turning what the user said into somewhere to go.

The old spine refused any request without a literal URL in it, which meant
"open YouTube and find MrBeast's latest video" died before a single page loaded.
Two mechanisms replace that:

**Named services** — a small table of the places a person means by name.
"YouTube" is not a guess, it is youtube.com. Resolving these up front is what
lets the task contract be seeded from the user's own words rather than from a
link some page offered.

**Search** — everything else. The query goes to a real search engine in the real
browser, and the agent reads the results page like a person would. Google is the
default because it is the engine the user is signed into in this profile; the choice
is config, not code.

Nothing here ever reads page content. Destinations come from the user's message
and from this table only, so a page can never introduce one.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, urlparse

# Every inbox message, across all of Gmail's category tabs. See the note on the
# "my inbox" entries below.
_INBOX_ALL = "https://mail.google.com/mail/u/0/#search/in%3Ainbox"

# Spoken/typed name -> canonical URL. Ordered longest-key-first at match time so
# "google docs" wins over "google".
SERVICES: Dict[str, str] = {
    "youtube": "https://www.youtube.com/",
    "yt": "https://www.youtube.com/",
    # "Open Gmail" means the app, so it lands where a person expects.
    "gmail": "https://mail.google.com/",
    "google mail": "https://mail.google.com/",
    # "My inbox" / "my emails" means everything that arrived, and Gmail's
    # default view shows only the Primary category tab. A run in July 2026 read
    # Primary, found nothing, and reported the whole inbox as empty — Promotions
    # and Social were never looked at. This search view lists every inbox
    # message across all categories, with no tabs to miss.
    "my email": _INBOX_ALL,
    "my emails": _INBOX_ALL,
    "my inbox": _INBOX_ALL,
    "inbox": _INBOX_ALL,
    "outlook": "https://outlook.live.com/mail/",
    "google docs": "https://docs.google.com/document/u/0/",
    "google doc": "https://docs.google.com/document/u/0/",
    "docs": "https://docs.google.com/document/u/0/",
    "google sheets": "https://docs.google.com/spreadsheets/u/0/",
    "google sheet": "https://docs.google.com/spreadsheets/u/0/",
    "sheets": "https://docs.google.com/spreadsheets/u/0/",
    "google slides": "https://docs.google.com/presentation/u/0/",
    "google drive": "https://drive.google.com/",
    "drive": "https://drive.google.com/",
    "google calendar": "https://calendar.google.com/",
    "calendar": "https://calendar.google.com/",
    "google maps": "https://www.google.com/maps",
    "maps": "https://www.google.com/maps",
    "reddit": "https://www.reddit.com/",
    "twitter": "https://x.com/",
    "x": "https://x.com/",
    "instagram": "https://www.instagram.com/",
    "linkedin": "https://www.linkedin.com/feed/",
    "github": "https://github.com/",
    "wikipedia": "https://en.wikipedia.org/",
    "amazon": "https://www.amazon.co.uk/",
    "netflix": "https://www.netflix.com/",
    "spotify": "https://open.spotify.com/",
    "chatgpt": "https://chatgpt.com/",
    "claude": "https://claude.ai/",
    "stack overflow": "https://stackoverflow.com/",
    "hacker news": "https://news.ycombinator.com/",
    "bbc": "https://www.bbc.co.uk/news",
    "google news": "https://news.google.com/",
}

# Domains a task should be able to reach whenever it may search at all. Stored
# without the www. prefix, because host_matches treats a stored domain as
# covering its sub-domains — "google.com" covers "www.google.com", but not the
# other way round.
SEARCH_DOMAINS = ("google.com", "duckduckgo.com", "bing.com")

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
_BARE_DOMAIN_RE = re.compile(
    r"\b((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+"
    r"(?:com|org|net|io|dev|ai|co|uk|edu|gov|news|app|me|tv|fm|xyz|so|sh))\b",
    re.IGNORECASE,
)
# Sorted once so the longest service name is tried first.
_SERVICE_KEYS = sorted(SERVICES, key=len, reverse=True)


def search_url(query: str, engine: str = "google") -> str:
    """The URL for a query on the configured engine."""
    q = quote_plus(str(query or "").strip())
    if not q:
        return ""
    eng = str(engine or "google").strip().lower()
    if eng == "duckduckgo":
        return f"https://duckduckgo.com/?q={q}"
    if eng == "bing":
        return f"https://www.bing.com/search?q={q}"
    return f"https://www.google.com/search?q={q}"


def engine_for(config: Optional[Dict[str, Any]] = None) -> str:
    return str((config or {}).get("supercomputer_search_engine", "google") or "google").lower()


def named_services(text: str) -> List[str]:
    """Canonical URLs for services the user named, in the order they appear.

    Matched on word boundaries so "next" does not resolve "x" and "docs" only
    matches the standalone word.
    """
    low = f" {str(text or '').lower()} "
    hits: List[tuple] = []
    for key in _SERVICE_KEYS:
        m = re.search(r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])", low)
        if m:
            url = SERVICES[key]
            if url not in [h[1] for h in hits]:
                hits.append((m.start(), url))
    hits.sort(key=lambda h: h[0])
    return [h[1] for h in hits]


def explicit_urls(text: str) -> List[str]:
    """URLs and bare domains typed by the user, in order, de-duplicated."""
    out: List[str] = []
    for m in _URL_RE.finditer(text or ""):
        url = m.group(0).rstrip(".,;:!?")
        if url not in out:
            out.append(url)
    for m in _BARE_DOMAIN_RE.finditer(text or ""):
        url = "https://" + m.group(1)
        if url not in out and not any(m.group(1) in u for u in out):
            out.append(url)
    return out


def resolve(text: str) -> List[str]:
    """Everywhere the user's message points, most explicit first.

    Explicit URLs beat named services: if the user pasted a link *and* said
    "youtube", the link is what was meant.
    """
    out = explicit_urls(text)
    for url in named_services(text):
        if url not in out:
            out.append(url)
    return out[:6]


def host_of(url: str) -> str:
    """Host of a URL with any leading ``www.`` removed.

    Note the removal is a prefix strip, not ``lstrip`` — ``lstrip("www.")``
    strips a *character set* and would turn "wow.example" into "example".
    """
    try:
        host = (urlparse(str(url or "")).hostname or "").lower()
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


def scope_host(url_or_host: str) -> str:
    """The form a host should be stored in a contract as.

    ``www.`` is dropped so the stored domain covers the bare host and its
    sub-domains (host_matches is one-directional: "youtube.com" covers
    "www.youtube.com", but "www.youtube.com" covers neither). Deeper
    sub-domains are kept exactly as they are — approving mail.google.com must
    not quietly approve all of google.com.
    """
    from .contract import normalise_host

    host = normalise_host(url_or_host)
    return host[4:] if host.startswith("www.") else host


def seed_domains(text: str, config: Optional[Dict[str, Any]] = None) -> List[str]:
    """Registrable hosts to seed a task contract with.

    The user's destinations plus the search engine, because a task that may
    look things up must be able to reach the engine it looks them up on.
    """
    out: List[str] = []
    for url in resolve(text):
        host = scope_host(url)
        if host and host not in out:
            out.append(host)
    for host in SEARCH_DOMAINS:
        if host not in out:
            out.append(host)
    return out


__all__ = [
    "SERVICES", "SEARCH_DOMAINS", "search_url", "engine_for", "named_services",
    "explicit_urls", "resolve", "seed_domains", "host_of", "scope_host",
]
