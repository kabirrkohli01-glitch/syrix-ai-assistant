"""Supercomputer browser sandbox.

Supercomputer drives ONLY the BrowserViews it created itself, inside its own
ephemeral Electron partition. The boundary is structural, not advisory: the
Electron module keeps its own view registry and its IPC handlers resolve targets
only from that registry, so there is no expressible operation against the main
HUD window, the existing HUD app dock, another application, or the desktop.

This package is deliberately separate from :mod:`syrix_core_v2.sandbox_browser`
(the older app-dock automation). It shares no state with it and does not modify
its code path, so the dock's behaviour cannot regress.
"""
from __future__ import annotations

__all__ = ["controller", "policy"]
