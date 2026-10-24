"""Market quotes: stocks, funds, indices, currencies and crypto.

Yahoo Finance's chart endpoint serves all five with one shape and no key, in
about 0.3s. A quote read here is exact and timestamped; a quote scraped from
a finance page is whatever that page rendered, and the measured baseline
answered "what is Nvidia trading at" with a closing price from 2024 while a
live quote page sat unopened in its results.

Symbols are resolved by Yahoo's own search, so "rolls royce" finds RR.L
without a hand-kept table of companies. Over-the-counter duplicates (ADRs,
grey-market lines) are skipped in favour of the primary listing.
"""
from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

import requests
import syrix_obs

_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_SEARCH = "https://query2.finance.yahoo.com/v1/finance/search"
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"),
    "Accept": "application/json",
}

# Secondary markets that list the same company again. A search for a UK or
# European name returns its US over-the-counter line first; the primary
# listing is the price people mean.
_SECONDARY = {"PNK", "OQB", "OQX", "OEM", "OTC", "YHD"}
_TRADABLE = {"EQUITY", "ETF", "INDEX", "MUTUALFUND", "CRYPTOCURRENCY", "CURRENCY", "FUTURE"}

_CURRENCY_SIGN = {"USD": "$", "GBP": "£", "EUR": "€", "JPY": "¥", "INR": "₹",
                  "CNY": "¥", "CAD": "C$", "AUD": "A$", "CHF": "CHF ", "HKD": "HK$"}


def resolve(query: str, timeout_s: float = 3.0) -> Optional[Dict[str, str]]:
    """The primary listing for a company, fund, index, coin or pair name."""
    resp = requests.get(_SEARCH, params={"q": query, "quotesCount": 8, "newsCount": 0},
                        headers=_HEADERS, timeout=timeout_s)
    resp.raise_for_status()
    quotes = [q for q in (resp.json().get("quotes") or [])
              if q.get("symbol") and str(q.get("quoteType") or "").upper() in _TRADABLE]
    if not quotes:
        return None
    primary = [q for q in quotes if str(q.get("exchange") or "").upper() not in _SECONDARY]
    best = (primary or quotes)[0]
    return {
        "symbol": str(best["symbol"]),
        "name": str(best.get("longname") or best.get("shortname") or best["symbol"]),
        "type": str(best.get("quoteType") or ""),
        "exchange": str(best.get("exchDisp") or best.get("exchange") or ""),
    }


def _chart(symbol: str, range_: str, interval: str, timeout_s: float) -> Dict[str, Any]:
    resp = requests.get(_CHART.format(symbol=symbol),
                        params={"range": range_, "interval": interval},
                        headers=_HEADERS, timeout=timeout_s)
    resp.raise_for_status()
    result = (resp.json().get("chart") or {}).get("result") or []
    if not result:
        raise ValueError(f"no chart for {symbol}")
    return result[0]


# The chart card's range tabs, fetched together so switching between them in
# the interface needs no further request. (range, interval, how to label a
# point's time).
_RANGES = (("1D", "1d", "5m", "time"), ("5D", "5d", "15m", "daytime"),
           ("1M", "1mo", "1d", "date"), ("6M", "6mo", "1d", "date"),
           ("1Y", "1y", "1wk", "month"))
_MAX_POINTS = 130


def _charts(symbol: str, timeout_s: float) -> Dict[str, Dict[str, Any]]:
    """Every range at once; the day chart is required, the rest are extras."""
    with ThreadPoolExecutor(max_workers=len(_RANGES), thread_name_prefix="v3-quote") as pool:
        futures = {key: pool.submit(_chart, symbol, rng, step, timeout_s)
                   for key, rng, step, _ in _RANGES}
    out: Dict[str, Dict[str, Any]] = {}
    for key, future in futures.items():
        try:
            out[key] = future.result()
        except (requests.RequestException, ValueError) as exc:
            if key == "1D":
                raise
            syrix_obs.report("v3.live.markets.range", exc, level="debug", range=key)
    return out


def _range(chart: Dict[str, Any], scale: float, offset_s: int, fmt: str) -> Dict[str, Any]:
    """One tab's line: prices and the exchange-local time of each point."""
    quote = ((chart.get("indicators") or {}).get("quote") or [{}])[0]
    rows = [(int(t), float(c)) for t, c in zip(chart.get("timestamp") or [], quote.get("close") or [])
            if c is not None]
    if len(rows) > _MAX_POINTS:
        step = math.ceil(len(rows) / _MAX_POINTS)
        rows = rows[::step] + ([rows[-1]] if (len(rows) - 1) % step else [])
    return {"series": [round(c * scale, 4) for _, c in rows],
            "times": [t + offset_s for t, _ in rows], "fmt": fmt}


def _first(values: List[Any]) -> Optional[float]:
    for value in values or []:
        if value is not None:
            return float(value)
    return None


