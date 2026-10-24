"""Per-mode budgets — the contract that keeps Spark fast.

The gateway can do a lot; that is the danger. A ladder that always climbs to
the top would turn every question into an agent workflow, and Spark's whole
value is that it answers in seconds. So each mode gets an explicit allowance
and the ladder stops when it runs out, not when it runs out of ideas.

The numbers come from measurement, not taste. A direct fetch is ~0.3–1.5 s. A
browser navigate + snapshot measured 0.8 s (Wikipedia) to 1.5 s (BBC News) with
the sidecar already warm, plus ~0.3 s for a cold start. Spark's allowance is
sized so the ordinary path finishes well inside five seconds and the browser
rung — which only runs when the alternative is admitting defeat — still lands
under ten.

``browser_pages`` of 0 does not mean "the browser is disabled". It means this
mode may not climb that far, and the caller will get an honest empty read
instead of a slow one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

MODE_SPARK = "spark"
MODE_ZENITH = "zenith"
MODE_SUPERCOMPUTER = "supercomputer"
MODE_BACKGROUND = "background"


@dataclass(frozen=True)
class Budget:
    """One mode's allowance. All times in seconds."""

    mode: str
    search_s: float
    read_pages: int
    read_workers: int
    read_s: float
    read_page_s: float
    browser_pages: int
    browser_s: float
    browser_page_s: float
    vision_pages: int
    wall_s: float

    def browser_allowed(self) -> bool:
        return self.browser_pages > 0


# Spark: the rapid path. One browser page, and only as the last thing before a
# refusal — trading a three-second "no sources" for a ten-second real answer.
SPARK = Budget(
    mode=MODE_SPARK,
    search_s=4.0,
    read_pages=3,
    read_workers=3,
    read_s=3.5,
    read_page_s=3.0,
    browser_pages=1,
    browser_s=4.5,
    browser_page_s=4.5,
    vision_pages=0,
    wall_s=8.0,
)

# Zenith: depth is the point. Parallel, but bounded — three workers, not eight,
# because the sidecar drives one browser and queueing beats thrashing.
ZENITH = Budget(
    mode=MODE_ZENITH,
    search_s=8.0,
    read_pages=8,
    read_workers=3,
    read_s=14.0,
    read_page_s=8.0,
    browser_pages=4,
    browser_s=22.0,
    browser_page_s=9.0,
    vision_pages=1,
    wall_s=60.0,
)

# Supercomputer: research support for a mode that already has its own visible
# browser. This is the backend fallback for pages that tab cannot read.
SUPERCOMPUTER = Budget(
    mode=MODE_SUPERCOMPUTER,
    search_s=8.0,
    read_pages=4,
    read_workers=2,
    read_s=10.0,
    read_page_s=8.0,
    browser_pages=2,
    browser_s=16.0,
    browser_page_s=9.0,
    vision_pages=1,
    wall_s=45.0,
)

# Anything not on a user's turn: generous wall, minimal parallelism, so a
# background job never competes with a live question for the browser.
BACKGROUND = Budget(
    mode=MODE_BACKGROUND,
    search_s=10.0,
    read_pages=6,
    read_workers=1,
    read_s=30.0,
    read_page_s=10.0,
    browser_pages=2,
    browser_s=25.0,
    browser_page_s=12.0,
    vision_pages=0,
    wall_s=120.0,
)

_BY_MODE: Dict[str, Budget] = {
    MODE_SPARK: SPARK,
    MODE_ZENITH: ZENITH,
    MODE_SUPERCOMPUTER: SUPERCOMPUTER,
    MODE_BACKGROUND: BACKGROUND,
}


def for_mode(mode: str, config: Optional[Dict[str, Any]] = None) -> Budget:
    """Budget for a mode name. Unknown modes get Spark's — the cheap one.

    Defaulting an unrecognised caller to the *tightest* allowance is deliberate:
    a new call site that forgets to name its mode should be slow to notice
    because it is fast, not because it quietly acquired Zenith's budget.
    """
    budget = _BY_MODE.get(str(mode or "").strip().lower(), SPARK)
    cfg = config or {}
    if budget.mode == MODE_SPARK and not bool(
        cfg.get("web_gateway_spark_browser_enabled", True)
    ):
        # Kill switch: Spark keeps the ladder but not the top rung.
        return Budget(**{**budget.__dict__, "browser_pages": 0})
    if not bool(cfg.get("web_gateway_vision_enabled", False)):
        return Budget(**{**budget.__dict__, "vision_pages": 0})
    return budget


__all__ = [
    "BACKGROUND",
    "Budget",
    "MODE_BACKGROUND",
    "MODE_SPARK",
    "MODE_SUPERCOMPUTER",
    "MODE_ZENITH",
    "SPARK",
    "SUPERCOMPUTER",
    "ZENITH",
    "for_mode",
]
