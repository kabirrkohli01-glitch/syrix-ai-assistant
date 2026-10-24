"""A page's links and its self-description, read from the HTML.

The direct read tier returns article text only. That is the right shape for
evidence and the wrong one for navigation: a homepage read that way has no
"About" link in it, so there is nothing to follow and the only way on to the
page asked for is to guess its URL. The rendered tier already reports links
from the accessibility tree; this gives the direct tier the same.

Also read here, because they are the one part of a page written to describe
the page: the ``<meta>`` description and Open Graph tags, and JSON-LD. A
company homepage is mostly navigation and promotion, and its own one-paragraph
summary of itself is often only in ``og:description``.

Standard library only. Never raises: a malformed page yields whatever was
parsed before the fault.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit, urlunsplit

# A retailer's or a chipmaker's mega-menu alone runs to several hundred links,
# and "About" is usually in the footer, after all of them.
MAX_LINKS = 800
_MAX_TEXT = 100

# Regions a link can sit in. Site structure lives in the chrome, so a link in
# the navigation or the footer is far likelier to be "About us" or "Careers"
# than one in the body, which is mostly articles and promotions.
REGION_NAV = "nav"
REGION_BODY = "body"
_CHROME_TAGS = {"nav", "header", "footer"}

_SKIP_SCHEMES = ("javascript:", "mailto:", "tel:", "data:", "sms:", "about:")
_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_CHROME_ROLES = ("navigation", "banner", "contentinfo")
_SPACE = re.compile(r"\s+")


@dataclass
class Link:
    text: str
    url: str
    region: str = REGION_BODY

    def as_tuple(self) -> Tuple[str, str]:
        return (self.text, self.url)


def _clean(text: str) -> str:
    return _SPACE.sub(" ", str(text or "")).strip()


def normalise(url: str) -> str:
    """Drop the fragment and a bare trailing ``?`` so one page is one URL."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/",
                       parts.query, ""))


class _Parser(HTMLParser):
    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self.links: List[Link] = []
        self.seen: Dict[str, int] = {}
        self.meta: Dict[str, str] = {}
        self.ld: List[str] = []
        self.title = ""
        self._in_title = False
        # Open elements as (tag, opens_chrome). Needed because a region can be
        # opened by an ARIA role on a plain <div>, and only the matching
        # </div> closes it — a counter keyed on tag names never came back
        # down, and every link after the first role="navigation" was filed as
        # navigation.
        self._stack: List[Tuple[str, bool]] = []
        self._chrome = 0
        self._anchor: Optional[Dict[str, Any]] = None
        self._script_ld = False
        self._script_buf: List[str] = []
        self._skip = 0

    # A link's visible text, or failing that its label or the alt text of an
    # image inside it — a logo that links home has no text of its own.
    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag not in _VOID:
            chrome = tag in _CHROME_TAGS or a.get("role", "").lower() in _CHROME_ROLES
            self._stack.append((tag, chrome))
            self._chrome += 1 if chrome else 0
        if tag == "base" and a.get("href"):
            self.base = urljoin(self.base, a["href"])
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and a.get("content") and key not in self.meta:
                self.meta[key] = _clean(a["content"])[:600]
        elif tag == "script":
            if "ld+json" in a.get("type", "").lower():
                self._script_ld = True
                self._script_buf = []
            else:
                self._skip += 1
        elif tag in ("style", "noscript", "template", "svg"):
            self._skip += 1
        elif tag == "a":
            self._anchor = {"href": a.get("href", ""),
                            "label": a.get("aria-label") or a.get("title") or "",
                            "text": []}
        elif tag == "img" and self._anchor is not None and a.get("alt"):
            self._anchor["text"].append(a["alt"])

    def handle_endtag(self, tag: str) -> None:
        # Pop back to the matching open tag, closing anything left unclosed
        # inside it, as a browser would. An end tag with no opener is ignored.
        for depth in range(len(self._stack) - 1, -1, -1):
            if self._stack[depth][0] == tag:
                for _, chrome in self._stack[depth:]:
                    self._chrome -= 1 if chrome else 0
                del self._stack[depth:]
                break
        if tag == "title":
            self._in_title = False
        elif tag == "script":
            if self._script_ld:
                self.ld.append("".join(self._script_buf))
                self._script_ld = False
            elif self._skip:
                self._skip -= 1
        elif tag in ("style", "noscript", "template", "svg"):
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._anchor is not None:
            self._close_anchor()

    def handle_data(self, data: str) -> None:
        if self._script_ld:
            self._script_buf.append(data)
            return
        if self._skip:
            return
        if self._in_title:
            self.title += data
        if self._anchor is not None:
            self._anchor["text"].append(data)

    def _close_anchor(self) -> None:
        anchor, self._anchor = self._anchor, None
        if len(self.links) >= MAX_LINKS or anchor is None:
            return
        href = anchor["href"].strip()
        if not href or href.startswith("#") or href.lower().startswith(_SKIP_SCHEMES):
            return
        url = normalise(urljoin(self.base, href))
        if not url:
            return
        text = _clean(" ".join(anchor["text"])) or _clean(anchor["label"])
        if not text:
            return
        text = text[:_MAX_TEXT]
        region = REGION_NAV if self._chrome else REGION_BODY
        if url in self.seen:
            # One destination, several anchors: keep the most descriptive
            # text, and count it as navigation if any anchor was.
            existing = self.links[self.seen[url]]
            if len(text) > len(existing.text) and len(text) <= 60:
                existing.text = text
            if region == REGION_NAV:
                existing.region = REGION_NAV
            return
        self.seen[url] = len(self.links)
        self.links.append(Link(text=text, url=url, region=region))