def _money(value: Optional[float], currency: str, scale_of: Optional[float] = None) -> str:
    """``value`` as money. Decimals follow ``scale_of`` (the price) when given:
    a 21p move on a £15 share is £0.21, and written £0.2140 it was repeated
    that way in the answer."""
    if value is None:
        return "?"
    sign = _CURRENCY_SIGN.get(currency, "")
    ref = scale_of if scale_of is not None else value
    digits = 2 if abs(ref) >= 1 else 4
    text = f"{value:,.{digits}f}"
    return f"{sign}{text}" if sign else f"{text} {currency}".strip()


def _closes(chart: Dict[str, Any], scale: float) -> List[float]:
    quote = ((chart.get("indicators") or {}).get("quote") or [{}])[0]
    return [round(float(c) * scale, 6) for c in (quote.get("close") or []) if c is not None]


def _intraday(chart: Dict[str, Any], scale: float, offset_s: int) -> Dict[str, Any]:
    """Aligned closes, volumes and four time labels for the chart card."""
    quote = ((chart.get("indicators") or {}).get("quote") or [{}])[0]
    stamps = chart.get("timestamp") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []
    rows = [(t, c, (volumes[i] if i < len(volumes) else 0) or 0)
            for i, (t, c) in enumerate(zip(stamps, closes)) if c is not None]
    rows = rows[-120:]
    ticks = []
    if len(rows) >= 4:
        for frac in (0.0, 1 / 3, 2 / 3, 1.0):
            t = rows[min(len(rows) - 1, int(frac * (len(rows) - 1)))][0]
            ticks.append([round(frac, 3), time.strftime("%H:%M", time.gmtime(int(t) + offset_s))])
    return {
        "series": [round(float(c) * scale, 6) for _, c, _ in rows],
        "volumes": [int(v) for _, _, v in rows],
        "ticks": ticks,
    }


def quote(symbol: str, *, timeout_s: float = 4.0) -> Dict[str, Any]:
    """Price, change and an intraday line for one symbol. Raises on failure."""
    charts = _charts(symbol, timeout_s)
    day = charts["1D"]
    meta = day.get("meta") or {}
    currency = str(meta.get("currency") or "")
    # The London exchange quotes in pence ("GBp"). Reading that as pounds
    # overstates a UK share price a hundredfold.
    scale = 0.01 if currency == "GBp" else 1.0
    if currency == "GBp":
        currency = "GBP"
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    price = float(price) * scale if price is not None else None
    prev = float(prev) * scale if prev is not None else None
    change = (price - prev) if (price is not None and prev) else None
    change_pct = (change / prev * 100.0) if (change is not None and prev) else None

    def _since(key: str) -> Optional[float]:
        closes = _closes(charts[key], scale) if key in charts else []
        if closes and price is not None and closes[0]:
            return (price - closes[0]) / closes[0] * 100.0
        return None

    month_pct = _since("1M")
    offset = int(meta.get("gmtoffset") or 0)
    ranges = {key: _range(charts[key], scale, offset, fmt)
              for key, _, _, fmt in _RANGES if key in charts}
    ranges = {key: r for key, r in ranges.items() if len(r["series"]) >= 2}
    day_quote = ((day.get("indicators") or {}).get("quote") or [{}])[0]
    day_open = _first(day_quote.get("open"))
    regular = ((meta.get("currentTradingPeriod") or {}).get("regular") or {})
    now = time.time()
    market_open = bool(regular.get("start") and regular.get("end")
                       and regular["start"] <= now <= regular["end"])

    stamp = meta.get("regularMarketTime")
    return {
        "symbol": str(meta.get("symbol") or symbol),
        "name": str(meta.get("longName") or meta.get("shortName") or symbol),
        "exchange": str(meta.get("fullExchangeName") or meta.get("exchangeName") or ""),
        "currency": currency,
        "price": price,
        "previous_close": prev,
        "change": change,
        "change_pct": change_pct,
        "day_high": (float(meta["regularMarketDayHigh"]) * scale
                     if meta.get("regularMarketDayHigh") is not None else None),
        "day_low": (float(meta["regularMarketDayLow"]) * scale
                    if meta.get("regularMarketDayLow") is not None else None),
        "year_high": (float(meta["fiftyTwoWeekHigh"]) * scale
                      if meta.get("fiftyTwoWeekHigh") is not None else None),
        "year_low": (float(meta["fiftyTwoWeekLow"]) * scale
                     if meta.get("fiftyTwoWeekLow") is not None else None),
        "volume": meta.get("regularMarketVolume"),
        "month_change_pct": month_pct,
        "year_change_pct": _since("1Y"),
        "open": day_open * scale if day_open is not None else None,
        "market_open": market_open,
        "ranges": ranges,
        "market_time": (time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(int(stamp)))
                        if stamp else ""),
        "instrument": str(meta.get("instrumentType") or ""),
        "spark": _closes(day, scale)[-80:],
        "url": f"https://finance.yahoo.com/quote/{symbol}",
        **_intraday(day, scale, int(meta.get("gmtoffset") or 0)),
    }


