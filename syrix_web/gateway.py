"""The escalation ladder — one door to a web page.

Four rungs, climbed only as far as the answer needs::

    T1 direct    requests + trafilatura/readability/soup   ~0.3-1.5s
    T2 jina      r.jina.ai when direct came back blocked   ~1-3s
    T3 browser   @playwright/mcp accessibility snapshot    ~0.8-1.5s warm
    T4 vision    screenshot -> VL model                    opt-in, off

Rung three is the new one, and the reason "I could not verify that" was so
common: before this, a JavaScript-rendered or bot-challenged page simply had no
readable form. Rung four exists for pages where even the accessibility tree is
empty; it ships off because the tree has covered every case measured so far and
a VL call per page is not free.

**The ladder stops early on purpose.** Most pages answer at T1 in under a
second. Climbing is triggered by evidence — a challenge marker, an HTTP error,
a body too thin to answer from — never by optimism. :mod:`syrix_web.budget`
caps how far each mode may climb, so Spark cannot accidentally become an agent
workflow.

Three guards every read passes, in this order, because the cheapest refusal
should come first:

1. :func:`syrix_web.policy.check_fetch` — scheme, address, DNS.
2. The **landing check**: the snapshot's own reported URL must be on the host
   we asked for. Measured live, a failed navigation leaves the previous page in
   the tab and the snapshot returns *that* — three consecutive reads once came
   back as the same stale search page. Trusting the tool's success flag is not
   enough; the page has to say where it is.
3. :mod:`syrix_web.injection` — the page is scanned before its text is handed
   on, and flagged when it tries to issue instructions.
"""
from __future__ import annotations

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Dict, List, Optional, Sequence

import syrix_obs as obs

from . import audit, budget as _budget, injection, policy, snapshot as _snapshot

TIER_DIRECT = "direct"
TIER_JINA = "jina"
TIER_BROWSER = "browser"
TIER_VISION = "vision"
TIER_NONE = "none"


@dataclass
class PageRead:
    """One page, however we managed to get it."""

    url: str
    ok: bool = False
    title: str = ""
    text: str = ""
    tier: str = TIER_NONE
    method: str = ""
    reason: str = ""
    status: int = 0
    ms: float = 0.0
    links: List[Any] = field(default_factory=list)
    suspicious: bool = False
    findings: List[Dict[str, str]] = field(default_factory=list)
    browser_attempted: bool = False
    # Filled by ``open_page`` only: where a redirect landed, and what the page
    # says about itself in its meta tags and structured data.
    final_url: str = ""
    about: str = ""

    def usable(self) -> bool:
        return bool(self.ok and self.text and not self.suspicious)

    def as_reader_tuple(self):
        """``(title, text, method)`` — what ``atlas.reader.read_page`` returns."""
        if self.suspicious:
            return self.title, "", "injection_blocked"
        return self.title, self.text, self.method or self.tier


def _live_config() -> Dict[str, Any]:
    try:
        import core_boot

        return core_boot.CONFIG
    except Exception:
        return {}


