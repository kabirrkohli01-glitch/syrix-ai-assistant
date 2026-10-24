"""Live data that has an exact answer: weather, quotes, currencies, the time.

A question like "what's the weather in London" or "how is NVDA doing" has one
correct figure and a source that publishes it directly. Searching the web for
it hands the model several pages' readings of that figure, taken at different
times, to reconcile — and when the pages do not open in time, nothing at all,
at which point a model produces a plausible reading of its own. Both were
measured: two sites' temperatures reported side by side, and on another run
a confident "18 °C, light rain" with no source behind it.

So these questions are recognised in code and read from the source, at t=0,
in parallel with anything else the turn gathers. The reading goes into the
prompt as the first evidence, and the same reading builds the card under the
answer — so the card and the sentence cannot disagree.

Detection is deliberately narrow. A question it misses still gets a web
search; a question it wrongly catches gets a weather report instead of an
answer, which is worse. "What causes rain" is a science question.
"""
from __future__ import annotations

import concurrent.futures as cf
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

from . import markets, weather

WEATHER = "weather"
QUOTE = "quote"
CRYPTO = "crypto"
FX = "fx"
CLOCK = "clock"


@dataclass
class Ask:
    kind: str
    subjects: List[str] = field(default_factory=list)
    days: int = 3
    convert: Optional[Tuple[float, str, str]] = None
    # Whether the reading is the whole answer. When it is, a web search can
    # add nothing but noise and latency.
    complete: bool = True


@dataclass
class Fact:
    kind: str
    block: str
    card: Dict[str, Any]
    sources: List[Dict[str, Any]]
    label: str
    complete: bool = True
    took_ms: float = 0.0


# ---------------------------------------------------------------- detection

_EXPLAINS = re.compile(
    r"\b(?:why|how does|how do|what causes|what is the (?:science|reason)|explain|"
    r"history of|definition|meaning of|climate|global warming)\b", re.I)

_WEATHER = re.compile(
    r"\b(?:weather|forecast|temperature outside|how (?:hot|cold|warm) (?:is it|will it)|"
    r"will it (?:rain|snow|be (?:hot|cold|sunny|warm|windy))|is it (?:raining|snowing|sunny|cold|hot)|"
    r"do i need (?:an? )?(?:umbrella|coat|jacket)|rain(?:ing)? (?:today|tomorrow|tonight)|"
    r"chance of rain)\b", re.I)

_PLACE_AFTER = re.compile(
    r"\b(?:in|for|at|around|near|over)\s+(?:the\s+)?"
    r"([a-z][a-z .'\-]{1,40}?(?:,\s*[a-z][a-z .'\-]{1,30}?)?)"
    r"(?=\s+(?:today|tomorrow|tonight|this|next|now|right now|on|at|over|for|in the|"
    r"later|like|currently|please|pls|rn|be)\b|\s*[?.!,]|\s*$)", re.I)

_PLACE_BEFORE = re.compile(
    r"^(?:what(?:'s| is)\s+)?(?:the\s+)?([a-z][a-z .'\-]{1,30}?)\s+(?:weather|forecast)\b", re.I)

_NOT_PLACES = {"today", "tomorrow", "the week", "week", "weekend", "the weekend", "general",
               "the moment", "my area", "here", "my city", "my location", "the uk", "uk",
               "celsius", "fahrenheit", "detail", "the morning", "the evening", "the afternoon"}

_PRICE_CONTEXT = re.compile(
    r"\b(?:stock|stocks|share price|shares?|stock price|price of|price for|trading at|trading|"
    r"ticker|market cap|quote|how(?:\s+is|\s+are|'s)\s+.{1,40}?\s+doing|up or down|worth right now|"
    r"how much is .{1,30} worth|price|valuation|chart|today|right now|points|level)\b", re.I)

# Words that make a company name a request for its share price. "How is Apple
# doing" alone could be about anything; with one of these it is a quote.
_FINANCE = re.compile(
    r"\b(?:stock|stocks|shares?|share price|stock price|trading|ticker|market cap|quote|"
    r"price|valuation|chart|investors?|nasdaq|nyse|lse)\b", re.I)

# A year in the past makes it history, not a quote: "the stock market crash
# of 1929".
_PAST_YEAR = re.compile(r"\b(?:1[5-9]\d\d|20[01]\d)\b")

_PRICE_ONLY = re.compile(
    r"\b(?:price|trading at|quote|how much is|worth|rate|value)\b", re.I)
_WANTS_CONTEXT = re.compile(
    r"\b(?:doing|why|news|should i|outlook|analysis|forecast|prediction|buy|sell|"
    r"earnings|compare|vs|versus)\b", re.I)

