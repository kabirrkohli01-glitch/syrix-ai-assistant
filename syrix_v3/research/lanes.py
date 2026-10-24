"""The individual places SYRIX can search. One function each, no orchestration.

Measured, which is why this module exists: the lanes themselves are fast —
DuckDuckGo answers in about 1.1 seconds, Mojeek in 0.2, the free-web chain in
0.7 — but going through the previous retrieval layer never returned anything
in under four seconds no matter what budget it was handed, because that layer
holds the whole fan-out open for its slowest member. A five-second turn cannot
afford a four-second floor that buys nothing.

So the lanes are here, plainly, and ``gather`` races them and takes what has
arrived. Each one:

* returns ``list[Source]`` and raises on provider failure, so a dead lane is
  visibly dead rather than silently empty
* takes a hard timeout and honours it
* sends an honest User-Agent — every one of these accepts it, verified, and
  none of them is told this is a browser
* has a confidence, used for ranking: an API with an extracted-content index
  outranks a scraped result page, which outranks a discovery headline

Page reads are NOT here. A URL that came from a search result or from the
model is untrusted input and goes through ``syrix_web``'s governed reader,
with its SSRF policy and injection scanning. These endpoints are fixed hosts
this module chose itself.
"""
from __future__ import annotations

import html as _html
import re
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit

from .. import config
from .schemas import Source
import syrix_obs

USER_AGENT = "SYRIX/3.0 (+local personal assistant; respectful)"

_MAX_PER_LANE = 8
# Enough of each page to answer from, small enough that eight fit a prompt.
_EXA_TEXT_CHARS = 3500
_TAG = re.compile(r"<[^>]+>")
_MOJEEK_RESULT = re.compile(
    r'<h2>\s*<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
_MOJEEK_SNIPPET = re.compile(r'<p\s+class="s">(.*?)</p>', re.S | re.I)

# Tracking parameters carry nothing and split one page into several sources.
_JUNK_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "msclkid", "ref", "ref_src", "mc_cid", "mc_eid",
})


def _domain(url: str) -> str:
    try:
        host = (urlsplit(url).hostname or "").lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def clean_url(url: str) -> str:
    """Normalise so the same page found twice is recognised as one source."""
    raw = str(url or "").strip()
    if not raw.startswith(("http://", "https://")):
        return ""
    try:
        parts = urlsplit(raw)
        query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        kept = [(k, v) for k, v in query if k.lower() not in _JUNK_PARAMS]
        return urllib.parse.urlunsplit((
            parts.scheme, parts.netloc, parts.path or "/",
            urllib.parse.urlencode(kept), "",
        ))
    except Exception:
        return raw


# Sponsored results come back as click-tracking redirects on the engine's own
# host. They are adverts, not sources, and cited they appear as "bing.com".
_AD_LINK = re.compile(
    r"^https?://(?:www\.)?(?:bing\.com/aclick|duckduckgo\.com/y\.js|"
    r"[^/]*doubleclick\.net/|[^/]*googleadservices\.com/)", re.I)


# Adult results. An abbreviation is enough to pull them into an ordinary
# search — "coc" for a game guide returned one, and it was then opened as a
# source. They are dropped unless the question itself asks for such content.
_EXPLICIT = re.compile(
    r"(?:porn|xxx|xvideos|xhamster|hentai|onlyfans|camgirl|nsfw|\bmilf\b|"
    r"\bnude|\bnaked\b|\bsex(?:y|ual)? ?(?:videos?|cams?|chat|tube)|"
    r"\bcock\b|\bpussy\b|blowjob|\bfuck|\bescorts?\b|wildhard)", re.I)


def explicit(text: str) -> bool:
    return bool(_EXPLICIT.search(str(text or "")))


