"""A named organisation's official website, found rather than guessed.

"Go to the nvda website" names a company, not an address. The address comes
from Wikidata's "official website" property (P856), which is maintained,
keyless and answers in well under a second, and never from a guessed
``www.<name>.com``: a guess that happens to resolve is someone else's site
stated as the company's.

The trap is ambiguity. "nvda" is a chipmaker's ticker and also a screen
reader, and Wikidata's own search ranks the screen reader first. A name shaped
like a ticker is checked against the candidates' own stock listings (P414 with
its ticker qualifier, P249), so the company that trades as NVDA wins on the
registry's word; a market-data lookup of the ticker is the second opinion.
Otherwise an organisation-shaped description beats any other. When the
registry has no answer, a web search for the official site is accepted only
if the result plainly belongs to the name.

Two doors to the registry, because one was measured failing: the SPARQL query
service answers search and website in one request, and on 2026-09-24 stopped
answering entirely, even to a trivial query, for long enough to matter. The
ordinary API is a separate service; it is slower here only because each
claim is its own request, made one at a time since concurrent claims requests
were answered 429. What is found is kept on disk, so a name is looked up once.
"""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Dict, List, Tuple
from urllib.parse import urlsplit

from .site import _STOP, host_of, registrable

_HEADERS = {"User-Agent": "SYRIX/3 (personal assistant; site lookup)"}
# What an organisation's description looks like, as opposed to a product's,
# a film's or a unit of measurement's. "nvda" is both a chipmaker's ticker and
# a screen reader; only one of them is what someone means by its website.
_ORG = re.compile(
    r"\b(?:company|corporation|conglomerate|manufacturer|business|brand|retailer|"
    r"organi[sz]ation|university|college|school|institute|broadcaster|newspaper|"
    r"magazine|agency|department|ministry|government|council|charity|foundation|"
    r"club|team|league|airline|bank|firm|studio|publisher|platform|service|website|"
    r"marketplace|start-?up|laboratory|hospital|museum|restaurant|chain|operator|"
    r"exchange|non-?profit|association|federation|society|union|party|network|"
    r"developer|maker|producer|provider|group|holding|enterprise|supermarket|carrier)\b",
    re.I)
_CACHE: Dict[str, Tuple[float, "Resolved"]] = {}
_CACHE_S = 24 * 3600.0
_CACHE_LOCK = threading.Lock()


@dataclass
class Resolved:
    url: str = ""
    label: str = ""
    how: str = ""

    def __bool__(self) -> bool:
        return bool(self.url)


def _get_json(url: str, params: Dict[str, str], timeout_s: float):
    import requests
    resp = requests.get(url, params=params, headers=_HEADERS, timeout=timeout_s)
    resp.raise_for_status()
    return resp.json()


_API = "https://www.wikidata.org/w/api.php"
_SPARQL = "https://query.wikidata.org/sparql"
# Search and the official-website property in one request, when the query
# service is answering: 0.4-1.2s measured, against an entity search plus one
# claims request per candidate.
_QUERY = """SELECT ?item ?num ?itemLabel ?itemDescription ?site ?rank ?lang WHERE {
  SERVICE wikibase:mwapi {
    bd:serviceParam wikibase:endpoint "www.wikidata.org"; wikibase:api "EntitySearch";
                    mwapi:search "%s"; mwapi:language "en"; mwapi:limit "6".
    ?item wikibase:apiOutputItem mwapi:item. ?num wikibase:apiOrdinal true. }
  ?item p:P856 ?st. ?st ps:P856 ?site; wikibase:rank ?rank.
  OPTIONAL { ?st pq:P407 ?lang }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
} ORDER BY ?num LIMIT 300"""
_SPARQL_S = 2.0
_ENGLISH = "Q1860"
_TICKER_SHAPED = re.compile(r"[A-Za-z]{1,5}(?:\.[A-Za-z]{1,2})?")
_REMEMBER_S = 30 * 86400.0
_REMEMBERED = "official_sites.json"