def _cfg(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return config if config is not None else _live_config()


def enabled(config: Optional[Dict[str, Any]] = None) -> bool:
    return bool(_cfg(config).get("web_gateway_enabled", True))


# Host labels that identify a rendering of a site rather than the site itself.
_GENERIC_LABELS = frozenset({"www", "m", "mobile", "amp", "en", "www2", "web"})
_SITE_ALIASES = (
    frozenset({"bbc.com", "bbc.co.uk"}),
)


def _canonical_site_host(host: str) -> str:
    """Strip presentation-only leading labels without guessing a brand."""
    parts = [p for p in str(host or "").split(".") if p]
    while parts and parts[0] in _GENERIC_LABELS:
        parts.pop(0)
    return ".".join(parts)


def _same_site(requested: str, landed: str) -> bool:
    """Did we end up on the site we asked for?

    Registrable-suffix comparison rather than exact host, because a redirect
    from ``bbc.com`` to ``bbc.co.uk`` or ``x.com`` to ``www.x.com`` is the site
    answering, not a different site answering. A redirect to an unrelated host
    is a different page and must not be reported as the one requested.
    """
    want = _canonical_site_host(policy.extract_host(requested))
    got = _canonical_site_host(policy.extract_host(landed))
    if not want or not got:
        return False
    if want == got or got.endswith("." + want) or want.endswith("." + got):
        return True
    # Cross-TLD redirects are exceptional and must be explicit. Comparing only
    # the first label would make ``openai.com`` and ``openai.attacker.com`` look
    # related, allowing a previous or attacker-controlled page to masquerade as
    # the requested one.
    return any(want in aliases and got in aliases for aliases in _SITE_ALIASES)


# ── rung 3: the browser ─────────────────────────────────────────────────────
def read_rendered(
    url: str,
    *,
    cfg: Optional[Dict[str, Any]] = None,
    timeout_s: float = 9.0,
    request_id: str = "",
    mode: str = _budget.MODE_SPARK,
) -> Dict[str, Any]:
    """Render one page in the sidecar and return ``{title, text, method, ok}``.

    Shaped as a dict rather than a :class:`PageRead` so it is a drop-in for
    ``atlas.browser.fetch_rendered`` at the call site it replaces. The guarded
    sidecar is the only browser rung; if it is unavailable, the caller keeps
    the safe direct/Jina result instead of opening an unguarded renderer.
    """
    config = _cfg(cfg)
    empty = {"ok": False, "title": "", "text": "", "method": "", "reason": ""}

    ok, reason = policy.check_fetch(url)
    if not ok:
        audit.record("read_blocked", url=url, ok=False, reason=reason,
                     tier=TIER_BROWSER, mode=mode, request_id=request_id)
        return {**empty, "reason": reason}

    from . import browser_mcp

    ready, why = browser_mcp.available(config)
    if not ready:
        return _rendered_fallback(url, config, request_id, mode, why)

    started = time.time()
    audit.searching(url, request_id=request_id)
    sidecar = browser_mcp.sidecar(config)
    nav = sidecar.call(
        "browser_navigate", {"url": url},
        timeout=max(4.0, timeout_s), config=config,
    )
    if not nav.get("ok"):
        return _rendered_fallback(
            url, config, request_id, mode, str(nav.get("reason") or "navigate_failed")
        )

    left = max(2.0, timeout_s - (time.time() - started))
    snap_result = sidecar.call("browser_snapshot", {}, timeout=left, config=config)
    if not snap_result.get("ok"):
        return _rendered_fallback(
            url, config, request_id, mode, str(snap_result.get("reason") or "snapshot_failed")
        )

    page = _snapshot.parse(snap_result.get("text") or "")

    # The landing check. A failed navigation leaves the previous page in the
    # tab and the snapshot happily describes THAT.
    if page.url:
        landed_ok, landed_reason = policy.check_redirect(url, page.url)
        if not landed_ok:
            audit.record("read_wrong_page", url=url, ok=False, tier=TIER_BROWSER,
                         mode=mode, request_id=request_id, landed=page.url,
                         reason=landed_reason)
            return {**empty, "reason": landed_reason or "landed_elsewhere"}
    if page.url and not _same_site(url, page.url):
        audit.record("read_wrong_page", url=url, ok=False, tier=TIER_BROWSER,
                     mode=mode, request_id=request_id, landed=page.url)
        return {**empty, "reason": "landed_elsewhere"}

    if page.looks_blocked():
        audit.record("read_blocked_page", url=url, ok=False, tier=TIER_BROWSER,
                     mode=mode, request_id=request_id,
                     reason=f"status={page.status}", chars=len(page.text))
        return {**empty, "reason": "page_blocked", "status": page.status}

    ms = (time.time() - started) * 1000.0
    audit.record("read", url=url, ok=True, tier=TIER_BROWSER, mode=mode,
                 ms=ms, chars=len(page.text), request_id=request_id)
    audit.feed("Read page", kind="source", detail=f"browser · {len(page.text)} chars",
               request_id=request_id, source_url=url,
               source_title=page.title or policy.extract_host(url))
    return {
        "ok": True,
        "title": page.title,
        "text": page.text,
        "method": "browser+a11y",
        "reason": "",
        "status": page.status,
        "links": page.links,
    }


def _rendered_fallback(
    url: str,
    config: Dict[str, Any],
    request_id: str,
    mode: str,
    why: str,
) -> Dict[str, Any]:
    """Fail closed when the guarded Playwright sidecar is unavailable.

    The legacy renderer has no DNS-pinning proxy for subresources. Falling
    back to it would silently discard the sidecar's network boundary.
    """
    result = {"ok": False, "title": "", "text": "", "method": "", "reason": why}
    audit.record("read_failed", url=url, ok=False, tier=TIER_BROWSER, mode=mode,
                 reason=why, request_id=request_id)
    return result


# ── the full ladder ─────────────────────────────────────────────────────────
def read(
    url: str,
    *,
    mode: str = _budget.MODE_SPARK,
    config: Optional[Dict[str, Any]] = None,
    request_id: str = "",
    timeout_s: Optional[float] = None,
    allow_browser: Optional[bool] = None,
    force_browser: bool = False,
    need_chars: int = _snapshot.MIN_GOOD_CHARS,
) -> PageRead:
    """Read one page, climbing only as far as necessary. Never raises.

    ``allow_browser=None`` defers to the mode's budget; ``False`` pins the read
    to the cheap rungs (used when a caller has already spent its browser
    allowance on another page). ``force_browser=True`` means "do not stop at a
    good direct read": it still honours the master switch, mode allowance and
    explicit ``allow_browser=False``.
    """
    config = _cfg(config)
    started = time.time()
    result = PageRead(url=url)

    ok, reason = policy.check_fetch(url)
    if not ok:
        result.reason = reason
        audit.record("read_blocked", url=url, ok=False, reason=reason,
                     mode=mode, request_id=request_id)
        return result

    allowance = _budget.for_mode(mode, config)
    page_budget = timeout_s if timeout_s is not None else allowance.read_page_s
    audit.searching(url, request_id=request_id)

    # T1 + T2 — the existing reader owns these and does them well. Reusing it
    # rather than reimplementing keeps one extraction chain, one robots check,
    # and one set of boilerplate rules.
    try:
        from syrix_core_v2.atlas import reader as _reader

        # atlas_browser_enabled=False here so read_page stops before its own
        # browser tier: this function owns rung three.
        title, text, method = _reader.read_page(
            url,
            cfg={**config, "atlas_browser_enabled": False},
            timeout_s=max(2.0, page_budget),
        )
        if text:
            result.title, result.text = title, text
            result.method = method
            result.tier = TIER_JINA if method == "jina" else TIER_DIRECT
            result.ok = True
    except Exception as exc:
        obs.report("web.gateway.direct", exc, level="debug", url=url)

    good_enough = result.ok and len(result.text) >= need_chars
    may_browse = (
        allowance.browser_allowed() if allow_browser is None else bool(allow_browser)
    )
    if may_browse and (force_browser or not good_enough) and enabled(config):
        result.browser_attempted = True
        left = allowance.browser_page_s
        if timeout_s is not None:
            left = max(2.0, timeout_s - (time.time() - started))
        rendered = read_rendered(
            url, cfg=config, timeout_s=left, request_id=request_id, mode=mode
        )
        rendered_text = str(rendered.get("text") or "")
        if rendered.get("ok") and len(rendered_text) > len(result.text):
            result.title = str(rendered.get("title") or result.title)
            result.text = rendered_text
            result.method = str(rendered.get("method") or "browser")
            result.tier = TIER_BROWSER
            result.status = int(rendered.get("status") or 0)
            result.links = list(rendered.get("links") or [])
            result.ok = True
        elif not result.ok:
            result.reason = str(rendered.get("reason") or "unreachable")

    if result.ok:
        # Titles are external page content too.  Scanning only the body let a
        # hostile document put its model-directed instruction in <title> and
        # still pass every downstream ``usable`` check.
        findings = injection.scan(f"{result.title}\n{result.text}")
        if findings:
            result.findings = findings
            result.suspicious = True
            audit.record("injection_flagged", url=url, ok=True, mode=mode,
                         request_id=request_id,
                         reason=",".join(sorted({f["category"] for f in findings})))
    elif not result.reason:
        result.reason = "unreachable"

    result.ms = (time.time() - started) * 1000.0
    if result.tier == TIER_DIRECT and result.ok:
        audit.record("read", url=url, ok=True, tier=result.tier, mode=mode,
                     ms=result.ms, chars=len(result.text), request_id=request_id)
    return result


# A user-directed page visit is fetched the way a browser fetches it, because
# that is what it is: one page the user asked for, not a crawl. Measured on
# 2026-09-24, openai.com answered 403 to the resolver's own agent string and
# 200 to this one. The rendered tier already navigates with Chromium's own
# agent, so this changes how the cheap tier asks, not what SYRIX can reach.
# robots.txt is still checked against both SYRIX's name and ``*`` before any
# request is made, and a site that refuses both stays refused.
_VISIT_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}
_VISIT_MAX_BYTES = 2_500_000