_TICKER = re.compile(r"(?:^|\s)\$?([A-Z]{2,5}(?:\.[A-Z]{1,2})?)(?=[\s?.!,']|$)")
_NOT_TICKERS = {"UK", "US", "USA", "AI", "CEO", "CFO", "EU", "ETF", "IPO", "GDP", "NHS",
                "BBC", "OK", "PM", "AM", "UTC", "GMT", "BST", "API", "LLM", "GPU", "CPU",
                "FTSE", "DOW", "WHAT", "HOW", "IS", "THE", "SYRIX", "NBA", "NFL", "PDF"}

_INDICES = [
    (re.compile(r"\b(?:s ?& ?p(?: ?500)?|sp ?500|spx)\b", re.I), "^GSPC"),
    (re.compile(r"\bdow(?: jones)?\b", re.I), "^DJI"),
    (re.compile(r"\bnasdaq(?: composite)?\b", re.I), "^IXIC"),
    (re.compile(r"\bftse(?: ?100)?\b", re.I), "^FTSE"),
    (re.compile(r"\bftse ?250\b", re.I), "^FTMC"),
    (re.compile(r"\bnikkei\b", re.I), "^N225"),
    (re.compile(r"\bdax\b", re.I), "^GDAXI"),
    (re.compile(r"\b(?:the )?(?:stock )?markets?\b(?= (?:today|doing|now|right now))", re.I), "^GSPC ^FTSE"),
]

_COINS = {
    "bitcoin": "BTC", "btc": "BTC", "ethereum": "ETH", "eth": "ETH", "ether": "ETH",
    "solana": "SOL", "dogecoin": "DOGE", "doge": "DOGE", "xrp": "XRP", "ripple": "XRP",
    "cardano": "ADA", "litecoin": "LTC", "bnb": "BNB", "polkadot": "DOT", "tron": "TRX",
    "shiba inu": "SHIB", "avalanche": "AVAX", "chainlink": "LINK", "toncoin": "TON",
}
_COIN = re.compile(r"\b(" + "|".join(sorted(map(re.escape, _COINS), key=len, reverse=True)) + r")\b", re.I)

_CURRENCIES = {
    "usd": "USD", "dollar": "USD", "dollars": "USD", "us dollar": "USD", "us dollars": "USD",
    "gbp": "GBP", "pound": "GBP", "pounds": "GBP", "sterling": "GBP", "quid": "GBP",
    "eur": "EUR", "euro": "EUR", "euros": "EUR", "jpy": "JPY", "yen": "JPY",
    "inr": "INR", "rupee": "INR", "rupees": "INR", "cad": "CAD", "canadian dollars": "CAD",
    "aud": "AUD", "australian dollars": "AUD", "chf": "CHF", "swiss francs": "CHF",
    "cny": "CNY", "yuan": "CNY", "aed": "AED", "dirham": "AED", "dirhams": "AED",
}
_CUR = "|".join(sorted(map(re.escape, _CURRENCIES), key=len, reverse=True))
_FX = re.compile(
    r"(?:(\d[\d,]*(?:\.\d+)?)\s*)?(?:[£$€¥₹]\s*)?\b(" + _CUR + r")\b\s*(?:to|in|into|->|/|vs|against)\s*\b(" + _CUR + r")\b",
    re.I)
_FX_AMOUNT_SIGN = re.compile(r"([£$€¥₹])\s*(\d[\d,]*(?:\.\d+)?)\s*(?:to|in|into)\s*\b(" + _CUR + r")\b", re.I)
_SIGN_CUR = {"£": "GBP", "$": "USD", "€": "EUR", "¥": "JPY", "₹": "INR"}
_FX_CONTEXT = re.compile(r"\b(?:exchange rate|convert|conversion|rate|how much is|worth in|to)\b", re.I)

_CLOCK = re.compile(
    r"\b(?:what(?:'s| is)?\s+(?:the\s+)?(?:current\s+|local\s+)?time|time is it|current time|local time)\b"
    r".*?\b(?:in|at)\s+([a-z][a-z .'\-]{1,40}?)\s*[?.!]*$", re.I)


def _clean_place(raw: str) -> str:
    place = " ".join(str(raw or "").strip(" .,'\"").split())
    place = re.sub(r"^(?:the)\s+", "", place, flags=re.I)
    return "" if place.lower() in _NOT_PLACES else place


def _weather_days(text: str) -> int:
    low = text.lower()
    if re.search(r"\b(?:week|weekend|next few days|7[- ]day|five[- ]day|5[- ]day|coming days)\b", low):
        return 7
    return 3


def _detect_weather(text: str) -> Optional[Ask]:
    if not _WEATHER.search(text) or _EXPLAINS.search(text):
        return None
    place = ""
    for match in _PLACE_AFTER.finditer(text):
        candidate = _clean_place(match.group(1))
        if candidate:
            place = candidate
            break
    if not place:
        before = _PLACE_BEFORE.search(text.strip())
        if before:
            place = _clean_place(before.group(1))
            if place.lower() in ("whats", "what", "the", "todays", "today's", "tomorrows"):
                place = ""
    return Ask(WEATHER, [place] if place else [], days=_weather_days(text))