def _address_order(row: Dict[str, str]) -> Tuple[int, int, int, int]:
    """Which of one entity's official addresses is the site meant.

    Measured: Nvidia's first listed address is its Taiwanese site, Apple's is
    its Jordanian store and The Guardian's is a Tor address. Wikidata marks
    the primary one "preferred" where an editor has said so, and qualifies
    regional ones with a language (P407), so a preferred, English or
    unqualified, shortest address is the one a person means by "their site".
    """
    rank = row.get("rank", "")
    url = row.get("site", "")
    return (0 if rank.endswith("PreferredRank") else 1,
            0 if (not row.get("lang") or row["lang"].endswith("/" + _ENGLISH)) else 1,
            1 if ".onion" in url else 0,
            len(url))


def _candidates(name: str, timeout_s: float) -> List[Dict[str, str]]:
    """Search hits with an official website, in the engine's order (SPARQL)."""
    # Letters, digits and the punctuation names use; nothing that can close
    # the SPARQL string it is placed in.
    safe = re.sub(r"[^\w &.'-]", " ", name)[:80]
    data = _get_json(_SPARQL, {"query": _QUERY % safe, "format": "json"}, timeout_s)
    grouped: Dict[str, Dict[str, object]] = {}
    for row in (data.get("results") or {}).get("bindings") or []:
        flat = {k: str((v or {}).get("value") or "") for k, v in row.items()}
        if flat.get("rank", "").endswith("DeprecatedRank") or not flat.get("site", "").startswith("http"):
            continue
        qid = flat.get("item", "").rsplit("/", 1)[-1]
        entry = grouped.setdefault(qid, {"id": qid, "order": int(flat.get("num") or 99),
                                         "label": flat.get("itemLabel", ""),
                                         "description": flat.get("itemDescription", ""),
                                         "rows": []})
        entry["rows"].append(flat)
    out = []
    for entry in sorted(grouped.values(), key=lambda e: e["order"]):
        best = sorted(entry.pop("rows"), key=_address_order)[0]
        entry["url"] = best["site"]
        out.append(entry)
    return out


def _api_candidates(name: str, timeout_s: float) -> List[Dict[str, str]]:
    """Search hits from the ordinary API. Addresses are fetched later, per pick."""
    found = _get_json(_API, {"action": "wbsearchentities", "search": name[:80],
                             "language": "en", "limit": "6", "format": "json"}, timeout_s)
    return [{"id": hit["id"], "order": i, "label": str(hit.get("label") or ""),
             "description": str(hit.get("description") or ""), "url": ""}
            for i, hit in enumerate(found.get("search") or []) if hit.get("id")]


def _claims(qid: str, prop: str, timeout_s: float) -> List[Dict]:
    data = _get_json(_API, {"action": "wbgetclaims", "entity": qid, "property": prop,
                            "format": "json"}, timeout_s)
    return (data.get("claims") or {}).get(prop) or []


def _website(qid: str, timeout_s: float) -> str:
    """The entity's primary official address, read from its claims."""
    rows = []
    for claim in _claims(qid, "P856", timeout_s):
        value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if not isinstance(value, str) or not value.startswith("http"):
            continue
        rank = str(claim.get("rank") or "")
        if rank == "deprecated":
            continue
        langs = [((q.get("datavalue") or {}).get("value") or {}).get("id") or ""
                 for q in (claim.get("qualifiers") or {}).get("P407") or []]
        rows.append({"site": value, "rank": "PreferredRank" if rank == "preferred" else "NormalRank",
                     "lang": ("/" + langs[0]) if langs else ""})
    return sorted(rows, key=_address_order)[0]["site"] if rows else ""