def _source(url: str, title: str, snippet: str, provider: str,
            score: float, published: str = "") -> Optional[Source]:
    if _AD_LINK.match(str(url or "").strip()):
        return None
    link = clean_url(url)
    name = " ".join(str(title or "").split())
    if not link and not name:
        return None
    return Source(
        url=link,
        title=name or link,
        snippet=" ".join(str(snippet or "").split())[:400],
        domain=_domain(link),
        provider=provider,
        published=str(published or "").strip(),
        score=score,
    )


_session_lock = threading.Lock()
_session: Any = None


def _http() -> Any:
    """One keep-alive session for the fixed search hosts.

    A fresh connection per call paid a TLS handshake on every search; with
    six to ten searches racing at the start of a turn, the handshakes queued
    behind each other and a lane that answers in 1.2s alone took over 3.5s.
    The pool is sized for every lane of a turn at once.
    """
    global _session
    with _session_lock:
        if _session is None:
            import requests
            from requests.adapters import HTTPAdapter
            session = requests.Session()
            adapter = HTTPAdapter(pool_connections=8, pool_maxsize=16)
            session.mount("https://", adapter)
            _session = session
        return _session


def _get(url: str, *, timeout: float, headers: Optional[Dict[str, str]] = None,
         params: Optional[Dict[str, Any]] = None) -> Any:
    head = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    head.update(headers or {})
    resp = _http().get(url, timeout=max(1.0, timeout), headers=head, params=params)
    resp.raise_for_status()
    return resp


def _post_json(url: str, payload: Dict[str, Any], *, timeout: float,
               headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    head = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
    head.update(headers or {})
    resp = _http().post(url, json=payload, timeout=max(1.0, timeout), headers=head)
    resp.raise_for_status()
    return resp.json()


# ── the lanes ───────────────────────────────────────────────────────────────

def duckduckgo(query: str, *, timeout: float) -> List[Source]:
    """The broadest keyless lane: DuckDuckGo and Bing, whichever answers first."""
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
    from ..compat import ddgs as _ddgs
    DDGS = _ddgs()
    out: List[Source] = []
    # Engines named explicitly: the library's default rotates through every
    # engine it knows, Google and Google-backed Startpage among them. Both are
    # asked at once and the first with results wins, because either can stall
    # — measured, DuckDuckGo throttling to four-second failures under load
    # while Bing answered in under two, and a combined "duckduckgo,bing"
    # request failing outright with Bing's answer discarded.
    def _ask(engine: str) -> List[Dict[str, Any]]:
        with DDGS(timeout=max(1, int(timeout))) as client:
            return client.text(query, max_results=_MAX_PER_LANE, backend=engine) or []

    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="v3-ddg")
    rows: List[Dict[str, Any]] = []
    try:
        pending = {pool.submit(_ask, e) for e in ("duckduckgo", "bing")}
        ends = time.monotonic() + timeout
        while pending and not rows:
            done, pending = wait(pending, timeout=max(0.05, ends - time.monotonic()),
                                 return_when=FIRST_COMPLETED)
            if not done:
                break
            for future in done:
                try:
                    rows = future.result() or rows
                except Exception as exc:
                    syrix_obs.report("v3.research.lanes.duckduckgo", exc, level="debug")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    for row in rows:
        if not isinstance(row, dict):
            continue
        source = _source(
            row.get("href") or row.get("url") or "",
            row.get("title") or "",
            row.get("body") or row.get("snippet") or "",
            "duckduckgo", 0.58,
        )
        if source and source.url:
            out.append(source)
    return out


def mojeek(query: str, *, timeout: float) -> List[Source]:
    """An independent index, so it corroborates rather than echoes."""
    resp = _get("https://www.mojeek.com/search", timeout=timeout,
                params={"q": query}, headers={"Accept": "text/html"})
    page = resp.text
    snippets = [_TAG.sub("", s.group(1)).strip()
                for s in _MOJEEK_SNIPPET.finditer(page)]
    out: List[Source] = []
    for i, match in enumerate(_MOJEEK_RESULT.finditer(page)):
        source = _source(
            match.group(1),
            _html.unescape(_TAG.sub("", match.group(2))),
            _html.unescape(snippets[i]) if i < len(snippets) else "",
            "mojeek", 0.52,
        )
        if source and source.url:
            out.append(source)
        if len(out) >= _MAX_PER_LANE:
            break
    return out