def as_fact(q: Dict[str, Any], *, convert: Optional[Tuple[float, str, str]] = None
            ) -> Tuple[str, Dict[str, Any]]:
    """The evidence block and the card for one quote.

    ``convert`` is ``(amount, from, to)`` for a currency conversion question,
    worked out here so the model reports a computed figure rather than
    multiplying in its head.
    """
    # An exchange rate is a ratio, not an amount of money; pricing it in
    # pounds reads as "the dollar costs £0.75" with a pound sign on the change.
    cur = "" if q.get("instrument") == "CURRENCY" else q["currency"]
    # The state of the market is stated outright. Given only a last-trade
    # time, a quote from the close was reported "as of" the current clock.
    state = ("market open now" if q.get("market_open")
             else "market closed now; this is the latest close")
    parts = [
        f"LIVE QUOTE — {q['name']} ({q['symbol']}, {q['exchange']}), Yahoo Finance, "
        f"last trade {q['market_time'] or 'time not reported'} ({state}):",
        f"Price {_money(q['price'], cur)}",
    ]
    if q["change"] is not None:
        parts.append(f"change {_money(q['change'], cur, q['price'])} ({q['change_pct']:+.2f}%) "
                     f"against the previous close of {_money(q['previous_close'], cur)}")
    if q["day_low"] is not None and q["day_high"] is not None:
        parts.append(f"day range {_money(q['day_low'], cur)}–{_money(q['day_high'], cur)}")
    if q["year_low"] is not None and q["year_high"] is not None:
        parts.append(f"52-week range {_money(q['year_low'], cur)}–{_money(q['year_high'], cur)}")
    if q["month_change_pct"] is not None:
        parts.append(f"{q['month_change_pct']:+.2f}% over the past month")
    if q.get("volume"):
        parts.append(f"volume {int(q['volume']):,}")
    block = parts[0] + " " + "; ".join(parts[1:]) + "."
    if convert and q["price"] is not None:
        amount, src, dst = convert
        block += (f"\nCONVERSION (computed): {amount:,.2f} {src} = "
                  f"{amount * q['price']:,.2f} {dst} at {q['price']:.4f}.")

    card = {
        "type": "stock",
        "symbol": q["symbol"],
        "name": q["name"],
        "exchange": q["exchange"],
        "currency": _CURRENCY_SIGN.get(cur, cur + " " if cur else ""),
        "price": round(q["price"], 4 if (q["price"] or 0) < 1 else 2) if q["price"] is not None else None,
        "change": round(q["change"], 4) if q["change"] is not None else None,
        "change_pct": round(q["change_pct"], 2) if q["change_pct"] is not None else None,
        "day_high": q["day_high"],
        "day_low": q["day_low"],
        "prev_close": q["previous_close"],
        "month_change_pct": (round(q["month_change_pct"], 2)
                             if q["month_change_pct"] is not None else None),
        "year_change_pct": (round(q["year_change_pct"], 2)
                            if q.get("year_change_pct") is not None else None),
        "open": q.get("open"),
        "year_low": q["year_low"],
        "year_high": q["year_high"],
        "volume": q.get("volume"),
        "market_open": bool(q.get("market_open")),
        "ranges": q.get("ranges") or {},
        "market_time": q["market_time"],
        "spark": q["spark"],
        "sources": [{"url": q["url"], "title": "Yahoo Finance", "provider": "finance.yahoo.com"}],
    }
    sign = card["currency"]
    stats = []
    if q["previous_close"] is not None:
        stats.append(["Prev close", _money(q["previous_close"], cur)])
    if q["day_low"] is not None and q["day_high"] is not None:
        stats.append(["Day range", f"{_money(q['day_low'], cur)} – {_money(q['day_high'], cur)}"])
    if q["year_low"] is not None and q["year_high"] is not None:
        stats.append(["52-wk range", f"{_money(q['year_low'], cur)} – {_money(q['year_high'], cur)}"])
    if q["month_change_pct"] is not None:
        stats.append(["1 month", f"{q['month_change_pct']:+.2f}%"])
    if q.get("volume"):
        stats.append(["Volume", f"{int(q['volume']):,}"])
    card.update({
        "series": q.get("series") or [],
        "volumes": q.get("volumes") or [],
        "ticks": q.get("ticks") or [],
        "stats": stats[:6],
        "change_abs": round(q["change"], 4) if q["change"] is not None else None,
        "as_of": q["market_time"],
        "currency": sign,
    })
    if convert and q["price"] is not None:
        amount, src, dst = convert
        card["conversion"] = {"amount": amount, "from": src, "to": dst,
                              "result": round(amount * q["price"], 2)}
    return block, card