def _tickers(qid: str, timeout_s: float) -> set:
    """The ticker symbols of an entity's stock listings (P414, qualifier P249)."""
    out = set()
    for claim in _claims(qid, "P414", timeout_s):
        for qual in (claim.get("qualifiers") or {}).get("P249") or []:
            value = (qual.get("datavalue") or {}).get("value")
            if isinstance(value, str):
                out.add(value.strip().upper())
    return out


def _ticker_company(name: str, timeout_s: float) -> str:
    """The company behind a ticker, from market data — the second opinion."""
    if not _TICKER_SHAPED.fullmatch(name):
        return ""
    try:
        from ..live import markets
        hit = markets.resolve(name, timeout_s)
    except Exception:
        return ""
    # An equity only: "BBC" is also a fund's symbol, and a fund's website is
    # not what anyone means by the BBC's.
    if hit and str(hit.get("symbol") or "").upper() == name.upper() \
            and str(hit.get("type") or "").upper() == "EQUITY":
        return re.sub(r",?\s+(?:Inc|Corp|Corporation|Ltd|Limited|plc|PLC|N\.V|S\.A|AG|SE)\.?$",
                      "", str(hit.get("name") or "")).strip()
    return ""


def _recall(key: str) -> "Resolved":
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _CACHE_S:
        return hit[1]
    try:
        from .. import state
        row = (state.read(_REMEMBERED, "runtime") or {}).get(key) or {}
    except Exception:
        row = {}
    if row.get("url") and time.time() - float(row.get("at") or 0) < _REMEMBER_S:
        found = Resolved(url=row["url"], label=row.get("label", ""), how=row.get("how", ""))
        with _CACHE_LOCK:
            _CACHE[key] = (time.time(), found)
        return found
    return Resolved()


def _remember(key: str, found: "Resolved") -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), found)
    try:
        from .. import state
        table = state.read(_REMEMBERED, "runtime") or {}
        table[key] = {"url": found.url, "label": found.label, "how": found.how, "at": time.time()}
        if len(table) > 500:
            table = dict(sorted(table.items(), key=lambda kv: -float(kv[1].get("at") or 0))[:400])
        state.write(table, _REMEMBERED, "runtime")
    except Exception:
        pass


def _org_first(candidates: List[Dict[str, str]]) -> List[Dict[str, str]]:
    return sorted(candidates, key=lambda c: (0 if _ORG.search(c.get("description", "")) else 1,
                                             c.get("order", 99)))


# A door that failed — timed out, or answered 429 — is left alone for a few
# minutes rather than waited on by every visit. Measured: with the query
# service down, each lookup spent its first two seconds on it, and a ticker
# lookup then ran out of time before it could read the website it had found.
_DOWN: Dict[str, float] = {}
_DOWN_S = 300.0


def _up(door: str) -> bool:
    return time.monotonic() >= _DOWN.get(door, 0.0)


def _failed(door: str) -> None:
    _DOWN[door] = time.monotonic() + _DOWN_S


