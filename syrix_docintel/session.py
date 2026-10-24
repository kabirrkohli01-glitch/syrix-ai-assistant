"""Follow-up document context — "keep talking about the file I attached".

The composer clears the attachment tray after each send, so ``doc_ids`` ride
only on the turn they were attached to. Before this module every follow-up
("what does section 2 mean?", "is the total correct?") silently lost the
document and fell through to the ungrounded brain. Deterministic, no LLM:

  ``note_used(doc_ids)``      — record the docs a grounded turn actually used
  ``resolve_followup(text)``  — doc_ids to ground a NEW text-only turn on, or []

``resolve_followup`` fires only when the turn plainly refers to attached
material (an explicit document word, a page/slide/sheet/cell locator, or one
of the attached filenames), or — inside a short window right after a grounded
turn — reads as a content follow-up ("explain that", "tell me more", "where
does it say that") without an obviously unrelated ask (weather / stocks /
open an app / timers). A miss returns [] and the turn routes exactly as
before, which is the safe default.

Config:
  docintel_followup_enabled          (default True)  — kill switch
  docintel_followup_ttl_s            (default 1800)  — docs stay addressable
  docintel_followup_continuation_s   (default 240)   — bare-continuation window
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Dict, List

_LOCK = threading.RLock()
_STATE: Dict[str, Any] = {"doc_ids": [], "names": [], "ts": 0.0}

# Explicit reference to attached material — safe at any point inside the TTL.
_DOC_WORD_RE = re.compile(
    r"\b(?:pdf|docx?|files?|documents?|attachments?|uploads?|uploaded|"
    r"spreadsheets?|workbook|worksheet|csv|slides?|deck|presentation|pptx?|"
    r"screenshots?|scans?|images?|photos?|pictures?|"
    r"pages?\s+\d+|p\.\s*\d+|slide\s+\d+|sheet\s*\d*|cell\s+[a-z]{1,3}\d+|"
    r"section\s+\d+|that\s+(?:table|chart|graph|figure|diagram)|"
    r"the\s+(?:report|invoice|contract|resume|cv|statement|budget|table|chart|graph|figure))\b",
    re.IGNORECASE)

# Content follow-up shapes for the short post-grounding window.
_CONTINUATION_RE = re.compile(
    r"(?:\b(?:explain|summariz|summaris|clarify|simplify|simpler|elaborate|expand|"
    r"rewrite|improve|shorten|translate|quote|extract|compare|list|find|"
    r"where\s+(?:does|is|did)|what\s+(?:does|do|is|are|about)|why\s+(?:does|is|are)|"
    r"how\s+(?:does|do|is|much|many)|is\s+(?:there|that|it|this)|does\s+(?:it|this|that)|"
    r"tell\s+me\s+more|more\s+detail|go\s+on|continue)\b)",
    re.IGNORECASE)

# Vetoes for the continuation window: asks that are clearly about something
# else even if phrased as a follow-up ("what about the weather?").
_OFF_TOPIC_RE = re.compile(
    r"\b(?:weather|forecast|temperature|stocks?|share\s+price|market|news|headlines?|"
    r"time\s+is\s+it|timer|alarm|remind(?:er)?|volume|brightness|"
    r"open\s+\w+|launch\s+\w+|play\s+\w+|email|inbox|mail|calendar|meeting)\b",
    re.IGNORECASE)


def _cfg(key: str, default: Any) -> Any:
    try:
        import core_boot  # type: ignore
        return getattr(core_boot, "CONFIG", {}).get(key, default)
    except Exception:
        return default


def enabled() -> bool:
    return bool(_cfg("docintel_followup_enabled", True))


def note_used(doc_ids: List[str], names: List[str] | None = None) -> None:
    """Record the docs the current grounded turn used (attach or follow-up)."""
    ids = [str(x) for x in (doc_ids or []) if str(x).strip()]
    if not ids:
        return
    resolved_names = [str(n) for n in (names or []) if str(n).strip()]
    if not resolved_names:
        try:
            from . import registry
            resolved_names = [d.name for d in registry.get_many(ids)]
        except Exception:
            resolved_names = []
    with _LOCK:
        _STATE["doc_ids"] = ids
        _STATE["names"] = resolved_names
        _STATE["ts"] = time.time()


def _name_mentioned(text_lower: str, names: List[str]) -> bool:
    for name in names:
        stem = str(name).rsplit(".", 1)[0].strip().lower()
        if len(stem) >= 4 and stem in text_lower:
            return True
    return False


def resolve_followup(text: str) -> List[str]:
    """doc_ids to ground this text-only turn on, or [] to route as before."""
    if not enabled():
        return []
    t = str(text or "").strip()
    if not t:
        return []
    with _LOCK:
        ids = list(_STATE["doc_ids"])
        names = list(_STATE["names"])
        ts = float(_STATE["ts"])
    if not ids or not ts:
        return []
    now = time.time()
    try:
        ttl = float(_cfg("docintel_followup_ttl_s", 1800))
    except Exception:
        ttl = 1800.0
    if (now - ts) > max(60.0, ttl):
        return []
    t_lower = t.lower()
    explicit = bool(_DOC_WORD_RE.search(t)) or _name_mentioned(t_lower, names)
    if not explicit:
        try:
            cont_window = float(_cfg("docintel_followup_continuation_s", 240))
        except Exception:
            cont_window = 240.0
        if (now - ts) > max(30.0, cont_window):
            return []
        if _OFF_TOPIC_RE.search(t) or not _CONTINUATION_RE.search(t):
            return []
    # Only ground on docs that are still resolvable.
    try:
        from . import registry
        live = [d.id for d in registry.get_many(ids)]
    except Exception:
        live = ids
    return live


def _reset_for_tests() -> None:
    with _LOCK:
        _STATE["doc_ids"] = []
        _STATE["names"] = []
        _STATE["ts"] = 0.0
