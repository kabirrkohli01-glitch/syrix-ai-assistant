"""SYRIX Connectors — secure, permission-driven external-service integrations.

Subsystem layout (framework schema v1):

    base.py          contracts: ConnectorBase, ConnectorAction, health states
    registry.py      static catalogue + lazy instance cache + version quarantine
    manager.py       THE choke point: gates → confirm → budget → audit → result
    permissions.py   granular per-action allow/block + task toggles (deny-unknown)
    secure_store.py  DPAPI token vault (encrypted or not at all)
    audit.py         append-only JSONL of every interaction (redacted)
    scheduler.py     opt-in background tasks (proactive_loop pattern)
    events.py        "Using <name> connector" thinking-panel + island notices
    tool_surface.py  brain tools for connected+allowed actions ONLY
    hud_api.py       HUD command handlers + syrix_connectors state snapshot
    auth/            provider auth (Google loopback OAuth)
    connectors/      implementations (gmail, ...)

Kill switches (config.json): ``connectors_enabled`` (master),
``connectors_tools_enabled`` (brain exposure), ``connectors_background_enabled``
(scheduler). All default true; the subsystem is inert until a connector is
connected from the HUD Connectors page.

Import cost is intentionally ~zero: heavy libraries (googleapiclient, ...)
load lazily inside actions, never at package import.
"""
from __future__ import annotations

from .base import SCHEMA_VERSION

__all__ = ["SCHEMA_VERSION"]