def _search(name: str, left) -> List[Dict[str, str]]:
    """Candidates from whichever door answers first with something.

    Both doors are asked at once — they are separate services, so this is
    one request to each rather than a burst to either — and the query
    service's answer is preferred, since it carries the addresses already.
    """
    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="v3wd")
    try:
        sparql = pool.submit(_candidates, name, min(_SPARQL_S, max(0.5, left()))) \
            if _up("sparql") else None
        api = pool.submit(_api_candidates, name, max(0.5, min(3.0, left()))) if _up("api") else None
        if sparql is not None:
            try:
                found = sparql.result(timeout=min(_SPARQL_S, max(0.3, left())) + 0.2)
                if found:
                    return found
            except Exception:
                _failed("sparql")
        if api is not None:
            try:
                return api.result(timeout=max(0.3, left()))
            except Exception:
                _failed("api")
        return []
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _resolve(name: str, timeout_s: float) -> Tuple[Resolved, str]:
    """``(site, company)`` — the company is the ticker's, when one was named,
    for the web-search fallback to look for instead of the bare ticker."""
    deadline = time.monotonic() + timeout_s

    def left() -> float:
        return deadline - time.monotonic()

    key = name.strip().lower()
    ticker = bool(_TICKER_SHAPED.fullmatch(name.strip()))
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="v3site")
    company = ""
    try:
        company_f = pool.submit(_ticker_company, name.strip(), 1.5) if ticker else None
        candidates = _search(name, left)
        chosen, how = None, ""
        if ticker and _up("api"):
            # One listing check at a time: concurrent claims requests were 429.
            for cand in _org_first(candidates)[:3]:
                if left() < 0.4:
                    break
                try:
                    if name.strip().upper() in _tickers(cand["id"], max(0.4, min(2.0, left()))):
                        chosen, how = cand, f"Wikidata, whose record lists it as {name.upper()}"
                        break
                except Exception:
                    _failed("api")
                    break
        if company_f is not None:
            try:
                company = company_f.result(timeout=max(0.2, left()))
            except Exception:
                company = ""
        if chosen is None and company and company.lower() != key:
            others = _org_first(_search(company, left))
            if others:
                chosen, how = others[0], f"Wikidata, via the ticker {name.upper()}"
        if chosen is None and not company:
            ranked = _org_first(candidates)
            chosen, how = (ranked[0], "Wikidata's official-website record") if ranked else (None, "")
        if chosen is None:
            return Resolved(), company
        url = chosen.get("url") or ""
        if not url and left() > 0.3 and _up("api"):
            try:
                url = _website(chosen["id"], max(0.4, min(2.0, left())))
            except Exception:
                _failed("api")
        if not url:
            return Resolved(), company
        return Resolved(url=url, label=chosen.get("label") or name, how=how), company
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def resolve(name: str, *, timeout_s: float = 3.0) -> Resolved:
    """The official homepage for a named site, from the registry only."""
    key = name.strip().lower()
    known = _recall(key)
    if known:
        return known
    found, _company = _resolve(name, timeout_s)
    if found:
        _remember(key, found)
    return found


def find(name: str, *, request_id: str, web_mode: str, timeout_s: float) -> Resolved:
    """The official homepage by any route: remembered, registry, then web search.

    The web search looks for the company when a ticker was named — "NVIDIA
    official website", not "nvda official website", which is as likely to
    find the screen reader.
    """
    key = name.strip().lower()
    known = _recall(key)
    if known:
        return known
    started = time.monotonic()
    found, company = _resolve(name, timeout_s)
    if not found:
        left = max(1.0, timeout_s - (time.monotonic() - started))
        found = resolve_by_search(company or name, request_id=request_id,
                                  web_mode=web_mode, timeout_s=left)
    if found:
        _remember(key, found)
    return found


def resolve_by_search(name: str, *, request_id: str, web_mode: str,
                       timeout_s: float) -> Resolved:
    """The official site from web search, when the registry has no answer.

    Accepted only when the result plainly belongs to the name — its address
    or its title carries the name's words — because the top hit for "X
    official website" is as often a review of X, or a directory listing it.
    """
    from . import gather as _gather

    try:
        results = _gather.search([f"{name} official website"], web_mode=web_mode,
                                 request_id=request_id, limit=6,
                                 deadline=time.monotonic() + timeout_s)
    except Exception:
        return Resolved()
    words = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if len(w) >= 3 and w not in _STOP]
    if not words:
        return Resolved()
    squashed = "".join(words)
    for source in results:
        host = registrable(host_of(source.url))
        title = (source.title or "").lower()
        in_title = sum(1 for w in words if w in title) >= max(1, (len(words) + 1) // 2)
        in_host = squashed in host.replace("-", "") or any(len(w) >= 4 and w in host for w in words)
        if in_host or in_title:
            parts = urlsplit(source.url)
            return Resolved(url=f"{parts.scheme}://{parts.netloc}/", label=name,
                            how="web search for its official website")
    return Resolved()