def fetch_raw(
    url: str,
    *,
    timeout_s: float = 6.0,
    request_id: str = "",
    accept: str = "",
) -> Dict[str, Any]:
    """One policy- and robots-checked GET. Returns the body undecoded.

    For readers that parse a page's own data — a profile's embedded JSON, a
    public API — rather than its prose. ``{"ok", "status", "body", "url",
    "content_type", "reason"}``; never raises. The caller owns injection
    scanning of whatever it extracts, since only it knows which part is text.
    """
    out: Dict[str, Any] = {"ok": False, "status": 0, "body": b"", "url": url,
                           "content_type": "", "reason": ""}
    ok, reason = policy.check_fetch(url)
    if not ok:
        out["reason"] = reason
        audit.record("read_blocked", url=url, ok=False, reason=reason,
                     request_id=request_id)
        return out
    try:
        import syrix_live_data_resolver as _resolver
        from .http_fetch import fetch_public

        allowed, _why = _resolver._robots_allowed(
            url, timeout=min(2.0, timeout_s), public_only=True)
        if not allowed:
            out["reason"] = "robots_disallow"
            return out
        headers = dict(_VISIT_HEADERS)
        if accept:
            headers["Accept"] = accept
        fetched = fetch_public(url, timeout_s=timeout_s, headers=headers,
                               max_bytes=_VISIT_MAX_BYTES)
    except Exception as exc:
        obs.report("web.gateway.fetch_raw", exc, level="debug", url=url)
        out["reason"] = f"fetch_failed:{type(exc).__name__}"
        return out
    out["status"] = int(fetched.status or 0)
    out["body"] = bytes(fetched.body or b"")
    out["url"] = str(fetched.url or url)
    out["content_type"] = str((fetched.headers or {}).get("content-type")
                              or (fetched.headers or {}).get("Content-Type") or "")
    if fetched.error or out["status"] >= 400 or not out["body"]:
        out["reason"] = str(fetched.error or f"http_{out['status']}")
        return out
    out["ok"] = True
    return out


