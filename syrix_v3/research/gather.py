"""Search everything at once, open the best of it, stop when time is up.

This is what makes a five-second turn able to stand on twenty sources. Not
one search followed by one read followed by an answer — three round trips
inside five seconds is not possible — but every query fired together, every
page opened together, and a hard line at the deadline with whatever arrived.

A slow host is abandoned, never waited on. The alternative is letting one
unresponsive server decide how long the turn takes, which is how the runtime
being replaced produced ninety-second answers.

All network access goes through ``syrix_web.gateway``: URL and SSRF policy,
the escalation ladder from a cheap fetch to a rendered page, and
prompt-injection scanning on what comes back. This module decides what to
ask for and how long to wait, and opens nothing itself.
"""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .. import config, state
from . import health as _health
from . import lanes as _lanes
from . import rank as _rank
from .schemas import Evidence, Source
import syrix_obs

_SNIPPET_CHARS = 320
_BODY_CHARS = 5000
# High enough that every lane on every query goes out at once. These threads
# spend their whole life waiting on a socket, so the count is bounded by
# politeness to the providers rather than by this machine.
_SEARCH_WORKERS = 16
# Raised from 6 alongside the per-question read ceiling. Six workers meant a
# breadth question asking for twelve pages needed two waves inside a read
# window of under four seconds, so the second wave was never reached and the
# extra pages were selected and then reported as unopened. Concurrency is
# bounded by the gathering deadline either way; this only stops the ceiling
# being decided by the worker count rather than by the time available.
_READ_WORKERS = 10


_LOG_FILE = "search_log.jsonl"
_LOG_MAX_BYTES = 2_000_000
_log_lock = threading.Lock()


def _log(entry: Dict[str, Any]) -> None:
    """One line per lane per query: what it returned, how fast, or why not.

    A lane that fails contributes nothing to the turn, which is right for the
    answer and wrong for diagnosis: without this, a search that quietly lost
    three of its four lanes looks exactly like a search with little to find.
    """
    try:
        path = state.path_for(_LOG_FILE, "runtime")
        with _log_lock:
            if os.path.exists(path) and os.path.getsize(path) > _LOG_MAX_BYTES:
                os.replace(path, path + ".1")
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        syrix_obs.report("v3.research.gather._log", exc, level="debug")


def _where(exc: BaseException) -> str:
    """The innermost frame that raised, as file:line — enough to find it."""
    try:
        import traceback
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        return f"{os.path.basename(frame.filename)}:{frame.lineno} {frame.name}"
    except Exception:
        return ""


def _race(jobs, workers: int, deadline: float, name: str):
    """Run jobs together and yield what finishes before ``deadline``.

    Deliberately not a ``with ThreadPoolExecutor(...)`` block. Leaving that
    block calls shutdown(wait=True), so a batch that "timed out" still
    blocked until its slowest member returned — the deadline logged
    correctly and then the turn waited anyway. Measured once as a three
    second read budget taking twelve and a half seconds.

    Abandoning means abandoning: outstanding work is cancelled where it has
    not started and left to finish unobserved where it has.
    """
    pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix=name)
    try:
        futures = [pool.submit(job) for job in jobs]
        left = deadline - time.monotonic()
        if left <= 0:
            return
        try:
            for future in as_completed(futures, timeout=left):
                yield future.result()
        except Exception:
            # A timeout here is the expected end of a batch, not a failure.
            return
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _race_keyed(jobs, workers: int, soft: float, hard: float, keep_waiting, name: str):
    """``_race`` with a second, later line for the jobs worth waiting for.

    Everything that finishes before ``soft`` is yielded. Past it the batch
    stays open until ``hard`` only while ``keep_waiting()`` says the result
    still missing is worth the time; the first time it says otherwise, the
    rest is abandoned exactly as ``_race`` abandons it.
    """
    from concurrent.futures import FIRST_COMPLETED, wait
    pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix=name)
    try:
        pending = {pool.submit(job) for job in jobs}
        while pending:
            now = time.monotonic()
            if now >= hard or (now >= soft and not keep_waiting()):
                return
            line = soft if now < soft else hard
            done, pending = wait(pending, timeout=max(0.02, line - now),
                                 return_when=FIRST_COMPLETED)
            for future in done:
                try:
                    yield future.result()
                except Exception:
                    continue
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _gateway():
    from syrix_web import gateway  # noqa: WPS433 — kept plumbing, imported late
    return gateway


