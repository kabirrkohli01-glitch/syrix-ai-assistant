"""The card that sits under an answer.

A price with a chart, a range and a volume is worth more than the same
number written out, and the interface already renders one beautifully —
``syrix_smart_panels`` builds it and ``SyrixAdaptiveCard`` draws it. v3
would have lost it silently: the envelope has a ``panel`` field and nothing
in the new engine filled it.

The card is decoration on top of an answer, never a substitute for one.
The behaviour this replaces got that backwards — it drew a perfect NVDA
chart and captioned it "In the news: Would you buy branded clothing from
your favourite tech firm?", which is the title of an unrelated page. The
card carried the substance and the sentence carried nothing.

So: the card is built from the same live data the answer was written
against, it is attached only when it adds something the prose cannot, and
it is never allowed to contribute text.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# Intents the card builder understands. Passed through so its own
# judgement order — weather, sport, stock, generic — still applies.
_PRICE = re.compile(
    r"\b(price|trading|worth|quote|stock|share|shares|ticker|"
    r"market cap|chart|\$[A-Z]{1,5}\b)", re.I)
_WEATHER = re.compile(r"\b(weather|forecast|temperature|rain|snow|wind)\b", re.I)
_SPORT = re.compile(r"\b(score|fixture|result|match|game|league|kick[\s-]?off)\b", re.I)


def _intent_for(question: str) -> str:
    if _WEATHER.search(question):
        return "weather"
    if _SPORT.search(question):
        return "sports"
    if _PRICE.search(question):
        return "stock"
    return "general"


def build(
    question: str,
    answer: str,
    *,
    sources: Optional[List[Dict[str, Any]]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """A card for this answer, or None for a plain reply. Never raises.

    Weather and quote cards are built by ``live`` from the same reading the
    answer was written against. This builder fetches its own data, so letting
    it draw those too put a third temperature under an answer that already
    quoted two. It is kept for scores, which ``live`` does not read.
    """
    if _intent_for(question) != "sports":
        return None
    try:
        import syrix_smart_panels  # noqa: WPS433 — kept plumbing, imported late
    except Exception:
        return None

    try:
        panel = syrix_smart_panels.build_panel(
            _intent_for(question),
            question,
            chat_text=answer,
            sources=sources or [],
            cfg=config,
        )
    except Exception:
        return None

    if not isinstance(panel, dict):
        return None

    # A generic "answer" card restates the reply above it in smaller type.
    # The prose is the answer; a card that only repeats it is noise.
    if str(panel.get("type") or "") == "answer":
        return None
    return panel