def wikipedia(query: str, *, timeout: float) -> List[Source]:
    """Cheap, fast and authoritative for anything encyclopaedic."""
    data = _get(
        "https://en.wikipedia.org/w/api.php", timeout=timeout,
        params={"action": "query", "list": "search", "srsearch": query,
                "srlimit": 4, "format": "json", "utf8": 1},
    ).json()
    out: List[Source] = []
    for row in ((data.get("query") or {}).get("search") or []):
        title = str(row.get("title") or "")
        if not title:
            continue
        source = _source(
            "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_")),
            title, _TAG.sub("", str(row.get("snippet") or "")),
            "wikipedia", 0.60, published=str(row.get("timestamp") or ""),
        )
        if source:
            out.append(source)
    return out


# Marks a result that is an image for the query rather than a page. Carried
# in the same list so a lane keeps one return type; the gatherer separates
# them before anything ranks or reads the pages.
IMAGE_PROVIDER = "image"


def tavily(query: str, *, timeout: float) -> List[Source]:
    """A real search API with an extracted-content index. Keyed, free tier.

    Also asked for images of the query, which costs no extra request and is
    the best-matched set of pictures any lane returns — they are chosen for
    the query, not for whichever page happened to rank.

    Without image descriptions: Tavily writes those with a model, and
    measured, asking for them took a search from 1.2-2.0s to 5.2s, with one
    request not answering in 15. A caption nobody reads is not worth the
    search arriving too late to be used.
    """
    key = config.secret("TAVILY_API_KEY")
    if not key:
        raise RuntimeError("no TAVILY_API_KEY")
    data = _post_json(
        "https://api.tavily.com/search",
        {"api_key": key, "query": query, "max_results": _MAX_PER_LANE,
         "search_depth": "basic", "include_answer": False,
         "include_images": True},
        timeout=timeout,
    )
    out: List[Source] = []
    for row in (data.get("results") or []):
        source = _source(row.get("url") or "", row.get("title") or "",
                         row.get("content") or "", "tavily", 0.74,
                         published=str(row.get("published_date") or ""))
        if source and source.url:
            out.append(source)
    for row in (data.get("images") or [])[:8]:
        url = row.get("url") if isinstance(row, dict) else row
        if isinstance(url, str) and url.startswith("https://"):
            out.append(Source(url=url, title=str((row.get("description") if isinstance(row, dict) else "") or "")[:200],
                              provider=IMAGE_PROVIDER, domain=_domain(url)))
    return out


def exa(query: str, *, timeout: float) -> List[Source]:
    """Neural search over an independent index. Keyed, free tier.

    The "fast" search type: measured on fresh queries at 0.7s against 1.2-1.6s
    for "auto", with the same number of results carrying page text.
    """
    key = config.secret("EXA_API_KEY")
    if not key:
        raise RuntimeError("no EXA_API_KEY")
    data = _post_json(
        "https://api.exa.ai/search",
        {"query": query, "numResults": _MAX_PER_LANE, "type": "fast",
         "contents": {"text": {"maxCharacters": _EXA_TEXT_CHARS}}},
        timeout=timeout, headers={"x-api-key": key},
    )
    out: List[Source] = []
    for row in (data.get("results") or []):
        text = row.get("text") or ""
        if isinstance(text, dict):
            text = text.get("text") or ""
        source = _source(row.get("url") or "", row.get("title") or "", text,
                         "exa", 0.70, published=str(row.get("publishedDate") or ""))
        if source and source.url:
            # Exa returns the page's main text with the result, so the page
            # does not need opening again: a search that arrives already read
            # is the cheapest evidence there is.
            if len(text) >= 400:
                source.body = text
                source.retrieved_at = time.strftime("%Y-%m-%dT%H:%M:%S")
            image = row.get("image")
            if isinstance(image, str) and image.startswith("https://"):
                source.image = image
            out.append(source)
    return out


