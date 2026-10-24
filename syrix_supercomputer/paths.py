"""Supercomputer state paths — everything under ``%LOCALAPPDATA%\\SYRIX\\supercomputer``.

Resolved through :mod:`syrix_paths` (the canonical state-root resolver) so state
survives repo moves and checkouts exactly like the HUD mailboxes. Nothing here
is read by the Electron side — the HUD sees Supercomputer state only through the
``syrix_supercomputer`` key in ``hud_state.json`` — so the two-resolver rule
(``syrix_paths.py`` ↔ ``hud_electron/syrix-paths.js``) is deliberately NOT
extended by this feature.

Every function resolves at call time so tests can redirect via
``SYRIX_STATE_DIR`` or by monkeypatching :func:`supercomputer_dir`.

Note what is absent: there is no ``permissions.json``. Grants are in-memory only
and void on task end, mode exit and app restart, so there is nothing to persist.
Capability *toggles* live in the main ``config.json`` alongside every other
SYRIX flag — there is no separate settings file, and the ``settings_path()``
helper that once promised one has been removed rather than left pointing at a
file nothing reads.

What IS persisted, since 2026-07-26, is the per-task **memory brief**
(:mod:`syrix_supercomputer.memory`). That is work product — what was read, what
was found, what was made — never authority. A brief cannot grant permission, so
persisting it leaves the "grants are in-memory only" rule untouched.
"""
from __future__ import annotations

import os
import threading
from typing import Tuple

import syrix_obs as obs


def supercomputer_dir() -> str:
    """Root directory for all Supercomputer runtime state (created on demand)."""
    try:
        import syrix_paths

        root = syrix_paths.syrix_root()
    except Exception as exc:  # pragma: no cover — syrix_paths is stdlib-only
        obs.report("supercomputer.paths.root", exc)
        root = os.path.join(os.path.expanduser("~"), "AppData", "Local", "SYRIX")
    d = os.path.join(root, "supercomputer")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception as exc:
        obs.report("supercomputer.paths.mkdir", exc, dir=d)
    return d


def memory_dir() -> str:
    """Per-task memory briefs — what the agent learned, kept across a card."""
    d = os.path.join(supercomputer_dir(), "tasks")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception as exc:
        obs.report("supercomputer.paths.memory_mkdir", exc, dir=d)
    return d


def task_memory_path(task_id: str) -> str:
    """The brief for one task. ``task_id`` is sanitised like a workspace name."""
    safe = "".join(ch for ch in str(task_id or "") if ch.isalnum() or ch in "_-")[:64]
    return os.path.join(memory_dir(), f"{safe or 'unnamed'}.json")


def task_evidence_path(task_id: str) -> str:
    """Everything one task established, kept beside its brief.

    Separate from the brief because they do different jobs: the brief is small
    and replayed into every resume prompt, this is large and read once by the
    final writer. Same sanitising, same directory.
    """
    safe = "".join(ch for ch in str(task_id or "") if ch.isalnum() or ch in "_-")[:64]
    return os.path.join(memory_dir(), f"{safe or 'unnamed'}.evidence.json")


def audit_path() -> str:
    """Append-only JSONL audit of every gated action and permission decision."""
    return os.path.join(supercomputer_dir(), "audit.jsonl")


# A folder the user chose for SYRIX to work in, instead of the hidden default under
# %LOCALAPPDATA%. Set from the workspace's "Working folder" button and held for
# the session — a place he can actually open, rather than files he has to be
# told the path of. Empty means the default.
_LOCK = threading.RLock()
_workspace_override: str = ""


def set_workspace_root(path: str) -> Tuple[bool, str]:
    """Point the workspaces tree at a folder of the user's choosing.

    Validated before it is accepted, because everything the agent writes lands
    under it: it must be a real, writable directory, and it must not be SYRIX's
    own repo — an agent writing into the assistant that is running it is the
    most expensive possible mistake.
    """
    global _workspace_override
    raw = str(path or "").strip().strip('"')
    if not raw:
        with _LOCK:
            _workspace_override = ""
        return True, ""
    try:
        resolved = os.path.realpath(os.path.expanduser(raw))
    except Exception as exc:
        obs.report("supercomputer.paths.workspace_resolve", exc, path=raw)
        return False, "unreadable_path"
    if not os.path.isdir(resolved):
        return False, "not_a_folder"
    if not os.access(resolved, os.W_OK):
        return False, "not_writable"
    try:
        from . import workspace as _ws

        if _ws.is_syrix_path(resolved):
            return False, "refused_syrix_repo"
    except Exception as exc:
        obs.report("supercomputer.paths.workspace_repo_check", exc, level="debug")
    with _LOCK:
        _workspace_override = resolved
    return True, resolved


def workspace_root() -> str:
    """Where SYRIX works. The user's chosen folder, or the default."""
    with _LOCK:
        chosen = _workspace_override
    return chosen or os.path.join(supercomputer_dir(), "workspaces")


def workspaces_dir() -> str:
    """Parent of the per-task isolated coding workspaces (Phase 7)."""
    d = workspace_root()
    try:
        os.makedirs(d, exist_ok=True)
    except Exception as exc:
        obs.report("supercomputer.paths.workspaces_mkdir", exc, dir=d)
    return d


def task_workspace(task_id: str) -> str:
    """Workspace for one task. ``task_id`` is sanitised: it reaches this from a
    generated id, but treating it as untrusted keeps the join from escaping."""
    safe = "".join(ch for ch in str(task_id or "") if ch.isalnum() or ch in "_-")[:64]
    if not safe:
        safe = "unnamed"
    d = os.path.join(workspaces_dir(), safe)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception as exc:
        obs.report("supercomputer.paths.task_mkdir", exc, dir=d)
    return d


def cache_dir() -> str:
    """Browser-derived scratch (page text, snapshots) — wiped on chat delete."""
    d = os.path.join(supercomputer_dir(), "cache")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception as exc:
        obs.report("supercomputer.paths.cache_mkdir", exc, dir=d)
    return d


def _reset_for_tests() -> None:
    global _workspace_override
    with _LOCK:
        _workspace_override = ""


__all__ = [
    "supercomputer_dir",
    "memory_dir",
    "task_memory_path",
    "audit_path",
    "set_workspace_root",
    "workspace_root",
    "workspaces_dir",
    "task_workspace",
    "cache_dir",
]
