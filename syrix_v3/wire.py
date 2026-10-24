"""The only module in v3 that knows the interface's JSON shapes.

Everything the HUD reads — the thinking ladder's events, the chat line's
fields, the envelope the voice path speaks from — is produced here, so a
change to the wire is a change to one file and the rest of v3 never has to
carry a field name it does not use itself.

Both protocols are existing contracts and are used verbatim rather than
reinvented:

``activity_feed.emit`` with its published ``kind`` constants drives the
thinking panel. The renderer maps each kind to an icon and a label, so the
names below are not decorative — a kind it does not recognise renders as a
generic step, and a stage that never emits renders as work that never
happened.

``ReplyEnvelope`` is what every surface downstream reads: the chat panel
takes its fields onto the chat line, the voice path speaks ``speak_text``,
and the diagnostics carry the mode back so the interface can label the turn.
"""
from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional

from syrix_core_v2 import activity_feed as feed
from syrix_core_v2.response_bus import ReplyEnvelope, ReplyStatus
import syrix_obs

# Re-exported so nothing else in v3 imports the kept modules directly.
KIND_INTENT = feed.KIND_INTENT
KIND_PLAN = feed.KIND_PLAN
KIND_MODE = feed.KIND_MODE
KIND_BRAIN_ROUTE = feed.KIND_BRAIN_ROUTE
KIND_BRAIN_FALLBACK = feed.KIND_BRAIN_FALLBACK
KIND_TOOL_CALL = feed.KIND_TOOL_CALL
KIND_SEARCH = feed.KIND_SEARCH
KIND_SOURCE = feed.KIND_SOURCE
KIND_MEMORY = feed.KIND_MEMORY
KIND_SYNTHESIS = feed.KIND_SYNTHESIS
KIND_DONE = feed.KIND_DONE
KIND_ERROR = feed.KIND_ERROR
KIND_INFO = feed.KIND_INFO
# Sub-agent progress. Missing from this list, every question split into
# parallel parts raised AttributeError on its first step, and the whole turn
# fell back to the previous runtime.
KIND_ZENITH = feed.KIND_ZENITH


def step(
    kind: str,
    label: str,
    *,
    request_id: str,
    detail: str = "",
    provider: str = "",
    **extra: Any,
) -> None:
    """One line on the thinking ladder. Never raises, never blocks a turn."""
    try:
        feed.emit(
            kind,
            label,
            detail=detail,
            request_id=request_id,
            provider=provider,
            extra=extra or None,
        )
    except Exception as exc:
        syrix_obs.report("v3.wire.step", exc, level="debug")


def stream(text: str, *, request_id: str) -> None:
    """Answer text as it is written, for the chat to show live.

    Uses the bridge's existing ``chat_token`` message, which the chat renders
    against the request id it is waiting on. Only reached when the host has
    already loaded the bridge: importing it here would start nothing useful
    in a test or the probe, and the answer still arrives whole at the end.
    """
    if not text or not request_id:
        return
    bridge = sys.modules.get("syrix_host_hud_bridge")
    if bridge is None:
        return
    try:
        bridge.push_chat_token(text, request_id)
    except Exception as exc:
        syrix_obs.report("v3.wire.stream", exc, level="debug")


def media(request_id: str, *, images: Optional[List[Dict[str, Any]]] = None,
          pending: int = 0, card: Optional[Dict[str, Any]] = None) -> None:
    """Pictures or a data card for the answer being written, ahead of the
    finished line.

    ``pending`` reserves room for that many tiles before any arrive; a later
    call with ``images`` fills them (an empty list releases the space).
    ``card`` is a live reading's card (a quote, the weather), shown above the
    answer as soon as the reading is in.
    """
    bridge = sys.modules.get("syrix_host_hud_bridge")
    if bridge is None or not request_id:
        return
    payload: Dict[str, Any] = {"type": "chat_media", "request_id": request_id}
    if images is not None:
        payload["images"] = list(images)[:8]
    if pending:
        payload["pending"] = int(pending)
    if card:
        payload["card"] = dict(card)
    try:
        bridge.ws_send(payload)
    except Exception as exc:
        syrix_obs.report("v3.wire.media", exc, level="debug")


def sources(found: List[Dict[str, Any]], *, request_id: str) -> None:
    """Publish read sources as clickable ladder entries."""
    try:
        feed.emit_sources(found, request_id=request_id)
    except Exception as exc:
        syrix_obs.report("v3.wire.sources", exc, level="debug")


def flags(**values: Any) -> None:
    """Presentation flags the interface reads from ``hud_state.hud_flags``."""
    try:
        feed.publish_hud_flags(dict(values))
    except Exception as exc:
        syrix_obs.report("v3.wire.flags", exc, level="debug")


def _clean_sources(found: Optional[List[Dict[str, Any]]]) -> List[Dict[str, str]]:
    """Reduce gathered evidence to the fields the chat line renders.

    A source without a URL cannot be clicked and cannot be checked, so it is
    not a citation and is dropped rather than shown as one.
    """
    out: List[Dict[str, str]] = []
    seen = set()
    for item in found or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        row = {
            "url": url,
            "name": str(item.get("title") or item.get("name") or url)[:160],
            "provider": str(item.get("provider") or item.get("domain") or "")[:48],
            "domain": str(item.get("domain") or "")[:64],
            "published": str(item.get("published") or "")[:64],
            "retrieved_at": str(item.get("retrieved_at") or "")[:64],
        }
        # The number the answer cites this source by, when it has one.
        if isinstance(item.get("n"), int):
            row["n"] = item["n"]
        out.append(row)
    return out


def answer(
    text: str,
    *,
    request_id: str,
    mode: str,
    provider: str = "",
    model: str = "",
    route: str = "v3",
    intent: str = "general",
    speak_text: Optional[str] = None,
    found: Optional[List[Dict[str, Any]]] = None,
    follow_ups: Optional[List[str]] = None,
    panel: Optional[Dict[str, Any]] = None,
    diagnostics: Optional[Dict[str, Any]] = None,
) -> ReplyEnvelope:
    """The envelope for a turn that produced an answer."""
    diag: Dict[str, Any] = {"syrix_model": mode, "engine": "v3"}
    diag.update(diagnostics or {})
    body = str(text or "")
    return ReplyEnvelope(
        status=ReplyStatus.SUCCESS,
        intent=intent,
        route=route,
        provider=provider,
        chat_text=body,
        speak_text=body if speak_text is None else str(speak_text),
        sources=_clean_sources(found),
        diagnostics=diag,
        request_id=request_id,
        follow_ups=[str(f) for f in (follow_ups or [])][:3],
        panel=panel,
    )


def failure(
    text: str,
    *,
    request_id: str,
    mode: str,
    reason: str,
    intent: str = "general",
) -> ReplyEnvelope:
    """The envelope for a turn that could not answer.

    Reserved for the case where there is genuinely nothing to say — no lane,
    no evidence, nothing gathered. A turn that ran out of time still answers
    from what it has and goes through :func:`answer`; shipping an apology in
    place of a partial answer is the behaviour being replaced.
    """
    return ReplyEnvelope(
        status=ReplyStatus.FAILED,
        intent=intent,
        route="v3",
        chat_text=str(text or ""),
        speak_text=str(text or ""),
        diagnostics={"syrix_model": mode, "engine": "v3", "failure_reason": reason},
        request_id=request_id,
        error_safe=reason,
    )