def searxng(query: str, *, timeout: float) -> List[Source]:
    """A self-hosted meta-search instance, when one is configured."""
    base = str(config.get("searxng_url", "") or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("no SEARXNG_URL")
    data = _get(f"{base}/search", timeout=timeout,
                params={"q": query, "format": "json"}).json()
    out: List[Source] = []
    for row in (data.get("results") or [])[:_MAX_PER_LANE]:
        source = _source(row.get("url") or "", row.get("title") or "",
                         row.get("content") or "", "searxng", 0.56,
                         published=str(row.get("publishedDate") or ""))
        if source and source.url:
            out.append(source)
    return out


# Ordered by how much a result from each is worth, which is also the order
# their scores encode. Presence of a key decides whether a keyed lane runs;
# an absent key raises, the lane is recorded as unavailable, and nothing else
# changes.
#
# Measured 2026-09-23 on "best laptops for students under 800 pounds": Exa
# 0.8s with page text, DuckDuckGo 1.3s, Tavily 2.2s, Wikipedia 0.7s (and only
# Wikipedia pages). Mojeek answers 403 and the configured public SearXNG
# instance 429, so both stay importable but are not raced by default.
ALL: Dict[str, Callable[..., List[Source]]] = {
    "tavily": tavily,
    "exa": exa,
    "duckduckgo": duckduckgo,
    "wikipedia": wikipedia,
    "mojeek": mojeek,
    "searxng": searxng,
}

# Raced for every question. The keyed lane spends free credit per request, so
# it runs on the main query only; DuckDuckGo covers the variants.
PRIMARY = ("exa", "duckduckgo", "wikipedia")
VARIANT = ("duckduckgo",)
# Tavily stands in for Exa rather than racing beside it. Its free allowance
# is a thousand searches a month, and raced on every turn it had used 834 of
# them when checked, while what it added was equally served by Exa's page
# text and a separate image search. Held in reserve, it covers the
# turns where Exa has no key, has spent its own allowance, or is refusing.
RESERVE = {"exa": "tavily"}
# The lanes that return page text and pictures with their results. Worth a
# short wait past the search window when neither has answered yet.
KEYED = ("exa", "tavily")

# Lanes that return a headline and no openable article. None are raced: a
# headline is a title written to be clicked, not information — asked what
# was happening with Bitcoin, SYRIX once answered with a headline promising
# to name "the one thing" standing in its way, which does not say what it is.
HEADLINE_ONLY: tuple = ()

_NEWS_HINT = re.compile(
    r"\b(news|headline|happened|announced|announcement|released|launch|"
    r"breaking|today|this morning|latest)\b", re.I)


def choose(query: str, *, allow_headlines: bool = False) -> List[str]:
    """Which lanes to race for this query.

    A news question gets the same lanes as any other. Tavily, Exa and
    DuckDuckGo all return real articles for a news query, and an article can
    be read; that is the difference between reporting what happened and
    repeating what a headline promised.
    """
    del allow_headlines
    out: List[str] = []
    for name in PRIMARY:
        if _in_service(name):
            out.append(name)
        elif _in_service(RESERVE.get(name, "")):
            out.append(RESERVE[name])
    return out


_KEYS = {"exa": "EXA_API_KEY", "tavily": "TAVILY_API_KEY"}


def _in_service(name: str) -> bool:
    if not name or name not in ALL:
        return False
    if name in _KEYS and not config.secret(_KEYS[name]):
        return False
    from . import health
    return not health.down(name)


def available() -> Dict[str, bool]:
    """Which lanes have what they need. For diagnostics, not routing."""
    return {
        "tavily": bool(config.secret("TAVILY_API_KEY")),
        "exa": bool(config.secret("EXA_API_KEY")),
        "searxng": bool(str(config.get("searxng_url", "") or "").strip()),
        "duckduckgo": True,
        "wikipedia": True,
        "mojeek": True,
    }
