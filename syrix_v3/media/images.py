"""Which answers get pictures, and which pictures.

A photograph earns its place when the subject is something you can see — a
person, a place, a product, an animal, a building, a news event — or when
pictures were asked for outright. It is noise on a question about how
something works, on code, on arithmetic, and on anything already carrying a
weather or quote card.

Candidates come from the evidence first, because those images belong to the
pages the answer was written from: Tavily chooses images for the query itself,
and Exa reports each page's own preview image. An image search is added only
when pictures were asked for or the evidence carried too few. Logos, icons,
avatars and sprites are dropped by name, since a publisher's logo is the most
common preview image and the least useful one.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# Typed fast, the word for pictures is often misspelt, and a request for them
# that is not recognised is answered with "I can't show images": "iamges",
# "imges", "picturs" and "fotos" are all asking.
_ASKED = re.compile(
    r"\b(?:show me|pictures?|picturs?|pics?|photos?|fotos?|images?|imgs?|imges?|iamges?|"
    r"imagse|imaegs?|wallpapers?|what (?:does|do|did) .{1,50} look like|looks? like)\b", re.I)

# Things that are worth seeing. Deliberately short: a picture on every answer
# is noise, and news, launches and general questions read better without one.
_VISIBLE = re.compile(
    r"\b(?:where is|visit|travel|trip to|holiday in|things to do|places to|landmarks?|"
    r"beach(?:es)?|mountains?|islands?|museum|castle|cathedral|temple|palace|skyline|"
    r"architecture|breed|species|animal|bird|recipe|dish|cuisine|painting|artwork|"
    r"sculpture|supercar|what does .{1,40} look like)\b", re.I)
_PERSON = re.compile(r"^\s*(?:who (?:is|was)|who's)\s+[a-z]+\s+[a-z]+", re.I)

_ABSTRACT = re.compile(
    r"\b(?:how (?:do|does|can|to|should|would)|why|explain|difference between|meaning of|"
    r"define|definition|calculate|convert|code|function|script|program|error|bug|"
    r"formula|equation|essay|write|rewrite|summari[sz]e|translate|should i)\b", re.I)

_JUNK = re.compile(
    r"(?:logo|icon|favicon|sprite|avatar|placeholder|default[-_]?image|blank|spacer|"
    r"badge|banner[-_]?ad|1x1|pixel|gravatar|emoji)", re.I)


def wanted(question: str, *, has_card: bool = False, has_attachment: bool = False) -> int:
    """How many images this answer should show: 0, 4 or 8."""
    from .. import form
    text = " ".join(str(question or "").split())
    if not text or has_attachment or form.no_images(text):
        return 0
    if _ASKED.search(text):
        return 8
    if has_card or _ABSTRACT.search(text) or form.requested(text):
        return 0
    if _VISIBLE.search(text):
        return 4
    return 3 if _PERSON.search(text) else 0


def explicit(question: str) -> bool:
    return bool(_ASKED.search(str(question or "")))


def usable(url: str) -> bool:
    if not isinstance(url, str) or not url.startswith("https://"):
        return False
    if url.lower().split("?")[0].endswith(".svg"):
        return False
    from ..research.lanes import explicit
    return not _JUNK.search(url) and not explicit(url)


def from_evidence(evidence: Any) -> List[Dict[str, str]]:
    """Images the gathered evidence already carries, best first."""
    out: List[Dict[str, str]] = []
    if evidence is None:
        return out
    for row in getattr(evidence, "images", None) or []:
        if usable(row.get("url", "")):
            out.append({"url": row["url"], "title": row.get("title", ""),
                        "page": row.get("page", ""), "domain": row.get("domain", "")})
    for source in (getattr(evidence, "opened", None) or [])[:8]:
        image = getattr(source, "image", "")
        if usable(image):
            out.append({"url": image, "title": source.title, "page": source.url,
                        "domain": source.domain})
    return out


def search(query: str, *, limit: int = 8, timeout_s: float = 3.0) -> List[Dict[str, str]]:
    """An image search for the query. DuckDuckGo and Bing only.

    The ``ddgs`` default rotates through every engine it knows, Google among
    them, so the engines are named explicitly.
    """
    from ..compat import ddgs as _ddgs
    try:
        DDGS = _ddgs()
    except Exception:
        return []
    rows: List[Dict[str, Any]] = []
    # Bing first: it answers image queries in about 0.4s, and DuckDuckGo
    # throttles under load (measured timing out at 6s). One engine at a time,
    # so a stalled one cannot take the other's results with it.
    for engine in ("bing", "duckduckgo"):
        try:
            with DDGS(timeout=max(1, int(timeout_s))) as client:
                rows = client.images(query, max_results=limit, safesearch="moderate",
                                     backend=engine) or []
        except Exception:
            rows = []
        if rows:
            break
    from ..research.lanes import explicit as _explicit
    asked_adult = _explicit(query)
    out: List[Dict[str, str]] = []
    for row in rows or []:
        url = str(row.get("image") or "")
        if not usable(url):
            url = str(row.get("thumbnail") or "")
        if not usable(url):
            continue
        page = str(row.get("url") or "")
        if not asked_adult and _explicit(f"{page} {row.get('title') or ''}"):
            continue
        domain = re.sub(r"^www\.", "", page.split("/")[2]) if page.count("/") >= 2 else ""
        out.append({"url": url, "title": str(row.get("title") or "")[:160],
                    "page": page, "domain": domain,
                    "thumb": str(row.get("thumbnail") or "")})
    return out


def dedupe(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen = set()
    out = []
    for row in rows:
        key = re.sub(r"[?#].*$", "", row["url"]).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def subject_of(question: str) -> str:
    """What to search images for: the question minus the asking."""
    text = " ".join(str(question or "").split())
    text = re.sub(r"^(?:can (?:you|u)|could (?:you|u)|please|pls|hey|yo)\s+", "", text, flags=re.I)
    text = re.sub(r"^(?:show me|find me|give me|get me)\s+(?:some\s+|a few\s+)?"
                  r"(?:pictures?|picturs?|photos?|fotos?|images?|imges?|iamges?|imgs?|pics?)"
                  r"\s+(?:of\s+)?", "", text, flags=re.I)
    text = re.sub(r"^(?:who|what|where)\s+(?:is|was|are|were|'s)\s+", "", text, flags=re.I)
    text = re.sub(r"^tell me about\s+", "", text, flags=re.I)
    # Only the thing to picture: "the best th 12 base and tell me why it is
    # good" searched whole returns pictures of the words "tell me why".
    text = re.split(r"\s+(?:and|&|then|plus)\s+(?:tell|explain|how|why|what|give|guide|show|"
                    r"also|walk|teach|help)\b|\s*[,;:]\s*(?:and\s+)?(?:tell|explain|how|why)\b",
                    text, maxsplit=1, flags=re.I)[0]
    text = re.sub(r"^the\s+", "", text, flags=re.I)
    return text.strip(" ?.!") or str(question or "")


def pick(candidates: List[Dict[str, str]], localized: Dict[str, str], limit: int
         ) -> List[Dict[str, str]]:
    """The images to show, in candidate order, each now a local file."""
    out = []
    for row in candidates:
        local = localized.get(row["url"])
        if not local:
            continue
        out.append({"src": local, "page": row.get("page", ""),
                    "title": row.get("title", "")[:160], "domain": row.get("domain", "")})
        if len(out) >= limit:
            break
    return out


def plan(question: str, evidence: Any, *, want: int) -> List[Dict[str, str]]:
    """Candidates for this answer, before any are fetched."""
    rows = from_evidence(evidence)
    if explicit(question) or len(rows) < want:
        rows = (search(subject_of(question), limit=max(want + 4, 8)) + rows
                if explicit(question) else rows + search(subject_of(question), limit=want + 4))
    return dedupe(rows)


__all__ = ["wanted", "plan", "pick", "search", "from_evidence", "subject_of", "usable"]