def _detect_fx(text: str) -> Optional[Ask]:
    match = _FX.search(text)
    amount = None
    if match:
        src, dst = _CURRENCIES[match.group(2).lower()], _CURRENCIES[match.group(3).lower()]
        if match.group(1):
            amount = float(match.group(1).replace(",", ""))
    else:
        signed = _FX_AMOUNT_SIGN.search(text)
        if not signed:
            return None
        src, dst = _SIGN_CUR[signed.group(1)], _CURRENCIES[signed.group(3).lower()]
        amount = float(signed.group(2).replace(",", ""))
    if src == dst:
        return None
    return Ask(FX, [f"{src}{dst}=X"], convert=(amount, src, dst) if amount else None)


def _detect_crypto(text: str) -> Optional[Ask]:
    match = _COIN.search(text)
    if not match or not _PRICE_CONTEXT.search(text) or _EXPLAINS.search(text):
        return None
    coin = _COINS[match.group(1).lower()]
    fiat = "GBP" if re.search(r"\b(?:gbp|pounds?|sterling|£)", text, re.I) else (
        "EUR" if re.search(r"\b(?:eur|euros?|€)", text, re.I) else "USD")
    return Ask(CRYPTO, [f"{coin}-{fiat}"],
               complete=bool(_PRICE_ONLY.search(text)) and not _WANTS_CONTEXT.search(text))


def _quote_subject(text: str) -> str:
    patterns = [
        r"\b(?:a share|one share|shares|stock) (?:of|in)\s+(.{2,40}?)(?:\s+(?:today|now|right now|worth|cost)\b|[?.!,]|$)",
        r"\b(?:price of|stock price of|share price of|shares of|stock of|quote for|price for)\s+(.{2,40}?)(?:\s+(?:stock|shares?|today|now|right now)\b|[?.!,]|$)",
        r"\bhow (?:is|are|'s)\s+(.{2,40}?)\s+(?:stock\s+|shares?\s+)?doing\b",
        r"\bwhat(?:'s| is)\s+(.{2,40}?)\s+trading at\b",
        r"\b(.{2,40}?)\s+(?:stock|share|shares)(?:\s+price)?\b",
        r"\b(.{2,40}?)\s+(?:market cap|ticker)\b",
    ]
    for pat in patterns:
        match = re.search(pat, text, re.I)
        if match:
            subject = re.sub(r"^(?:what(?:'s| is)|how(?:'s| is)|the|is|are|can (?:u|you) tell me|"
                             r"tell me(?: about)?|about|show me|check)\s+",
                             "", match.group(1).strip(" ?.!,"), flags=re.I)
            subject = re.sub(r"^(?:the)\s+", "", subject, flags=re.I).strip()
            # "Apple's share price": the listing is Apple, not "Apple's".
            subject = re.sub(r"['’]s$", "", subject).strip()
            if subject and subject.lower() not in ("the", "it", "that", "this", "a", "my"):
                return subject
    return ""


def _detect_quote(text: str) -> Optional[Ask]:
    if not _PRICE_CONTEXT.search(text) or _PAST_YEAR.search(text) or _EXPLAINS.search(text):
        return None
    symbols: List[str] = []
    for pattern, symbol in _INDICES:
        if pattern.search(text):
            symbols.extend(symbol.split())
    # A capitalised token is a ticker only in a sentence about markets: "is
    # AWS down today" is an outage question, "how is NVDA doing" is a quote.
    if not symbols and (_FINANCE.search(text) or re.search(r"\b(?:doing|up or down)\b", text, re.I)):
        for match in _TICKER.finditer(text):
            token = match.group(1)
            if token not in _NOT_TICKERS:
                symbols.append(token)
                break
    if not symbols and _FINANCE.search(text):
        # A company named in words is only a quote request when the question
        # is plainly about its shares, and the listing it resolves to must
        # carry the name that was asked for (checked at fetch time).
        subject = _quote_subject(text)
        if subject:
            symbols.append("?" + subject)
    if not symbols:
        return None
    return Ask(QUOTE, symbols[:3],
               complete=bool(_PRICE_ONLY.search(text)) and not _WANTS_CONTEXT.search(text))


def _detect_clock(text: str) -> Optional[Ask]:
    match = _CLOCK.search(text.strip())
    if not match:
        return None
    place = _clean_place(match.group(1))
    return Ask(CLOCK, [place]) if place else None


def detect(text: str) -> Optional[Ask]:
    """What live reading, if any, answers this question."""
    text = " ".join(str(text or "").split())
    if not text or len(text) > 400:
        return None
    for detector in (_detect_clock, _detect_weather, _detect_fx, _detect_crypto, _detect_quote):
        ask = detector(text)
        if ask:
            return ask
    return None


