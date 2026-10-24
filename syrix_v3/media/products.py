"""Product cards for a shopping answer: a picture, a price, a real link.

Written after the answer, from the products the answer actually recommends,
so a card can never show something the prose did not choose. Each product is
looked up once — a search for a retailer listing and an image search, both
concurrent — and a card is kept only when it has a real link.

Prices are what the retailer's listing shows at search time. They are marked
as such on the card, because a price read from a search snippet is a quote,
not a guarantee.
"""
from __future__ import annotations

import concurrent.futures as cf
import re
import time
from typing import Dict, List, Optional

_SHOPPING = re.compile(
    r"\b(?:best|top \d+|top ten|cheapest|budget|recommend(?:ation)?s?|which .{0,30}(?:should i|to) "
    r"(?:buy|get|pick)|worth (?:buying|it)|under [£$€]?\d|for under|buy(?:ing)?|deals?|"
    r"alternatives? to|vs\.?|versus|compare)\b", re.I)

_GOODS = re.compile(
    r"\b(?:laptops?|notebooks?|macbooks?|phones?|smartphones?|iphones?|headphones|earbuds|"
    r"earphones|speakers?|cameras?|lens(?:es)?|watch(?:es)?|smartwatch(?:es)?|tvs?|televisions?|"
    r"monitors?|consoles?|tablets?|ipads?|keyboards?|mice|mouse|gpus?|graphics cards?|cpus?|"
    r"processors?|routers?|drones?|e-?readers?|kindles?|printers?|chairs?|desks?|mattress(?:es)?|"
    r"vacuums?|hoovers?|blenders?|air fryers?|coffee machines?|kettles?|bikes?|e-?bikes?|scooters?|"
    r"shoes|trainers|sneakers|boots|jackets?|backpacks?|bags?|perfumes?|fragrances?|"
    r"skincare|moisturi[sz]ers?|toothbrush(?:es)?|cars?|tyres?|tires?|ssds?|hard drives?|"
    r"microphones?|mics?|webcams?|projectors?|gaming pcs?|pcs?|desktops?)\b", re.I)

_RETAILERS = (
    "amazon.co.uk", "amazon.com", "argos.co.uk", "currys.co.uk", "johnlewis.com",
    "very.co.uk", "ao.com", "scan.co.uk", "box.co.uk", "ebuyer.com", "laptopsdirect.co.uk",
    "ebay.co.uk", "ebay.com", "bestbuy.com", "walmart.com", "newegg.com", "apple.com",
    "dell.com", "lenovo.com", "hp.com", "samsung.com", "microsoft.com", "asus.com",
    "acer.com", "backmarket.co.uk", "uk.webuy.com", "richersounds.com", "halfords.com",
    "decathlon.co.uk", "jdsports.co.uk", "nike.com", "adidas.co.uk", "sony.co.uk",
    "bose.co.uk", "sonos.com", "boots.com", "superdrug.com", "sportsdirect.com",
    "overclockers.co.uk", "cclonline.com", "costco.co.uk", "target.com", "bhphotovideo.com",
)

_PRICE = re.compile(r"(?:[£$€]\s?\d[\d,]*(?:\.\d{2})?)")

_BOLD = re.compile(r"\*\*([^*\n]{3,90})\*\*")
_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$", re.M)
_HEAD_NAME = re.compile(r"^\s*#{2,4}\s+(?:\d+[.)]\s*)?([^\n#]{3,80})$", re.M)
_LIST_NAME = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+\*\*([^*\n]{3,90})\*\*", re.M)
# A product name looks like one: a model code with a digit ("WH-1000XM5",
# "XPS 13") or at least two capitalised words ("Bose QuietComfort Ultra").
# "Feature set" and "Sound quality vs. price" are the bold subheadings a
# shopping answer is also full of, and neither passes.
_MODEL_CODE = re.compile(r"\b[A-Za-z]*\d[\w-]*\b")
_CAPITALISED = re.compile(r"\b[A-Z][\w'’-]+")

_NOT_NAMES = re.compile(
    r"^(?:why|how|what|best|bottom line|verdict|summary|pros|cons|price|prices|laptop|model|"
    r"option|pick|budget|note|tip|overall|key|specs?|trade-?offs?|performance|battery|display|"
    r"conclusion|recommendation|alternatives?|rank|approx|typical|who|the |for |if |my )",
    re.I)