def parse(html: str, base_url: str) -> Tuple[str, List[Link], Dict[str, str], List[Any]]:
    """``(title, links, meta, json_ld)`` for one page."""
    parser = _Parser(base_url)
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        pass
    ld: List[Any] = []
    for raw in parser.ld:
        try:
            ld.append(json.loads(raw.strip()))
        except ValueError:
            continue
    return _clean(parser.title), parser.links, parser.meta, ld


_LD_FIELDS = ("name", "alternateName", "legalName", "description", "foundingDate",
              "founder", "numberOfEmployees", "address", "email", "telephone",
              "jobTitle", "worksFor", "alumniOf", "url", "sameAs")


def _ld_items(node: Any) -> List[Dict[str, Any]]:
    if isinstance(node, list):
        return [x for item in node for x in _ld_items(item)]
    if isinstance(node, dict):
        if "@graph" in node:
            return _ld_items(node["@graph"])
        return [node]
    return []


def _flat(value: Any) -> str:
    if isinstance(value, dict):
        return _clean(value.get("name") or value.get("value")
                      or ", ".join(str(v) for k, v in value.items()
                                   if not str(k).startswith("@") and isinstance(v, (str, int))))
    if isinstance(value, list):
        return ", ".join(filter(None, (_flat(v) for v in value[:6])))
    return _clean(str(value))


def describe(meta: Dict[str, str], ld: List[Any]) -> str:
    """What the page says about itself, as short labelled lines.

    Only organisation- and person-shaped JSON-LD is used. A product page's
    JSON-LD is a catalogue and an article's is its byline, and neither is the
    page describing the site.
    """
    lines: List[str] = []
    summary = meta.get("og:description") or meta.get("description") or ""
    if summary:
        lines.append(f"The page describes itself as: {summary}")
    for item in _ld_items(ld):
        declared = item.get("@type") or ""
        kind = (declared if isinstance(declared, str)
                else ", ".join(str(t) for t in declared if t))
        if not re.search(r"Organization|Corporation|Person|ProfilePage|LocalBusiness|"
                         r"CollegeOrUniversity|EducationalOrganization|NewsMediaOrganization",
                         kind):
            continue
        facts = []
        for key in _LD_FIELDS:
            if key in item and item[key]:
                value = _flat(item[key])
                if value and value not in summary:
                    facts.append(f"{key}: {value[:240]}")
        if facts:
            lines.append(f"Structured data ({kind}): " + "; ".join(facts))
        if len(lines) >= 4:
            break
    return "\n".join(lines)