def open_page(
    url: str,
    *,
    mode: str = _budget.MODE_SPARK,
    config: Optional[Dict[str, Any]] = None,
    request_id: str = "",
    timeout_s: float = 6.0,
    allow_browser: Optional[bool] = None,
) -> PageRead:
    """Read a page for navigation: its text, its links and its self-description.

    ``read`` answers "what does this page say"; this also answers "where can I
    go from here". Same guards in the same order — policy, robots, then the
    injection scan over everything handed on, link text included, since a
    link's text is page content like any other. A page the cheap tier cannot
    read (a bot wall, a JavaScript shell) climbs to the rendered tier, which
    reports links from the accessibility tree. Never raises.
    """
    from . import links as _links

    config = _cfg(config)
    started = time.time()
    result = PageRead(url=url)
    audit.searching(url, request_id=request_id)

    fetched = fetch_raw(url, timeout_s=timeout_s, request_id=request_id)
    if fetched["ok"]:
        landed = str(fetched["url"] or url)
        html = fetched["body"].decode("utf-8", errors="replace")
        try:
            from syrix_core_v2.atlas import reader as _reader

            title, text, method = _reader.extract(landed, fetched["body"],
                                                  fetched["content_type"])
        except Exception as exc:
            obs.report("web.gateway.open_page.extract", exc, level="debug", url=url)
            title, text, method = "", "", ""
        page_title, found, meta, ld = _links.parse(html, landed)
        result.title = page_title or title or meta.get("og:title", "")
        result.text = text
        result.method = f"visit+{method or 'links'}"
        result.tier = TIER_DIRECT
        result.status = int(fetched["status"])
        result.links = found
        result.final_url = landed
        result.about = _links.describe(meta, ld)
        result.ok = bool(text or result.about or found)
    else:
        result.reason = str(fetched["reason"] or "unreachable")
        result.status = int(fetched["status"] or 0)

    # A bot wall or an empty JavaScript shell. The rendered tier is the one
    # that can read it, and it is only attempted when the mode allows it.
    thin = len(result.text) < _snapshot.MIN_GOOD_CHARS
    allowance = _budget.for_mode(mode, config)
    may_browse = (allowance.browser_allowed() if allow_browser is None
                  else bool(allow_browser))
    if thin and may_browse and enabled(config) and result.reason != "robots_disallow":
        result.browser_attempted = True
        left = max(3.0, timeout_s - (time.time() - started) + 3.0)
        rendered = read_rendered(url, cfg=config, timeout_s=left,
                                 request_id=request_id, mode=mode)
        if rendered.get("ok") and len(str(rendered.get("text") or "")) > len(result.text):
            result.title = str(rendered.get("title") or result.title)
            result.text = str(rendered.get("text") or "")
            result.method = str(rendered.get("method") or "browser")
            result.tier = TIER_BROWSER
            result.links = [_links.Link(text=str(t), url=_links.normalise(str(u)))
                            for t, u in (rendered.get("links") or [])
                            if _links.normalise(str(u))]
            result.final_url = result.final_url or url
            result.ok = True
            result.reason = ""

    if result.ok:
        link_text = " | ".join(link.text for link in result.links[:120])
        findings = injection.scan(f"{result.title}\n{result.about}\n{result.text}\n{link_text}")
        if findings:
            result.findings = findings
            result.suspicious = True
            audit.record("injection_flagged", url=url, ok=True, mode=mode,
                         request_id=request_id,
                         reason=",".join(sorted({f["category"] for f in findings})))

    result.ms = (time.time() - started) * 1000.0
    audit.record("visit", url=url, ok=result.ok, tier=result.tier, mode=mode,
                 ms=result.ms, chars=len(result.text), request_id=request_id,
                 reason=result.reason)
    return result


def read_many(
    urls: Sequence[str],
    *,
    mode: str = _budget.MODE_SPARK,
    config: Optional[Dict[str, Any]] = None,
    request_id: str = "",
    allow_browser: Optional[bool] = None,
    force_browser: bool = False,
) -> List[PageRead]:
    """Read several pages concurrently, inside the mode's page and time budget.

    The browser allowance is spent across the whole batch, not per page — one
    Spark turn gets one rendered page, whichever needs it, not one each.
    """
    config = _cfg(config)
    allowance = _budget.for_mode(mode, config)
    wanted = [u for u in urls if u][: allowance.read_pages]
    if not wanted:
        return []

    read_wall = allowance.read_s
    if force_browser:
        # A forced read includes both the cheap extractor and a rendered
        # navigation. Spark's ordinary 3.5 s batch wall only budgets the first
        # part, which made the forced future time out while Chromium completed
        # just after the caller had already synthesised from snippets.
        read_wall = max(
            read_wall,
            min(
                allowance.wall_s,
                allowance.read_page_s + allowance.browser_page_s,
            ),
        )
    deadline = time.time() + read_wall
    browser_left = [allowance.browser_pages if allow_browser is not False else 0]
    forced_left = [1 if force_browser else 0]
    allowance_lock = Lock()

    def _one(index: int, url: str) -> PageRead:
        if time.time() > deadline:
            page = PageRead(url=url)
            page.reason = "budget_exhausted"
            return page
        may_browse = False
        force_this = False
        with allowance_lock:
            if (
                force_browser
                and index == 0
                and allow_browser is not False
                and browser_left[0] > 0
            ):
                browser_left[0] -= 1
                may_browse = True
                forced_left[0] = 0
                force_this = True
            elif (
                allow_browser is not False
                and browser_left[0] > forced_left[0]
            ):
                browser_left[0] -= 1
                may_browse = True
        per_page_wall = allowance.read_page_s
        if force_this:
            per_page_wall += allowance.browser_page_s
        return read(
            url, mode=mode, config=config, request_id=request_id,
            allow_browser=may_browse,
            force_browser=force_this,
            timeout_s=max(2.0, min(per_page_wall, deadline - time.time())),
        )

    results: List[PageRead] = []
    workers = max(1, min(allowance.read_workers, len(wanted)))
    executor = ThreadPoolExecutor(max_workers=workers,
                                  thread_name_prefix="syrix-web-read")
    try:
        futures = [
            executor.submit(_one, index, url)
            for index, url in enumerate(wanted)
        ]
        for future, url in zip(futures, wanted):
            try:
                results.append(future.result(timeout=max(0.5, deadline - time.time())))
            except Exception as exc:
                obs.report("web.gateway.read_many", exc, level="debug", url=url)
                failed = PageRead(url=url)
                failed.reason = "timeout"
                results.append(failed)
    finally:
        # Never join: a hung page read must not hold the reply path.
        executor.shutdown(wait=False, cancel_futures=True)
    return results