def wanted(question: str) -> bool:
    text = str(question or "")
    return bool(_SHOPPING.search(text) and _GOODS.search(text))


def _clean(name: str) -> str:
    name = re.sub(r"\([^)]*\)", "", name)          # specs in brackets
    name = re.sub(r"\s*[–—:-]\s.*$", "", name)       # "Name – reason"
    name = re.sub(r"[*_`]", "", name)
    return " ".join(name.split()).strip(" .,:;")


def _product_like(name: str) -> bool:
    words = name.split()
    if len(words) < 2 or len(words) > 9 or _NOT_NAMES.match(name):
        return False
    if re.search(r"[£$€%]", name):
        return False
    return bool(_MODEL_CODE.search(name)) or len(_CAPITALISED.findall(name)) >= 2


def _table_names(answer: str) -> List[str]:
    """The first bold cell of each table row, or its first naming cell."""
    out = []
    for match in _TABLE_ROW.finditer(answer or ""):
        cells = [c.strip() for c in match.group(1).split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        bold = _BOLD.search(match.group(1))
        if bold:
            out.append(bold.group(1))
            continue
        for cell in cells:
            if cell and not re.fullmatch(r"[#\d.\s]+", cell):
                out.append(cell)
                break
    return out


def names_from(answer: str, limit: int = 5) -> List[str]:
    """The products the answer names, in the order it names them."""
    found: List[str] = []
    sources = (
        _table_names(answer),
        [m.group(1) for m in _LIST_NAME.finditer(answer or "")],
        [m.group(1) for m in _HEAD_NAME.finditer(answer or "")],
        [m.group(1) for m in _BOLD.finditer(answer or "")],
    )
    for candidates in sources:
        for raw in candidates:
            name = _clean(raw)
            if not _product_like(name):
                continue
            if name.lower() in (f.lower() for f in found):
                continue
            found.append(name)
            if len(found) >= limit:
                return found
        if len(found) >= 3:
            break
    return found


def _domain(url: str) -> str:
    try:
        host = url.split("/")[2].lower()
    except IndexError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _retailer(domain: str) -> bool:
    return any(domain == r or domain.endswith("." + r) for r in _RETAILERS)


def _listing(name: str, timeout_s: float) -> Optional[Dict[str, str]]:
    from ..compat import ddgs as _ddgs
    rows = []
    for engine in ("bing", "duckduckgo"):
        try:
            with _ddgs()(timeout=max(1, int(timeout_s))) as client:
                rows = client.text(f"{name} price", max_results=10, region="uk-en",
                                   backend=engine) or []
        except Exception:
            rows = []
        if rows:
            break
    if not rows:
        return None
    best = None
    for row in rows or []:
        url = str(row.get("href") or "")
        domain = _domain(url)
        if not url.startswith("https://") or not domain:
            continue
        text = f"{row.get('title', '')} {row.get('body', '')}"
        price = _PRICE.search(text)
        entry = {"url": url, "merchant": domain, "price": price.group(0).replace(" ", "") if price else "",
                 "title": str(row.get("title") or "")[:140]}
        if _retailer(domain):
            if price or best is None or not _retailer(best["merchant"]):
                best = entry
                if price:
                    break
        elif best is None:
            best = entry
    return best


def lookup(names: List[str], *, deadline_s: float = 4.0) -> List[Dict[str, str]]:
    """A card per product that has a real link. Images are fetched by the caller."""
    from . import images as _images
    if not names:
        return []
    ends = time.monotonic() + deadline_s
    pool = cf.ThreadPoolExecutor(max_workers=min(10, 2 * len(names)), thread_name_prefix="v3-shop")
    listings = {n: pool.submit(_listing, n, deadline_s) for n in names}
    pictures = {n: pool.submit(_images.search, n, limit=3, timeout_s=deadline_s) for n in names}
    cards: List[Dict[str, str]] = []
    try:
        for name in names:
            left = max(0.05, ends - time.monotonic())
            try:
                listing = listings[name].result(timeout=left)
            except Exception:
                listing = None
            if not listing:
                continue
            try:
                pics = pictures[name].result(timeout=max(0.05, ends - time.monotonic()))
            except Exception:
                pics = []
            cards.append({
                "name": name,
                "url": listing["url"],
                "merchant": listing["merchant"],
                "price": listing["price"],
                "image_url": pics[0]["url"] if pics else "",
            })
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return cards
