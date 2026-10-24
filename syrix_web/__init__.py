"""SYRIX Web Capability Gateway — the one door to the live internet.

Before this package there were three unconnected web stacks: the ATLAS
retrieval lanes (:mod:`syrix_core_v2.atlas`), a complete duplicate research
pipeline (:mod:`syrix_core_v2.research_mission`), and the Supercomputer's
Electron BrowserView. Each had its own fetcher, its own idea of which URLs are
safe, and its own failure message. That is why SYRIX said "no verified sources"
so often: whichever stack you landed in, its ladder was three rungs short.

This package supplies **evidence and browser access**. It does not reason, does
not synthesise, does not own conversation state, and does not decide *whether*
fresh data is needed — that stays with :mod:`syrix_core_v2.atlas.gate`. Callers
hand it a URL or a query and a mode, and get back grounded material in the
shapes the existing engines already consume.

Layout::

    policy.py      the ONE url validator (scheme, SSRF, DNS, redirects)
    audit.py       redacted JSONL trail
    injection.py   untrusted-page fencing
    budget.py      per-mode time/page budgets
    snapshot.py    accessibility tree -> text/elements
    browser_mcp.py persistent @playwright/mcp session (lazy, idle-shutdown)
    public_proxy.py DNS-pinned public-only transport for every browser request
    gateway.py     the escalation ladder

Everything is **synchronous**. The house rule (``syrix_mcp_layer:15-17``) is
that async is wrapped and never propagated: the reply path is threaded, and a
coroutine escaping into it would contend with the HUD bridge's event loop.

Kill switch ``web_gateway_enabled``. With it off, every call site falls back to
the code path that existed before this package — nothing here is load-bearing
for a degraded SYRIX.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def _live_config() -> Dict[str, Any]:
    try:
        import core_boot

        return core_boot.CONFIG
    except Exception:
        return {}


def enabled(config: Optional[Dict[str, Any]] = None) -> bool:
    """Master switch. False ⇒ callers keep their pre-gateway behaviour."""
    cfg = config if config is not None else _live_config()
    try:
        return bool(cfg.get("web_gateway_enabled", True))
    except Exception:
        return False


__all__ = ["enabled"]
