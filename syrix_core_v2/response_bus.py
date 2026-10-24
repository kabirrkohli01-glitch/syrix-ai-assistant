"""Structured response envelope for the SYRIX Core V2 runtime.

Every adapter and the spine itself return a :class:`ReplyEnvelope`. This is
the single canonical shape the HUD wiring, voice layer, and diagnostics all
consume — replacing the old "string or None" return contract that hid failures
behind empty strings.

The status taxonomy is intentionally small. ``needs_legacy_fallback`` is the
only value the spine treats as "let the old syrix_host.py path try" — every
other status is taken at face value and reported honestly to the user.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


class ReplyStatus:
    """Closed enumeration of envelope statuses."""

    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    NEEDS_SETUP = "needs_setup"
    BLOCKED = "blocked"
    NEEDS_LEGACY_FALLBACK = "needs_legacy_fallback"
    CLARIFY = "clarify"

    ALL = (
        SUCCESS,
        FAILED,
        TIMEOUT,
        UNAVAILABLE,
        NEEDS_SETUP,
        BLOCKED,
        NEEDS_LEGACY_FALLBACK,
        CLARIFY,
    )


@dataclass
class ReplyEnvelope:
    """The single value every spine path produces.

    ``chat_text`` is what the HUD chat panel shows. ``speak_text`` is what TTS
    reads aloud; it may be shorter or differently phrased than chat_text.
    Adapters that should not speak set ``speak_text=""``.
    """

    status: str = ReplyStatus.SUCCESS
    intent: str = "unknown"
    route: str = "brain"
    provider: str = ""
    chat_text: str = ""
    speak_text: str = ""
    hud_action: Optional[Dict[str, Any]] = None
    error_safe: Optional[str] = None
    sources: List[Dict[str, str]] = field(default_factory=list)
    freshness_seconds: Optional[float] = None
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    request_id: str = ""
    # CAP-002/003 (2026-06-04): confidence score + proactive follow-up
    # suggestions. ``confidence`` is 0.0-1.0 when populated; ``follow_ups`` is
    # a list of natural-language suggestions the HUD can render as chips
    # ("ask about options flow", "compare to AMD"). Both default to None / []
    # so existing callers stay unchanged.
    confidence: Optional[float] = None
    follow_ups: List[str] = field(default_factory=list)
    # Smart panel: an optional structured rich-card payload the HUD chat renders
    # inline under the answer (live sports score, stock quote, weather, or a
    # generic answer card). Populated only when SYRIX judges a card adds clarity
    # — see syrix_smart_panels.build_panel. None means "plain text answer".
    panel: Optional[Dict[str, Any]] = None

    def is_terminal(self) -> bool:
        """True if the spine has fully resolved the request (no fallback needed)."""
        return self.status != ReplyStatus.NEEDS_LEGACY_FALLBACK

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def fallback(cls, intent: str = "unknown", reason: str = "spine_unsure") -> "ReplyEnvelope":
        return cls(
            status=ReplyStatus.NEEDS_LEGACY_FALLBACK,
            intent=intent,
            route="legacy",
            chat_text="",
            speak_text="",
            diagnostics={"fallback_reason": reason},
        )

    @classmethod
    def failed(
        cls,
        intent: str,
        route: str,
        chat_text: str,
        *,
        error_safe: str = "",
        provider: str = "",
    ) -> "ReplyEnvelope":
        return cls(
            status=ReplyStatus.FAILED,
            intent=intent,
            route=route,
            provider=provider,
            chat_text=chat_text,
            speak_text=chat_text,
            error_safe=error_safe or None,
        )

    @classmethod
    def clarify(cls, intent: str, question: str) -> "ReplyEnvelope":
        return cls(
            status=ReplyStatus.CLARIFY,
            intent=intent,
            route="clarify",
            chat_text=question,
            speak_text=question,
        )


__all__ = ["ReplyEnvelope", "ReplyStatus"]