def search(
    queries: List[str],
    *,
    web_mode: str,
    request_id: str,
    limit: int,
    deadline: float,
    recency_days: int = 0,
    images_out: Optional[List[Dict[str, Any]]] = None,
    keyed_until: float = 0.0,
) -> List[Source]:
    """Race every lane against every query and merge what arrives in time.

    Every (query, lane) pair is one job and they all go out together, so the
    pass costs the slowest lane that answers rather than the sum. Anything
    still running at the deadline is abandoned.

    A page found by two independent lanes is not two sources; it is one that
    two indexes agreed on, and corroboration is worth more than either
    finding alone — so a duplicate raises the score rather than adding a row.

    ``keyed_until``, when later than ``deadline``, keeps the search open past
    it for Exa and Tavily on the main query, and only while neither has
    answered. They carry page text and pictures and vary from half a second
    to six: measured in the app, both timed out at 2.3s on a travel question,
    which was then answered from result snippets with no page text and no
    images to show.
    """
    if not queries:
        return []

    lane_names = _lanes.choose(queries[0])
    # The deep mode can afford the keyed lanes on every angle; a fast turn
    # spends them on the main question only.
    variant_lanes = lane_names if web_mode == "zenith" else list(_lanes.VARIANT)
    per_lane_timeout = max(1.2, (deadline - time.monotonic()) - 0.3)
    hard = max(deadline, keyed_until)
    keyed = [n for n in lane_names if n in _lanes.KEYED]
    keyed_state = {"answered": False, "left": len(keyed)}

    def _job(query: str, lane: str, main: bool = False):
        # The main query's keyed lanes may run to the later line.
        ends = hard if (main and lane in keyed) else deadline

        def _run():
            left = ends - time.monotonic()
            if left <= 0.3:
                return lane, [], main
            began = time.monotonic()
            try:
                found = _lanes.ALL[lane](
                    query, timeout=left - 0.1 if ends > deadline else min(left, per_lane_timeout))
                _log({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "rid": request_id,
                      "lane": lane, "query": query[:120], "n": len(found or []),
                      "ms": round((time.monotonic() - began) * 1000)})
                return lane, found, main
            except Exception as exc:
                # A lane that fails contributes nothing and is not an error
                # for the turn — the others are why there is more than one.
                # A failure that says the lane is out (allowance spent, key
                # refused) takes it out of later races until it recovers.
                _log({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "rid": request_id,
                      "lane": lane, "query": query[:120], "n": 0,
                      "ms": round((time.monotonic() - began) * 1000),
                      "error": f"{type(exc).__name__}: {str(exc)[:160]}",
                      "where": _where(exc), "out": _health.note_failure(lane, exc)})
                return lane, [], main
        return _run

    jobs = [_job(queries[0], lane, main=True) for lane in lane_names]
    jobs += [_job(q, lane) for q in queries[1:] for lane in variant_lanes]
    merged: Dict[str, Source] = {}
    corroborated: Dict[str, set] = {}
    headlines: List[Source] = []

    def _wait_for_keyed() -> bool:
        return bool(keyed) and not keyed_state["answered"] and keyed_state["left"] > 0

    adult_ok = _lanes.explicit(" ".join(queries))
    for lane, found, main in _race_keyed(jobs, _SEARCH_WORKERS, deadline, hard,
                                         _wait_for_keyed, "v3search"):
        if not adult_ok:
            found = [s for s in (found or [])
                     if not _lanes.explicit(f"{s.url} {s.title}")]
        if main and lane in keyed:
            keyed_state["left"] -= 1
            keyed_state["answered"] = keyed_state["answered"] or bool(found)
        for source in found or []:
            if source.provider == _lanes.IMAGE_PROVIDER:
                if images_out is not None and len(images_out) < 12:
                    images_out.append({"url": source.url, "title": source.title,
                                       "page": "", "domain": source.domain})
                continue
            if not source.url:
                # A discovery headline has no citable link; it still tells the
                # model what happened and who reported it.
                headlines.append(source)
                continue
            existing = merged.get(source.url)
            if existing is None:
                merged[source.url] = source
                corroborated[source.url] = {source.provider}
            else:
                corroborated.setdefault(source.url, {existing.provider}).add(source.provider)
                if len(source.snippet) > len(existing.snippet):
                    existing.snippet = source.snippet
                if not existing.published and source.published:
                    existing.published = source.published
                if not existing.body and source.body:
                    existing.body = source.body
                    existing.retrieved_at = source.retrieved_at
                if not existing.image and source.image:
                    existing.image = source.image
                existing.score = max(existing.score, source.score)

    ranked = _rank.order(list(merged.values()) + headlines,
                         query=queries[0], recency_days=recency_days)
    # Independent indexes agreeing is a signal about the source, so it is
    # applied after relevance rather than being mixed into the lane prior.
    for source in ranked:
        agreeing = len(corroborated.get(source.url) or ())
        if agreeing > 1:
            source.score = round(source.score + 0.05 * (agreeing - 1), 4)
    ranked.sort(key=lambda s: -s.score)
    return ranked[:limit]


