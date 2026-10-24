"""Accessibility-tree snapshot → readable text, links and elements.

``@playwright/mcp``'s ``browser_snapshot`` returns a header block plus the
page's accessibility tree as annotated YAML::

    ### Page
    - Page URL: https://example.com/
    - Page Title: Example Domain
    - HTTP status: 403
    ### Snapshot
    ```yaml
    - generic [ref=e2]:
      - heading "Example Domain" [level=1] [ref=e3]
      - paragraph [ref=e4]: This domain is for use in documentation.
      - link "Learn more" [ref=e6] [cursor=pointer]:
        - /url: https://iana.org/domains/example
    ```

This is parsed line-by-line rather than with a YAML loader: the ``[ref=e3]``
annotations are not YAML values, real pages emit unquoted colons inside node
text, and a parse error on one weird page must not lose the whole read.
A measured BBC News snapshot is ~85 000 characters of tree; the reader wants
prose, so structure is dropped and only the human-visible strings survive.

Why the tree instead of ``innerText`` (which ``atlas/browser.py`` uses): the
tree already excludes script, style and aria-hidden content, keeps heading
levels, and names links — so link text does not fuse into the paragraph beside
it, which is what produced the "pasted search-result titles as the answer" bug.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# Landmark roles whose ENTIRE subtree is site furniture. Skipping the node
# alone is not enough: a <nav> emits its whole link list as children, which is
# how "BBC Homepage | Skip to content | Sign in" ended up at the top of a news
# read. ``search`` is deliberately absent — some search engines wrap their
# results in it, and losing a results page is worse than keeping a search box.
_CHROME_CONTAINERS = frozenset({
    "banner", "navigation", "complementary", "contentinfo",
    "menubar", "menu", "tablist", "toolbar",
})

# Roles that never carry prose worth keeping, but whose children might.
_NO_TEXT_ROLES = frozenset({
    "img", "image", "figure", "separator", "progressbar", "alert",
    "status", "listbox", "presentation", "none",
})

# Roles that carry real prose.
_TEXT_ROLES = frozenset({
    "heading", "paragraph", "text", "generic", "listitem", "cell",
    "rowheader", "columnheader", "blockquote", "code", "term", "definition",
    "caption", "article", "main", "region", "note", "log", "time",
    "strong", "emphasis", "superscript", "subscript", "mark", "insertion",
    "deletion", "abbr", "document", "group", "list", "table", "row",
    "rowgroup", "gridcell", "grid", "details", "summary",
})

# Roles a caller may click or type into. Kept so Supercomputer-style callers
# can act on a page read through the gateway without a second snapshot format.
_ACTIONABLE_ROLES = frozenset({
    "link", "button", "textbox", "searchbox", "combobox", "checkbox",
    "radio", "menuitem", "option", "tab", "switch", "slider", "spinbutton",
})

_NODE_RE = re.compile(
    r"""^(?P<indent>\s*)-\s+
        (?P<role>[a-zA-Z][\w-]*)
        (?:\s+"(?P<name>(?:[^"\\]|\\.)*)")?
        (?P<attrs>(?:\s*\[[^\]]*\])*)
        \s*:?\s*
        (?P<value>.*)$""",
    re.VERBOSE,
)
_URL_LINE_RE = re.compile(r"^\s*-\s*/url:\s*(?P<url>\S+)\s*$")
_REF_RE = re.compile(r"\[ref=([^\]]+)\]")
_LEVEL_RE = re.compile(r"\[level=(\d+)\]")

_HEADER_URL_RE = re.compile(r"^-\s*Page URL:\s*(?P<v>.+)$", re.M)
_HEADER_TITLE_RE = re.compile(r"^-\s*Page Title:\s*(?P<v>.+)$", re.M)
_HEADER_STATUS_RE = re.compile(r"^-\s*HTTP status:\s*(?P<v>\d+)", re.M)

_YAML_FENCE_RE = re.compile(r"```(?:yaml)?\s*\n(?P<body>.*?)```", re.S)

# The same markers atlas/reader.py uses, so "this page is a challenge, not
# content" means the same thing on both sides of the ladder.
_CHALLENGE_MARKERS = (
    "just a moment", "enable javascript", "checking your browser",
    "captcha", "access denied", "are you a robot", "verify you are human",
    "attention required", "complete the following challenge",
    "made by a human", "bots use", "unusual traffic", "not a robot",
    "security check", "ddos protection", "ray id",
)

MIN_GOOD_CHARS = 400


@dataclass
class Element:
    ref: str
    role: str
    name: str
    url: str = ""


@dataclass
class PageSnapshot:
    url: str = ""
    title: str = ""
    status: int = 0
    text: str = ""
    links: List[Tuple[str, str]] = field(default_factory=list)
    elements: List[Element] = field(default_factory=list)
    raw_chars: int = 0

    def looks_blocked(self) -> bool:
        """True when we got a challenge page rather than the content.

        Status is checked first because a 403 with a plausible-looking body is
        exactly how Cloudflare presents itself — trusting the text alone would
        let "Just a moment..." through as an answer.

        Deliberately *not* a length check. A short page is short, not blocked:
        conflating the two made ``example.com`` report as a bot challenge and
        would burn a browser escalation on every genuinely brief page.
        """
        if self.status and self.status >= 400:
            return True
        probe = (self.title + " " + self.text[:1500]).lower()
        return any(marker in probe for marker in _CHALLENGE_MARKERS)

    def is_thin(self) -> bool:
        """Readable, but probably not enough to answer from."""
        return len(self.text) < MIN_GOOD_CHARS

    def usable(self) -> bool:
        return bool(self.text) and not self.looks_blocked()

    def as_dict(self) -> Dict[str, object]:
        return {
            "url": self.url,
            "title": self.title,
            "status": self.status,
            "text": self.text,
            "links": list(self.links),
            "elements": [
                {"ref": e.ref, "role": e.role, "name": e.name, "url": e.url}
                for e in self.elements
            ],
        }


def _unescape(value: str) -> str:
    return value.replace('\\"', '"').replace("\\\\", "\\")


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _extract(
    body: str,
    *,
    skip_chrome: bool,
    max_chars: int,
    max_elements: int,
    max_links: int,
) -> Tuple[List[str], List[Tuple[str, str]], List[Element]]:
    """One pass over the tree. ``skip_chrome`` drops landmark subtrees."""
    pieces: List[str] = []
    links: List[Tuple[str, str]] = []
    elements: List[Element] = []
    seen_text: set = set()
    budget = max_chars
    # A link's ``/url:`` line is a descendant, not necessarily the next line —
    # a headline link wraps a heading, so the URL arrives several nodes later.
    # Tracking the link's own indent is what keeps them paired.
    pending_link: Optional[Element] = None
    pending_indent = -1
    # Indent of a chrome subtree we are inside; -1 when not skipping. Skipping
    # the node alone is not enough — a <nav> emits its whole link list as
    # children, which is how "BBC Homepage | Skip to content | Sign in" ended up
    # at the top of a news read.
    skip_indent = -1

    for line in body.splitlines():
        if not line.strip():
            continue

        indent = len(line) - len(line.lstrip())
        if skip_indent >= 0:
            if indent > skip_indent:
                continue
            skip_indent = -1

        url_match = _URL_LINE_RE.match(line)
        if url_match:
            if pending_link is not None and indent > pending_indent:
                pending_link.url = url_match.group("url")
                if (len(links) < max_links and pending_link.name
                        and pending_link.url.startswith(("http://", "https://"))):
                    links.append((pending_link.name, pending_link.url))
                pending_link, pending_indent = None, -1
            continue

        node = _NODE_RE.match(line)
        if node is None:
            continue
        if pending_link is not None and indent <= pending_indent:
            pending_link, pending_indent = None, -1

        role = (node.group("role") or "").lower()
        name = _unescape(node.group("name") or "")
        attrs = node.group("attrs") or ""
        value = _clean(node.group("value") or "")

        ref_match = _REF_RE.search(attrs)
        ref = ref_match.group(1) if ref_match else ""

        if skip_chrome and role in _CHROME_CONTAINERS:
            # Everything nested under this node is furniture too.
            skip_indent = indent
            continue

        if role in _NO_TEXT_ROLES:
            continue

        if role in _ACTIONABLE_ROLES:
            element = Element(ref=ref, role=role, name=_clean(name) or value)
            if len(elements) < max_elements:
                elements.append(element)
            if role == "link":
                pending_link, pending_indent = element, indent
            # A link's own label is content too — a headline is usually a link.
            candidate = element.name
        elif role in _TEXT_ROLES or not role:
            candidate = value or _clean(name)
            if role == "heading" and candidate:
                level_match = _LEVEL_RE.search(attrs)
                level = int(level_match.group(1)) if level_match else 2
                candidate = ("#" * max(1, min(6, level))) + " " + candidate
        else:
            candidate = value or _clean(name)

        candidate = _clean(candidate)
        if not candidate or len(candidate) < 2:
            continue
        key = candidate.lower()
        if key in seen_text:
            continue
        seen_text.add(key)
        if budget <= 0:
            continue
        pieces.append(candidate)
        budget -= len(candidate) + 1

    return pieces, links, elements


def parse(
    payload: str,
    *,
    max_chars: int = 12000,
    max_elements: int = 120,
    max_links: int = 60,
) -> PageSnapshot:
    """Parse one ``browser_snapshot`` payload. Never raises.

    Two passes, and the second one is the whole point. Dropping landmark
    subtrees turns a navigation-heavy page into prose — but real markup is not
    tidy: Bing nests its *entire* results page inside ``banner``, so the tidy
    pass returned nothing at all. So: try it clean, and if that came back thin,
    take the whole tree instead. More text beats no text every time, and a page
    read that silently returns "" is indistinguishable from a page that had
    nothing to say.
    """
    snap = PageSnapshot()
    raw = str(payload or "")
    snap.raw_chars = len(raw)
    if not raw:
        return snap

    match = _HEADER_URL_RE.search(raw)
    if match:
        snap.url = _clean(match.group("v"))
    match = _HEADER_TITLE_RE.search(raw)
    if match:
        snap.title = _clean(match.group("v"))
    match = _HEADER_STATUS_RE.search(raw)
    if match:
        try:
            snap.status = int(match.group("v"))
        except ValueError:
            snap.status = 0

    fence = _YAML_FENCE_RE.search(raw)
    if fence is not None:
        body = fence.group("body")
    else:
        # No fence (a truncated or unusual payload). Drop the header block by
        # hand so "Page URL: ..." never lands in the page text.
        marker = raw.find("### Snapshot")
        body = raw[marker + len("### Snapshot"):] if marker >= 0 else raw

    kwargs = {
        "max_chars": max_chars,
        "max_elements": max_elements,
        "max_links": max_links,
    }
    pieces, links, elements = _extract(body, skip_chrome=True, **kwargs)
    if sum(len(p) for p in pieces) < MIN_GOOD_CHARS:
        alt_pieces, alt_links, alt_elements = _extract(
            body, skip_chrome=False, **kwargs
        )
        if sum(len(p) for p in alt_pieces) > sum(len(p) for p in pieces):
            pieces, links, elements = alt_pieces, alt_links, alt_elements

    snap.text = "\n".join(pieces)[:max_chars]
    snap.links = links
    snap.elements = elements
    return snap


def to_reader_tuple(snap: PageSnapshot) -> Tuple[str, str, str]:
    """``(title, text, method)`` — the shape ``atlas.reader.read_page`` returns.

    Keeping the gateway's output in the reader's existing tuple means the
    ATLAS enrichment path needs no new branch to consume a browser read.
    """
    return snap.title, snap.text, "browser+a11y"


__all__ = [
    "Element",
    "MIN_GOOD_CHARS",
    "PageSnapshot",
    "parse",
    "to_reader_tuple",
]