# ---------------------------------------------------------------- fetching

_cache: Dict[str, Tuple[float, Fact]] = {}
_CACHE_S = 60.0


def _clock_fact(place: str, timeout_s: float) -> Optional[Fact]:
    hit = weather.geocode(place, timeout_s)
    if not hit:
        return None
    resp = requests.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": hit["latitude"], "longitude": hit["longitude"],
        "current": "is_day", "timezone": "auto"}, timeout=timeout_s)
    resp.raise_for_status()
    data = resp.json()
    offset = int(data.get("utc_offset_seconds") or 0)
    local = datetime.now(timezone.utc) + timedelta(seconds=offset)
    label = ", ".join(p for p in (hit.get("name"), hit.get("country")) if p)
    sign = "+" if offset >= 0 else "-"
    hours, minutes = divmod(abs(offset) // 60, 60)
    utc = f"UTC{sign}{hours:02d}:{minutes:02d}"
    block = (f"LOCAL TIME — {label}: {local.strftime('%H:%M on %A %d %B %Y')} "
             f"({data.get('timezone_abbreviation') or data.get('timezone')}, {utc}).")
    card = {"type": "clock", "location": label, "time": local.strftime("%H:%M"),
            "date": local.strftime("%A %d %B %Y"), "zone": utc,
            "sources": [weather.source()]}
    return Fact(CLOCK, block, card, [weather.source()], f"Checked the time in {label}")


def _quote_facts(symbols: List[str], timeout_s: float, convert=None) -> List[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
    resolved: List[str] = []
    for symbol in symbols:
        if symbol.startswith("?"):
            asked = symbol[1:]
            hit = markets.resolve(asked, timeout_s)
            if hit and _same_name(asked, hit):
                resolved.append(hit["symbol"])
        else:
            resolved.append(symbol)
    out = []
    with cf.ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(markets.quote, s, timeout_s=timeout_s) for s in resolved[:3]]
        for future in futures:
            try:
                q = future.result(timeout=timeout_s + 1.0)
            except Exception:
                continue
            block, card = markets.as_fact(q, convert=convert)
            out.append((block, card, card["sources"][0]))
    return out


def _same_name(asked: str, hit: Dict[str, str]) -> bool:
    """Does the listing carry the name that was asked for?

    Yahoo's search returns its best guess for any string at all, so a name
    that is not a company still comes back with a ticker attached.
    """
    words = [w for w in re.findall(r"[a-z0-9]+", asked.lower()) if len(w) >= 2]
    if not words:
        return False
    haystack = re.sub(r"[^a-z0-9]+", " ", f"{hit.get('name', '')} {hit.get('symbol', '')}".lower())
    return words[0] in haystack.split() or words[0] in haystack.replace(" ", "")


def fetch(ask: Ask, *, timeout_s: float = 4.0) -> Optional[Fact]:
    """Read the answer from its source. None when it could not be read."""
    key = f"{ask.kind}|{'|'.join(ask.subjects)}|{ask.days}|{ask.convert}"
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_S:
        return hit[1]
    started = time.monotonic()
    fact: Optional[Fact] = None
    try:
        if ask.kind == WEATHER:
            data = weather.fetch(ask.subjects[0] if ask.subjects else "",
                                 days=ask.days, timeout_s=timeout_s)
            if data:
                block, card = weather.as_fact(data)
                fact = Fact(WEATHER, block, card, [weather.source()],
                            f"Read the forecast for {card['location']}")
        elif ask.kind == CLOCK:
            fact = _clock_fact(ask.subjects[0], timeout_s)
        elif ask.kind in (QUOTE, CRYPTO, FX):
            rows = _quote_facts(ask.subjects, timeout_s, convert=ask.convert)
            if rows:
                block = "\n".join(r[0] for r in rows)
                card = rows[0][1]
                if len(rows) > 1:
                    # Only the first quote draws a chart. The host drops a
                    # card over 12KB, and three intraday series with their
                    # volumes come close to that on their own.
                    card = dict(card)
                    card["others"] = [
                        {k: v for k, v in r[1].items()
                         if k not in ("series", "volumes", "ticks", "spark", "stats")}
                        for r in rows[1:]]
                label = "Read the live quote for " + ", ".join(r[1]["symbol"] for r in rows)
                fact = Fact(ask.kind, block, card, [r[2] for r in rows], label)
    except (requests.RequestException, ValueError, KeyError, IndexError):
        fact = None
    if fact is None:
        return None
    fact.complete = ask.complete
    fact.took_ms = round((time.monotonic() - started) * 1000.0, 1)
    _cache[key] = (time.monotonic(), fact)
    return fact