def read(
    sources: List[Source],
    *,
    web_mode: str,
    request_id: str,
    deadline: float,
) -> List[str]:
    """Open pages concurrently, filling in ``body``. Returns what refused."""
    if not sources:
        return []
    gateway = _gateway()
    cfg = config.settings()
    problems: List[str] = []

    # Pages the gathering window expired before reaching. This was silent: a
    # source that was never attempted recorded neither a body nor a problem,
    # so a turn that opened two of the six it selected looked identical to a
    # turn where only two pages existed. On a ranked-list question that is the
    # difference between "here are four of the ten, and why the rest are
    # missing" and an answer that quietly invents the remainder.
    unreached: List[str] = []

    def _one(source: Source) -> None:
        left = deadline - time.monotonic()
        if left <= 0.3:
            unreached.append(source.domain or source.url)
            return
        page = gateway.read(
            source.url, mode=web_mode, config=cfg,
            request_id=request_id, timeout_s=left,
        )
        if getattr(page, "suspicious", False):
            # The page carried text aimed at the model rather than the reader.
            # It is evidence of nothing and is not passed on.
            problems.append(f"{source.domain or source.url}: ignored, the page "
                            "contained instructions aimed at the assistant")
            return
        if not getattr(page, "ok", False):
            problems.append(f"{source.domain or source.url}: "
                            f"{getattr(page, 'reason', '') or 'would not open'}")
            return
        body = str(getattr(page, "text", "") or "").strip()
        if body:
            source.body = body[:_BODY_CHARS]
            source.retrieved_at = time.strftime("%Y-%m-%dT%H:%M:%S")
            if not source.title:
                source.title = str(getattr(page, "title", "") or source.url)

    jobs = [(lambda s=s: _one(s)) for s in sources]
    for _ in _race(jobs, min(len(sources), _READ_WORKERS), deadline, "v3read"):
        pass

    if unreached:
        problems.append(
            f"{len(unreached)} of {len(sources)} selected pages were not opened "
            f"before the gathering window closed "
            f"({', '.join(unreached[:4])}{', …' if len(unreached) > 4 else ''})")

    return problems


def gather(
    queries: List[str],
    *,
    web_mode: str,
    request_id: str,
    max_sources: int,
    max_open: int,
    deadline_s: float,
    recency_days: int = 0,
    on_step: Optional[Any] = None,
) -> Evidence:
    """One full pass: search wide, open the best, inside one deadline."""
    started = time.monotonic()
    deadline = started + max(0.5, deadline_s)
    evidence = Evidence(queries=list(queries))

    if on_step:
        on_step("search", "Searching the web", "; ".join(queries)[:160])

    # Reading gets the larger share, and that split is measured rather than
    # guessed: the lanes return twenty sources in about a second and a half,
    # while opening those sources is what turns a link into an answer. A
    # source found and not opened contributes a title, and a title is not
    # information — asked what was happening with Bitcoin, the behaviour this
    # replaces answered "Kevin O'Leary Names the One Thing Standing Between
    # Bitcoin and $1 Million", which does not say what the one thing is.
    # The search phase waits for the strong lanes rather than a fixed share.
    # At 40% of a four-second window it closed at 1.6s, before Tavily (2.2s)
    # or Exa (0.8-2.5s) could answer, and the only lane that reliably made it
    # was Wikipedia: "best laptops for students under 800 pounds" was answered
    # from pages about ThinkPads in general and a rapper. Exa returns page
    # text with its results, so time spent waiting for it is time saved on
    # reading.
    search_share = min(deadline_s * 0.65, 3.2 if web_mode != "zenith" else 8.0)
    search_deadline = min(deadline, started + max(0.4, search_share))
    # Exa's results arrive already read, so waiting for it costs reading time
    # only when it then fails; the later line still leaves a moment to open
    # pages if it does.
    evidence.sources = search(
        queries, web_mode=web_mode, request_id=request_id,
        limit=max_sources, deadline=search_deadline, recency_days=recency_days,
        images_out=evidence.images,
        keyed_until=max(search_deadline, min(deadline - 0.6, search_deadline + 2.0)),
    )
    evidence.searched_ms = round((time.monotonic() - started) * 1000.0, 1)

    if not evidence.sources:
        evidence.problems.append("no search results came back")
        return evidence

    if on_step:
        on_step("filter_rank", f"{len(evidence.sources)} sources found",
                f"opening the top {min(max_open, len(evidence.sources))}")

    read_started = time.monotonic()
    # Pages that arrived already read count towards the breadth asked for.
    already = sum(1 for s in evidence.sources[:max(max_open * 2, 8)] if s.opened)
    to_open = _rank.pick_to_open([s for s in evidence.sources if not s.opened],
                                 max(0, max_open - already))
    if time.monotonic() < deadline - 0.3:
        evidence.problems.extend(read(
            to_open, web_mode=web_mode, request_id=request_id, deadline=deadline,
        ))
    evidence.read_ms = round((time.monotonic() - read_started) * 1000.0, 1)
    evidence.truncated = time.monotonic() >= deadline

    if on_step:
        opened = len(evidence.opened)
        on_step("pack_built", f"Read {opened} of {len(evidence.sources)} sources",
                f"{evidence.searched_ms:.0f}ms searching, {evidence.read_ms:.0f}ms reading")
    return evidence