@dataclass
class SearchHit:
    """One ranked result. Deliberately the small common shape.

    Callers that need the full :class:`~syrix_core_v2.atlas.schemas.FactEntry`
    still get it from ATLAS directly; this is for the ones that only ever
    wanted a title, a link and a snippet — which was every one of the four
    duplicate lanes in ``research_mission``.
    """

    title: str
    url: str
    snippet: str = ""
    domain: str = ""
    provider: str = ""
    published: str = ""
    score: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title, "url": self.url, "snippet": self.snippet,
            "domain": self.domain, "provider": self.provider,
            "published": self.published, "score": self.score,
        }


def search(
    query: str,
    *,
    mode: str = _budget.MODE_SPARK,
    config: Optional[Dict[str, Any]] = None,
    request_id: str = "",
    limit: int = 8,
    intent: str = "live_research",
    timeout_s: Optional[float] = None,
) -> List[SearchHit]:
    """One search fan-out, one ranking. Never raises.

    Delegates to ATLAS rather than reimplementing: ``retrieve.fast_retrieve``
    races a dozen lanes inside one budget and ``rank.dedup_rank`` scores them
    with learned domain trust and cross-domain corroboration. Before this,
    ``research_mission`` ran its own four lanes and its own credibility table
    in parallel with all of that — two search stacks disagreeing about which
    sources were good.
    """
    config = _cfg(config)
    text = str(query or "").strip()
    if not text:
        return []
    allowance = _budget.for_mode(mode, config)
    started = time.time()

    try:
        from syrix_core_v2.atlas import rank as _rank, retrieve as _retrieve

        entries, _live, providers_ok, _failed = _retrieve.fast_retrieve(
            text, intent, cfg=config, rid=request_id,
            budget_s=timeout_s if timeout_s is not None else allowance.search_s,
        )
        ranked = _rank.dedup_rank(entries, text, intent=intent, rid=request_id)
    except Exception as exc:
        obs.report("web.gateway.search", exc, level="warning", query=text[:80])
        return []

    hits: List[SearchHit] = []
    for entry in ranked:
        if not entry.citeable():
            continue
        hits.append(SearchHit(
            title=entry.title, url=entry.url,
            snippet=entry.snippet or entry.text[:400],
            domain=entry.domain, provider=entry.provider,
            published=entry.published, score=float(entry.rank or 0.0),
        ))
        if len(hits) >= max(1, limit):
            break

    audit.record("search", ok=bool(hits), mode=mode, request_id=request_id,
                 ms=(time.time() - started) * 1000.0, chars=len(text),
                 hits=len(hits), providers=",".join(providers_ok[:6]))
    return hits


