"""Connector lifecycle + usage events → thinking panel and Dynamic Island.

Two audiences:

* ``announce_usage`` — the per-turn "Using Gmail connector" line in the HUD
  thinking panel (Research Activity Feed). Fired by the manager on EVERY
  action execution so the user always sees when an answer touched a
  connector. Rides the existing ``syrix_core_v2.activity_feed`` ring →
  ``hud_state.json`` → every theme renders it; labels are redacted by the
  feed itself.
* ``island`` / ``lifecycle`` — one-shot notices ("Gmail connected",
  "Gmail needs re-authentication") through syrix_host's island publisher,
  lazy-imported exactly like ``syrix_core_v2.proactive_loop`` does to avoid
  circular imports.

Everything here is best-effort telemetry: failures are logged via obs and
never propagate into the action path.
"""
from __future__ import annotations

from typing import Optional

import syrix_obs as obs


def announce_usage(connector_name: str, action_label: str, *,
                   detail: str = "", request_id: str = "") -> None:
    """Thinking-panel pill: ``🔌 Using <name> connector · <action>``.

    Emits the first-class ``connector`` kind so the HUD renders a distinct
    (amber, glowing, named) pill — never a generic tool step.
    """
    try:
        from syrix_core_v2 import activity_feed as _feed

        _feed.emit(
            _feed.KIND_CONNECTOR,
            f"Using {connector_name} connector",
            detail=str(action_label or "")[:120],
            request_id=request_id or "",
            provider="connectors",
        )
    except Exception as exc:
        obs.report("connectors.events.announce_usage", exc, connector=connector_name)


def announce_result(connector_name: str, action_label: str, *, ok: bool,
                    summary: str = "", request_id: str = "") -> None:
    """Thinking-panel follow-up pill: what the connector returned / the failure."""
    try:
        from syrix_core_v2 import activity_feed as _feed

        _feed.emit(
            _feed.KIND_CONNECTOR,
            (f"{connector_name} connector · {action_label} ✓" if ok
             else f"{connector_name} connector · {action_label} failed"),
            detail=summary[:200],
            request_id=request_id or "",
            provider="connectors",
        )
    except Exception as exc:
        obs.report("connectors.events.announce_result", exc, connector=connector_name)


def island(text: str, *, tone: str = "info", kind: str = "connectors",
           ttl_ms: int = 6000, dedupe_seconds: float = 300.0) -> bool:
    """Publish a Dynamic Island notice via syrix_host (lazy, never circular)."""
    try:
        import syrix_host as _jc  # type: ignore

        pub = getattr(_jc, "_publish_island_notice", None)
        if callable(pub):
            return bool(pub(text, tone=tone, kind=kind,
                            ttl_ms=ttl_ms, dedupe_seconds=dedupe_seconds))
    except Exception as exc:
        obs.report("connectors.events.island", exc)
    return False


def lifecycle(connector_name: str, event: str, detail: str = "") -> None:
    """Surface a lifecycle change: connected / disconnected / needs_reauth / error."""
    messages = {
        "connected": (f"{connector_name} connected.", "success"),
        "disconnected": (f"{connector_name} disconnected.", "info"),
        "needs_reauth": (f"{connector_name} needs re-authentication — open Connectors.", "warning"),
        "error": (f"{connector_name} connector error{': ' + detail if detail else ''}.", "warning"),
    }
    text, tone = messages.get(event, (f"{connector_name}: {event}", "info"))
    island(text, tone=tone, kind=f"connectors:{event}")


__all__ = ["announce_usage", "announce_result", "island", "lifecycle"]
