"""Connector state paths — everything lives under ``%LOCALAPPDATA%\\SYRIX\\connectors``.

Resolved through :mod:`syrix_paths` (the canonical state-root resolver) so
connector state survives repo moves/checkouts exactly like the HUD mailboxes.
Nothing here is read by the Electron side — the HUD sees connector state only
through the ``syrix_connectors`` key in ``hud_state.json`` — so the
two-resolver rule (syrix_paths.py ↔ syrix-paths.js) is not extended.

Every function resolves at call time (no import-time caching beyond what
syrix_paths itself does) so tests can redirect via ``SYRIX_STATE_DIR`` or by
monkeypatching :func:`connectors_dir`.
"""
from __future__ import annotations

import os

import syrix_obs as obs


def connectors_dir() -> str:
    """Root directory for all connector runtime state (created on demand)."""
    try:
        import syrix_paths

        root = syrix_paths.syrix_root()
    except Exception as exc:  # pragma: no cover — syrix_paths is stdlib-only
        obs.report("connectors.paths.root", exc)
        root = os.path.join(os.path.expanduser("~"), "AppData", "Local", "SYRIX")
    d = os.path.join(root, "connectors")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception as exc:
        obs.report("connectors.paths.mkdir", exc, dir=d)
    return d


def tokens_dir() -> str:
    """Directory for DPAPI-encrypted token blobs."""
    d = os.path.join(connectors_dir(), "tokens")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception as exc:
        obs.report("connectors.paths.tokens_mkdir", exc, dir=d)
    return d


def state_path() -> str:
    """Non-secret per-connector state (permissions, task toggles, health)."""
    return os.path.join(connectors_dir(), "connectors_state.json")


def audit_path() -> str:
    """Append-only JSONL audit log of every connector interaction."""
    return os.path.join(connectors_dir(), "audit.jsonl")


__all__ = ["connectors_dir", "tokens_dir", "state_path", "audit_path"]