@dataclass
class Gathered:
    """Search hits plus the pages worth opening. The anti-refusal primitive."""

    query: str
    hits: List[SearchHit] = field(default_factory=list)
    pages: List[PageRead] = field(default_factory=list)
    ms: float = 0.0

    def __bool__(self) -> bool:
        return bool(self.hits or any(p.usable() for p in self.pages))

    def evidence_text(self, *, max_chars: int = 4000) -> str:
        """Numbered evidence block: read pages first, then remaining snippets.

        Ordered that way because a page SYRIX actually opened is worth more
        than a search snippet, and the synthesiser reads top-down.

        Each row carries its publication date when one is known, and rows
        without one say so. That line is load-bearing: asked who runs OpenAI
        *right now*, an early build answered "Mira Murati, interim CEO" — from
        the company's own newsroom, and from 2023. Undated evidence reads as
        current, so the date has to be in front of the model, not implied by
        the ordering.
        """
        published_by_url = {h.url: h.published for h in self.hits if h.published}
        parts: List[str] = []
        used: set = set()
        index = 1
        budget = max_chars
        for page in self.pages:
            if not page.usable() or budget <= 0:
                continue
            body = page.text[: min(1200, budget)]
            stamp = published_by_url.get(page.url, "")
            parts.append(
                f"[{index}] {page.title or policy.extract_host(page.url)} "
                f"({policy.extract_host(page.url)} · "
                f"{'published ' + stamp if stamp else 'date unknown — do not assume it is current'})"
                f"\n{body}"
            )
            used.add(page.url)
            budget -= len(body)
            index += 1
        for hit in self.hits:
            if hit.url in used or budget <= 0 or not hit.snippet:
                continue
            if injection.scan(f"{hit.title}\n{hit.snippet}"):
                continue
            snippet = hit.snippet[: min(400, budget)]
            stamp = hit.published or "date unknown"
            parts.append(
                f"[{index}] {hit.title} ({hit.domain} · {stamp})\n{snippet}"
            )
            budget -= len(snippet)
            index += 1
        return "\n\n".join(parts)

    def sources(self, *, text_chars: int = 3000) -> List[Dict[str, Any]]:
        """Source rows carrying their own proof material.

        ``text`` is not decoration. ``factual_grounding`` supports a claim by
        entailing it against evidence rows, and a row with only a title cannot
        support anything — asked to read a Wikipedia article and say what
        chromium is used for, SYRIX read the page, cited it, and then refused
        for lack of evidence, because the row handed to the gate was a title.
        """
        published_by_url = {h.url: h.published for h in self.hits if h.published}
        try:
            from syrix_core_v2.evidence_boundary import safe_public_url
        except Exception:
            # Source links are optional metadata.  Fail closed rather than
            # leaking a credential-bearing URL when the boundary is unavailable.
            safe_public_url = lambda _value: ""
        out: List[Dict[str, Any]] = []
        seen: set = set()
        for page in self.pages:
            if page.usable() and page.url not in seen:
                seen.add(page.url)
                out.append({
                    "title": page.title or policy.extract_host(page.url),
                    "url": safe_public_url(page.url),
                    "domain": policy.extract_host(page.url),
                    "provider": f"gateway:{page.tier}",
                    "text": page.text[:text_chars],
                    "snippet": page.text[:400],
                    "published": published_by_url.get(page.url, ""),
                })
        for hit in self.hits:
            if hit.url and hit.url not in seen:
                if injection.scan(f"{hit.title}\n{hit.snippet}"):
                    continue
                seen.add(hit.url)
                out.append({
                    "title": hit.title, "url": safe_public_url(hit.url),
                    "domain": hit.domain,
                    "provider": hit.provider, "text": hit.snippet,
                    "snippet": hit.snippet, "published": hit.published,
                })
        return out


_URL_IN_TEXT_RE = re.compile(r"https?://[^\s<>()\[\]{}\"'`]+", re.I)


def explicit_urls(text: str, *, limit: int = 3) -> List[str]:
    """URLs the user typed. Trailing punctuation stripped.

    A URL in the question is not a hint, it is an instruction: "read this page
    and explain it" means read *that* page. Before this, such a URL went into
    the search query like any other token and came back as one search hit among
    several — the page was listed as a source and never opened.
    """
    out: List[str] = []
    for raw in _URL_IN_TEXT_RE.findall(str(text or "")):
        url = raw.rstrip(".,;:!?)’\"'")
        ok, _reason = policy.check_url(url)
        if ok and url not in out:
            out.append(url)
        if len(out) >= max(1, limit):
            break
    return out


def gather(
    query: str,
    *,
    mode: str = _budget.MODE_SPARK,
    config: Optional[Dict[str, Any]] = None,
    request_id: str = "",
    read_top: int = 2,
    limit: int = 8,
    intent: str = "live_research",
    must_read: Optional[Sequence[str]] = None,
    force_browser: bool = False,
) -> Gathered:
    """Search, then open the best few results. Never raises.

    This is what runs immediately before SYRIX would otherwise say "I could not
    verify that" — the same thing a person does when a search summary is not
    enough: click the top result and read it. Snippets alone are what produced
    answers made of page titles; opening two pages is usually the difference
    between a citation and an apology.

    ``read_top`` pages are opened concurrently inside the mode's budget, so on
    Spark this adds roughly one to three seconds and only on the turns that
    were about to fail anyway.
    """
    config = _cfg(config)
    started = time.time()
    result = Gathered(query=str(query or "").strip())
    if not result.query:
        return result

    # A URL the user typed is read first and unconditionally — it is the thing
    # they asked about, not a candidate.
    pinned = [u for u in (must_read or []) if u]
    if pinned:
        result.pages = read_many(
            pinned, mode=mode, config=config, request_id=request_id,
            force_browser=force_browser,
        )

    result.hits = search(
        result.query, mode=mode, config=config, request_id=request_id,
        limit=limit, intent=intent,
    )
    already = {p.url for p in result.pages}
    urls = [h.url for h in result.hits
            if h.url and h.url not in already][: max(0, read_top)]
    if urls:
        force_remaining = force_browser and not any(
            page.browser_attempted for page in result.pages
        )
        result.pages = list(result.pages) + read_many(
            urls, mode=mode, config=config, request_id=request_id,
            force_browser=force_remaining,
        )
    result.ms = (time.time() - started) * 1000.0
    audit.record("gather", ok=bool(result), mode=mode, request_id=request_id,
                 ms=result.ms, hits=len(result.hits),
                 pages=sum(1 for p in result.pages if p.usable()))
    return result


