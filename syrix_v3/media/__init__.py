"""What is shown with an answer beyond its text: images, products, next questions.

Two phases so none of it lengthens the wait for the answer itself:

``begin`` runs as soon as the evidence is in — before the model is asked —
and works out which images belong to this answer and fetches them, in
parallel with the model writing. ``finish`` runs once the answer exists:
product cards come from the products it recommends and follow-up questions
from what it covered, both concurrently, and then everything is folded into
the card the chat line already carries.

Every image reaches the interface as a local file fetched under the web
gateway's URL policy (see ``cache``). Nothing here can fail a turn: each
piece that does not arrive in time is simply left out.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .. import state, wire
from . import cache, followups, images, products
import syrix_obs

_POOL = cf.ThreadPoolExecutor(max_workers=4, thread_name_prefix="v3-media")
_IMAGE_BUDGET_S = 4.0
_FINISH_BUDGET_S = 4.5

_LOG_FILE = "media_log.jsonl"
_LOG_MAX_BYTES = 1_000_000
_log_lock = threading.Lock()


def _log(entry: Dict[str, Any]) -> None:
    """One line per turn that wanted pictures: how many it found and kept.

    An image job that comes back empty shows nothing in the chat by design,
    which also makes it indistinguishable from one that was never asked for.
    """
    try:
        path = state.path_for(_LOG_FILE, "runtime")
        with _log_lock:
            if os.path.exists(path) and os.path.getsize(path) > _LOG_MAX_BYTES:
                os.replace(path, path + ".1")
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        syrix_obs.report("v3.media._log", exc, level="debug")


@dataclass
class Media:
    images: List[Dict[str, str]] = field(default_factory=list)
    products: List[Dict[str, str]] = field(default_factory=list)
    follow_ups: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.images or self.products)


@dataclass
class Pending:
    question: str
    images: Optional["cf.Future"] = None
    follow_ups: Optional["cf.Future"] = None
    shopping: bool = False
    voice: bool = False
    # Conversation rather than a question: no pictures, no suggested
    # follow-ups, and no model call spent preparing either.
    quiet: bool = False
    started: float = field(default_factory=time.monotonic)


def _images_job(question: str, evidence: Any, want: int, request_id: str) -> List[Dict[str, str]]:
    began = time.monotonic()
    entry: Dict[str, Any] = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "rid": request_id,
                             "want": want, "question": question[:120]}
    picked: List[Dict[str, str]] = []
    try:
        entry["from_evidence"] = len(images.from_evidence(evidence))
        candidates = images.plan(question, evidence, want=want)
        entry["candidates"] = len(candidates)
        if candidates:
            localized = cache.localize_many([c["url"] for c in candidates[:want * 3]],
                                            want=want, deadline_s=_IMAGE_BUDGET_S)
            entry["localized"] = len(localized)
            picked = images.pick(candidates, localized, want)
    except Exception as exc:
        entry["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    entry["picked"] = len(picked)
    entry["ms"] = round((time.monotonic() - began) * 1000)
    _log(entry)
    # Sent the moment they are ready, so they settle above the answer while
    # it is still being written instead of arriving with the finished line
    # and pushing the text down. Sent when empty too: the chat is holding
    # space for them and needs to be told to let it go.
    wire.media(request_id, images=picked)
    return picked


def begin(question: str, evidence: Any, *, has_card: bool, has_attachment: bool,
          voice: bool = False, request_id: str = "") -> Pending:
    """Start the image work for this answer. Never raises, never blocks."""
    pending = Pending(question=question, voice=voice)
    try:
        pending.shopping = products.wanted(question) and not has_attachment
        want = 0 if pending.shopping else images.wanted(
            question, has_card=has_card, has_attachment=has_attachment)
        if want:
            # The chat reserves the space now, before any text, so nothing
            # below it moves when the pictures land.
            wire.media(request_id, pending=min(want, 4) if want <= 4 else 5)
            pending.images = _POOL.submit(_images_job, question, evidence, want, request_id)
    except Exception:
        pending.images = None
    try:
        if not voice and followups.wanted_early(question):
            pending.follow_ups = _POOL.submit(followups.suggest, question, "",
                                              found=_found(evidence))
    except Exception:
        pending.follow_ups = None
    return pending


def _found(evidence: Any) -> str:
    """What the search turned up, briefly: titles and the start of each snippet."""
    rows = []
    for source in (getattr(evidence, "sources", None) or [])[:8]:
        title = str(getattr(source, "title", "") or "").strip()
        snippet = " ".join(str(getattr(source, "snippet", "") or "").split())[:140]
        if title:
            rows.append(f"- {title}" + (f": {snippet}" if snippet else ""))
    return "\n".join(rows)


def _products_job(answer: str) -> List[Dict[str, str]]:
    cards = products.lookup(products.names_from(answer), deadline_s=3.2)
    urls = [c["image_url"] for c in cards if c.get("image_url")]
    localized = cache.localize_many(urls, want=len(urls), deadline_s=1.5) if urls else {}
    for card in cards:
        card["image"] = localized.get(card.pop("image_url", ""), "")
    return cards


def finish(pending: Pending, answer: str) -> Media:
    """Everything that depends on the finished answer, then collect it all."""
    media = Media()
    if pending is None or pending.quiet:
        return media
    ends = time.monotonic() + _FINISH_BUDGET_S
    shop = _POOL.submit(_products_job, answer) if pending.shopping else None
    if pending.follow_ups is not None:
        # Started with the images; a short reply (a greeting, a one-liner)
        # does not get chips even when they were prepared.
        nexts = pending.follow_ups if len(str(answer or "")) >= 120 else None
    else:
        nexts = (_POOL.submit(followups.suggest, pending.question, answer)
                 if not pending.voice and followups.wanted(pending.question, answer) else None)
    for name, future in (("images", pending.images), ("products", shop), ("follow_ups", nexts)):
        if future is None:
            continue
        try:
            setattr(media, name, future.result(timeout=max(0.05, ends - time.monotonic())) or [])
        except Exception as exc:
            syrix_obs.report("v3.media.finish", exc, level="debug")
    try:
        cache.prune()
    except Exception as exc:
        syrix_obs.report("v3.media.finish", exc, level="debug")
    return media


def attach(card: Optional[Dict[str, Any]], media: Media) -> Optional[Dict[str, Any]]:
    """The panel for the chat line: the card, with the media folded in."""
    if not media:
        return card
    block = {}
    if media.images:
        block["images"] = media.images[:8]
    if media.products:
        block["products"] = media.products[:6]
    if card:
        merged = dict(card)
        merged["media"] = block
        return merged
    return {"type": "media", "media": block}


__all__ = ["Media", "Pending", "attach", "begin", "finish"]
