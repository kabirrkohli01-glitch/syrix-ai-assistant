"""Searching, opening pages, and finding things on them.

Three tools, split along the lines the model actually reasons about:
``search`` answers "what is out there", ``read`` answers "what does that page
say, and where does it lead", and ``find_on_page`` answers "is this on the
page I have open". The previous runtime had four overlapping search entry
points and the model could not tell them apart.

``read`` returns a page's links numbered, and follows one by number. That is
the shape of ChatGPT's ``open``/``click``: the step from a homepage to its
About page is one call, not a guessed address or a second search. Before it,
the direct read tier returned text only, so a homepage read offered nothing
to follow.

``search`` is a thin wrapper over ``research.gather`` — the same code the turn
uses to prefetch before the first round — so the model and the prefetch get
the same evidence for the same query.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

from ..research import gather as _gather
from .registry import Tool, ToolContext, ToolResult, _as_list, register

_SPARK_READ_LIMIT = 6
_ZENITH_READ_LIMIT = 12

# Page text handed back per page; less when several are opened at once, so a
# batch fits the same prompt as a single page.
_PAGE_CHARS = 3500
_PAGE_CHARS_MANY = 1800
_LINKS_SHOWN = 25
_LINKS_SHOWN_MANY = 12


def _web_mode(tctx: ToolContext) -> str:
    return tctx.budget.mode if tctx.budget.mode in ("spark", "zenith") else "spark"


def _deadline(tctx: ToolContext, ceiling: float) -> float:
    return time.monotonic() + min(tctx.remaining(), ceiling)


def _search(args: Dict[str, Any], tctx: ToolContext) -> ToolResult:
    from . import pages

    queries = _as_list(args.get("queries") or args.get("query"), tctx.budget.max_queries)
    if not queries:
        return ToolResult.problem(
            "No query given. Pass 'queries' as a list of search strings."
        )
    # His name is never searched: the results are other people. What SYRIX
    # knows about him is returned in their place.
    from .. import owner as _owner
    queries, held = _owner.withhold(queries, tctx.question)
    if held and not queries:
        return ToolResult(
            content=_owner.note(),
            detail="answered from what SYRIX knows about its creator",
        )
    if tctx.remaining() <= 0.3:
        return ToolResult.problem(
            "Out of time before the search could run. Answer from what you have."
        )

    sources = _gather.search(
        queries,
        web_mode=_web_mode(tctx),
        request_id=tctx.request_id,
        limit=tctx.budget.max_sources,
        deadline=_deadline(tctx, 8.0 if tctx.budget.mode == "spark" else 20.0),
    )
    if not sources:
        return ToolResult.problem(
            "Nothing came back for: " + "; ".join(queries) +
            ". Try different wording, or answer from what you already have."
        )

    pages.admit(tctx.request_id, [source.url for source in sources])
    lines = [f"{len(sources)} results for: " + " | ".join(queries), ""]
    for i, source in enumerate(sources, 1):
        when = f" ({source.published})" if source.published else ""
        lines.append(f"[{i}] {source.title or source.url}{when}\n    {source.url}")
        if source.snippet:
            lines.append(f"    {source.snippet}")
    lines += ["", "These are titles and snippets. Open the ones the answer "
                  "depends on with the read tool before quoting a figure."]

    return ToolResult(
        content="\n".join(lines),
        sources=[s.as_dict() for s in sources],
        detail=f"{len(sources)} results across {len(queries)} queries",
    )


def _links_listing(opened: Any, shown: int) -> List[str]:
    """The page's links, numbered by their place in the page's own list.

    Same-site navigation first — that is where "About", "Careers" and
    "Pricing" live — then the rest of the same site, then elsewhere. The
    number is the link's index in the page, so it stays valid whichever of
    them are listed.
    """
    from ..research.site import host_of, registrable

    home = registrable(host_of(opened.final_url or opened.url))
    ranked = sorted(
        enumerate(opened.links, 1),
        key=lambda item: (registrable(host_of(item[1].url)) != home,
                          getattr(item[1], "region", "") != "nav", item[0]))
    lines = [f"[{n}] {link.text} — {link.url}" for n, link in ranked[:shown]]
    if len(opened.links) > shown:
        lines.append(f"... {len(opened.links) - shown} more links not listed; "
                     "find_on_page searches them too.")
    return lines


def _read(args: Dict[str, Any], tctx: ToolContext) -> ToolResult:
    from syrix_web import gateway

    from ..research.pack import _excerpt
    from . import pages

    limit = _SPARK_READ_LIMIT if tctx.budget.mode == "spark" else _ZENITH_READ_LIMIT
    urls = _as_list(args.get("urls") or args.get("url"), limit)
    held = pages.page(tctx.request_id, args.get("page")) if args.get("page") is not None else None
    if args.get("page") is not None and held is None:
        # "Page 1" before any page was opened means source [1] of the
        # evidence — the only thing numbered 1 in the prompt. Measured, a lane
        # asked for page 1, link 3 on its first call, was told no page 1 was
        # open, and spent a round finding that out. Source [N] is opened, and
        # its own links come back numbered for the next step.
        source = pages.evidence_url(tctx.request_id, args.get("page"))
        if not source:
            return ToolResult.problem(
                f"There is no page {args.get('page')} open this turn. Open a URL with "
                "read first; its links then come back numbered.")
        urls = [source] + [u for u in urls if u != source]
    elif args.get("page") is not None and args.get("link") is not None:
        try:
            index = int(args.get("link")) - 1
            if index < 0:
                raise IndexError(index)
            chosen = held.links[index]
        except (TypeError, ValueError, IndexError):
            return ToolResult.problem(
                f"Page {args.get('page')} has no link {args.get('link')}; its links are "
                f"numbered 1 to {len(held.links)}.")
        urls = [chosen.url] + [u for u in urls if u != chosen.url]
    if not urls:
        return ToolResult.problem(
            "No page given. Pass 'urls' as a list of links, or 'page' and 'link' "
            "to follow a numbered link on a page already open.")
    if tctx.remaining() <= 0.4:
        return ToolResult.problem(
            "Out of time before any page could be opened. Answer from what you have.")

    refused: List[str] = []
    wanted: List[str] = []
    for url in urls:
        ok, why = pages.allowed(tctx.request_id, url)
        if ok:
            wanted.append(url)
        else:
            refused.append(why)
    if not wanted:
        return ToolResult.problem("\n".join(refused))

    mode = _web_mode(tctx)
    per_page = min(tctx.remaining(), 6.0 if mode == "spark" else 20.0)
    with ThreadPoolExecutor(max_workers=len(wanted), thread_name_prefix="v3open") as pool:
        results = list(pool.map(
            lambda u: gateway.open_page(u, mode=mode, request_id=tctx.request_id,
                                        timeout_s=max(1.5, per_page - 0.3)),
            wanted))

    many = len(wanted) > 1
    blocks: List[str] = []
    cited: List[Dict[str, Any]] = []
    problems = list(refused)
    for url, opened in zip(wanted, results):
        if getattr(opened, "suspicious", False):
            problems.append(f"{url}: ignored, the page contained instructions "
                            "aimed at the assistant")
            continue
        if not getattr(opened, "ok", False):
            problems.append(f"{url}: {getattr(opened, 'reason', '') or 'would not open'}")
            continue
        number = pages.keep(tctx.request_id, opened)
        pages.admit(tctx.request_id, [link.url for link in opened.links])
        landed = opened.final_url or url
        body = _excerpt(opened.text or "", _PAGE_CHARS_MANY if many else _PAGE_CHARS,
                        tctx.question)
        parts = [f"### Page {number}: {opened.title or landed}", landed]
        if opened.about:
            parts.append(opened.about)
        parts += ["", body or "(no readable text on this page)"]
        if opened.links:
            parts += ["", f'Links on page {number} (follow one with read '
                          f'{{"page": {number}, "link": N}}):']
            parts += _links_listing(opened, _LINKS_SHOWN_MANY if many else _LINKS_SHOWN)
        blocks.append("\n".join(parts))
        cited.append({"url": landed, "title": opened.title or landed,
                      "domain": landed.split("/")[2] if "//" in landed else "",
                      "provider": "read", "snippet": "", "published": "",
                      "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "image": ""})

    if not blocks:
        return ToolResult.problem("None of those pages could be read:\n- " + "\n- ".join(problems))
    content = "\n\n".join(blocks)
    if problems:
        content += "\n\nNot read:\n- " + "\n- ".join(problems)
    return ToolResult(content=content, sources=cited,
                      detail=f"{len(blocks)} of {len(urls)} pages read")


def _find(args: Dict[str, Any], tctx: ToolContext) -> ToolResult:
    """Ctrl-F over a page already open: its text and its link labels."""
    from . import pages

    number = args.get("page") or 1
    held = pages.page(tctx.request_id, number)
    needle = str(args.get("text") or args.get("pattern") or "").strip()
    if held is None:
        return ToolResult.problem(f"Page {number} is not open. Open it with read first.")
    if not needle:
        return ToolResult.problem("Say what to look for with 'text'.")
    lines = [ln.strip() for ln in (held.text or "").splitlines() if ln.strip()]
    low = needle.lower()
    hits: List[str] = []
    for i, line in enumerate(lines):
        if low in line.lower():
            before = lines[i - 1] if i > 0 else ""
            after = lines[i + 1] if i + 1 < len(lines) else ""
            hits.append(" / ".join(x for x in (before[-160:], line[:400], after[:160]) if x))
        if len(hits) >= 12:
            break
    links = [f"[{n}] {link.text} — {link.url}" for n, link in enumerate(held.links, 1)
             if low in link.text.lower() or low in link.url.lower()][:12]
    if not hits and not links:
        return ToolResult(content=f"'{needle}' does not appear on page {number}.",
                          detail="no match")
    out = [f"'{needle}' on page {number}:"] + [f"- {h}" for h in hits]
    if links:
        out += ["", "Matching links:"] + links
    return ToolResult(content="\n".join(out), detail=f"{len(hits)} lines, {len(links)} links")


register(Tool(
    name="search",
    description=(
        "Search the web. Pass every query you want at once as a list — they "
        "run together and cost the same as one. Use it for anything current, "
        "anything specific, and anything you would otherwise be guessing at. "
        "Returns ranked titles, links and snippets, not full pages."
    ),
    parameters={
        "type": "object",
        "properties": {
            "queries": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Search queries. Write them the way you would type them "
                    "into a search box, and vary the angle between them "
                    "rather than repeating one phrasing."
                ),
            },
        },
        "required": ["queries"],
    },
    run=_search,
    kind="search",
    label="Searching the web",
))

register(Tool(
    name="read",
    description=(
        "Open web pages and return their text AND their links, numbered. Pass "
        "every URL you want at once, or follow a link on a page you already "
        'opened with {"page": N, "link": M} — that is how you move from a '
        "homepage to its About, Careers or Pricing page. Use it before quoting "
        "a figure, a date or a direct statement from a source. Only addresses "
        "already seen this turn can be opened: ones the user gave, ones in "
        "search results and evidence, and links on pages you opened."
    ),
    parameters={
        "type": "object",
        "properties": {
            "urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Full URLs, from the user, a search result or a page's link list.",
            },
            "page": {
                "type": "integer",
                "description": "A page opened earlier this turn, by its number.",
            },
            "link": {
                "type": "integer",
                "description": "Which numbered link on that page to follow.",
            },
        },
    },
    run=_read,
    kind="source",
    label="Opening pages",
))

register(Tool(
    name="find_on_page",
    description=(
        "Search the text and links of a page already opened with read, like "
        "Ctrl-F. Use it to check a long page for a name, a figure or a "
        "section without reading all of it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "page": {"type": "integer", "description": "The page number read gave it."},
            "text": {"type": "string", "description": "The word or phrase to find."},
        },
        "required": ["page", "text"],
    },
    run=_find,
    kind="tool_call",
    label="Searching the page",
))