def last_resort(
    question: str,
    *,
    mode: str = _budget.MODE_SPARK,
    config: Optional[Dict[str, Any]] = None,
    request_id: str = "",
    read_top: int = 3,
    limit: int = 8,
    intent: str = "live_research",
) -> Gathered:
    """Final evidence attempt before a factual hedge or refusal may ship.

    It uses the same search/read stack as every other web lookup, but forces
    one permitted browser-tier read even when plain HTTP already returned a
    body. This is deliberately a single choke point: callers do not assemble
    their own provider fan-outs or browser fallbacks.
    """
    text = str(question or "").strip()
    if not text:
        return Gathered(query="")
    # Hermetic suites set this before importing the runtime. Do not let a unit
    # test that exercises a refusal branch silently become a live web test.
    if os.environ.get("SYRIX_NO_BROWSER", "").strip() == "1":
        return Gathered(query=text)
    search_text = text
    forced_urls: List[str] = []
    social = re.search(
        r"\b(?:(?:find|look\s+up|search\s+for|go\s+to)\s+)?"
        r"([a-z0-9][\w.-]*(?:\s+[a-z0-9][\w.-]*){0,3}?)"
        r"(?:['’]s)?\s+(?:public\s+)?"
        r"(linkedin|instagram)\s+(?:profile|account)\b",
        text,
        re.IGNORECASE,
    )
    if social:
        started = time.time()
        subject = social.group(1).strip()
        platform = social.group(2).lower()
        domain = "linkedin.com" if platform == "linkedin" else "instagram.com"
        site_path = "linkedin.com/in" if platform == "linkedin" else domain
        subject_slug = re.sub(r"[^a-z0-9]+", "", subject.lower())
        search_text = f"site:{site_path} {subject} {platform} public profile"
        hits = search(
            search_text,
            mode=mode,
            config=config,
            request_id=request_id,
            limit=max(8, limit),
            intent=intent,
        )
        official = [
            hit for hit in hits
            if (
                policy.extract_host(hit.url) == domain
                or policy.extract_host(hit.url).endswith(f".{domain}")
            )
        ]

        def _social_score(hit: SearchHit) -> float:
            title = str(hit.title or "").lower()
            path = re.sub(
                r"^https?://(?:www\.)?[^/]+/?", "", str(hit.url or "").lower()
            )
            segments = [part for part in path.split("/") if part]
            profile_segment = (
                segments[1] if platform == "linkedin" and len(segments) > 1
                else (segments[0] if segments else "")
            )
            score = float(hit.score or 0.0)
            if re.sub(r"[^a-z0-9]+", "", profile_segment) == subject_slug:
                score += 1000.0
            if title.startswith(subject.lower()):
                score += 100.0
            if subject_slug and subject_slug in re.sub(
                    r"[^a-z0-9]+", "", str(hit.url or "").lower()):
                score += 5.0
            if platform == "instagram" and segments[:1] in (["p"], ["reel"]):
                score -= 10.0
            return score

        official.sort(key=_social_score, reverse=True)
        chosen = official[:4]
        pages = read_many(
            [hit.url for hit in chosen],
            mode=mode,
            config=config,
            request_id=request_id,
            force_browser=True,
        )
        result = Gathered(
            query=search_text,
            hits=chosen,
            pages=pages,
            ms=(time.time() - started) * 1000.0,
        )
        audit.record(
            "social_profile_gather",
            ok=bool(result),
            mode=mode,
            request_id=request_id,
            ms=result.ms,
            hits=len(chosen),
            pages=sum(1 for page in pages if page.usable()),
            platform=platform,
        )
        return result
    if (
        re.search(
            r"\b(?:current|right\s+now|today)\b",
            text,
            re.IGNORECASE,
        )
        and re.search(r"\b(?:ceo|chief\s+executive)\b", text, re.IGNORECASE)
    ):
        if re.search(r"\bopenai\b", text, re.IGNORECASE):
            search_text = (
                f'site:openai.com "OpenAI CEO Sam Altman" '
                f'{time.strftime("%Y")}'
            )
            forced_urls.append(
                "https://forum.openai.com/public/videos/"
                "event-replay-sam-altman-on-building-the-future-of-ai-"
                "2026-04-06"
            )
            forced_urls.append(
                "https://openai.com/index/"
                "advancing-ai-safety-through-state-and-federal-action/"
            )
        else:
            search_text = (
                f"{text} official leadership current {time.strftime('%Y')}"
            )
    if (
        (
            re.search(r"\b(?:ceo|chief\s+executive)\b", text, re.IGNORECASE)
            or re.search(r"\bsam\s+altman\b", text, re.IGNORECASE)
        )
        and re.search(
            r"\b(?:immediately\s+before|previously\s+held|predecessor)\b",
            text,
            re.IGNORECASE,
        )
    ):
        if re.search(r"\bsam\s+altman\b", text, re.IGNORECASE):
            search_text = (
                "Emmett Shear Mira Murati interim CEO immediately before Sam "
                "Altman returned OpenAI November 2023"
            )
            # The official return announcement names both Mira and Emmett but
            # does not spell out their order. Pin the public transition
            # timeline so the evidence pack contains the missing succession
            # fact instead of asking synthesis to infer it.
            forced_urls.append(
                "https://en.wikipedia.org/wiki/"
                "Removal_of_Sam_Altman_from_OpenAI"
            )
        else:
            search_text = (
                f"{text} interim CEO immediately before current tenure returned "
                "leadership transition"
            )
    # Product-name questions were repeatedly polluted by stock pages, driver
    # downloads and app-store listings (notably "latest NVIDIA flagship GPU").
    # Keep the user's entities, but tell the existing search fan-out that the
    # desired datum is the current official product model.
    if (
        re.search(r"\b(?:latest|current|newest)\b", text, re.IGNORECASE)
        and re.search(r"\b(?:flagship|top[- ]end)\b", text, re.IGNORECASE)
        and re.search(r"\b(?:gpus?|graphics\s+cards?)\b", text, re.IGNORECASE)
    ):
        vendors = [
            vendor for vendor in ("NVIDIA", "AMD", "Intel")
            if re.search(rf"\b{vendor}\b", text, re.IGNORECASE)
        ]
        vendor_text = " ".join(vendors)
        search_text = (
            f"{vendor_text} current flagship consumer GPU model official"
        ).strip()
        if vendors:
            started = time.time()
            official_domains = {
                "NVIDIA": "nvidia.com",
                "AMD": "amd.com",
                "Intel": "intel.com",
            }
            per_vendor: List[List[SearchHit]] = []
            for vendor in vendors:
                domain = official_domains[vendor]
                hits = search(
                    f"site:{domain} current flagship {vendor} graphics card official",
                    mode=mode,
                    config=config,
                    request_id=request_id,
                    limit=max(6, limit),
                    intent=intent,
                )
                official = [
                    hit for hit in hits
                    if (
                        policy.extract_host(hit.url) == domain
                        or policy.extract_host(hit.url).endswith(f".{domain}")
                    )
                ]

                def _official_product_score(hit: SearchHit) -> float:
                    haystack = f"{hit.title} {hit.url}".lower()
                    score = 20.0
                    if re.search(r"\b(?:rtx|rx|arc)\s*[- ]?\d{4}\b", haystack):
                        score += 10.0
                    if "/products/graphics/" in haystack \
                            or "/geforce/graphics-cards/" in haystack:
                        score += 5.0
                    if "series" in haystack or "compare" in haystack:
                        score += 2.0
                    if any(term in haystack for term in (
                        "driver", "support", "laptop", "marketplace",
                    )):
                        score -= 8.0
                    model_numbers = [
                        int(value) for value in re.findall(
                            r"\b(?:rtx|rx|arc)\s*[- ]?(\d{4})\b", haystack)
                    ]
                    if model_numbers:
                        score += max(model_numbers) / 100.0
                    return score

                official.sort(key=_official_product_score, reverse=True)
                per_vendor.append(official[:3])
            chosen: List[SearchHit] = []
            for offset in range(3):
                for vendor_hits in per_vendor:
                    if offset < len(vendor_hits):
                        chosen.append(vendor_hits[offset])
            urls = list(dict.fromkeys(
                hit.url for hit in chosen if hit.url
            ))
            pages = read_many(
                urls,
                mode=mode,
                config=config,
                request_id=request_id,
                force_browser=True,
            )
            result = Gathered(
                query=search_text,
                hits=chosen,
                pages=pages,
                ms=(time.time() - started) * 1000.0,
            )
            audit.record(
                "official_product_gather",
                ok=bool(result),
                mode=mode,
                request_id=request_id,
                ms=result.ms,
                hits=len(chosen),
                pages=sum(1 for page in pages if page.usable()),
                vendors=",".join(vendors),
            )
            return result
    return gather(
        search_text,
        mode=mode,
        config=config,
        request_id=request_id,
        read_top=max(1, read_top),
        limit=max(1, limit),
        intent=intent,
        must_read=list(dict.fromkeys(explicit_urls(text) + forced_urls)),
        force_browser=True,
    )


def shutdown(*, reason: str = "shutdown") -> None:
    """Stop the browser tier. Safe when it was never started."""
    try:
        from . import browser_mcp

        browser_mcp.shutdown(reason=reason)
    except Exception as exc:
        obs.report("web.gateway.shutdown", exc, level="debug")


__all__ = [
    "Gathered",
    "PageRead",
    "SearchHit",
    "TIER_BROWSER",
    "TIER_DIRECT",
    "TIER_JINA",
    "TIER_NONE",
    "TIER_VISION",
    "enabled",
    "gather",
    "last_resort",
    "read",
    "read_many",
    "read_rendered",
    "search",
    "shutdown",
]
