"""SYRIX runtime path resolver — keeps live state out of the repo.

The code lives at C:\\SYRIX (moved out of OneDrive on 2026-07-08), and
high-frequency runtime files (hud_state.json ticks, logs, PID files, caches)
live under %LOCALAPPDATA%\\SYRIX. Historically this split existed because
OneDrive's sync engine took write locks that stalled or dropped writes
mid-tick; it is kept because state must survive repo moves and checkouts.

Resolution order for the root directory:
  1. env ``SYRIX_STATE_DIR``      (set by probes/tests or the Electron spawner)
  2. ``%LOCALAPPDATA%\\SYRIX``
  3. ``~\\AppData\\Local\\SYRIX``  (when LOCALAPPDATA is unset)
  4. ``%TEMP%\\SYRIX``             (last resort — never fails)

Layout under the root:
  state\\    hot JSON mailboxes (hud_state.json, hud_control.json, ...)
  logs\\     syrix.log*, backend_autospawn.log, syrix_startup.log
  cache\\    brain/research caches
  run\\      PID files and one-shot run artifacts
  secrets.dat   DPAPI-encrypted provider keys

Stdlib-only on purpose: imported before everything else, by the backend, by
tools/ probes, and indirectly mirrored by hud_electron/syrix-paths.js — the
two resolvers MUST stay rule-identical.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
from typing import Callable, Dict, Optional

# Runtime mailbox files that historically lived at the repo root. Any path
# whose basename matches one of these is redirected into state_dir().
RUNTIME_STATE_FILES = frozenset(
    {
        "hud_state.json",
        "hud_control.json",
        "hud_chat.json",
        "hud_ready.json",
        "hud_brain_state.json",
        "hud_free_web_state.json",
        "hud_brain_control_result.json",
        "hud_hand_control_event.json",
        "hud_voice_sync.json",
    }
)

_MIGRATION_MARKER = ".migrated_from_onedrive"
_LEGACY_BAK_SUFFIX = ".pre-localappdata.bak"

_lock = threading.Lock()
_cached_root: Optional[str] = None
_ensured_dirs: set = set()


def _compute_root() -> str:
    env_dir = str(os.environ.get("SYRIX_STATE_DIR") or "").strip()
    if env_dir:
        return os.path.abspath(env_dir)
    local = str(os.environ.get("LOCALAPPDATA") or "").strip()
    if not local:
        home = os.path.expanduser("~")
        candidate = os.path.join(home, "AppData", "Local")
        local = candidate if os.path.isdir(candidate) else ""
    if not local:
        local = tempfile.gettempdir()
    return os.path.join(local, "SYRIX")


def syrix_root() -> str:
    """Root of the SYRIX runtime tree (cached for the process lifetime)."""
    global _cached_root
    with _lock:
        if _cached_root is None:
            _cached_root = _compute_root()
        return _cached_root


def _ensure_dir(path: str) -> str:
    with _lock:
        if path not in _ensured_dirs:
            try:
                os.makedirs(path, exist_ok=True)
            except Exception:
                pass
            _ensured_dirs.add(path)
    return path


def state_dir() -> str:
    return _ensure_dir(os.path.join(syrix_root(), "state"))


def logs_dir() -> str:
    return _ensure_dir(os.path.join(syrix_root(), "logs"))


def cache_dir() -> str:
    return _ensure_dir(os.path.join(syrix_root(), "cache"))


def run_dir() -> str:
    return _ensure_dir(os.path.join(syrix_root(), "run"))


def secrets_path() -> str:
    _ensure_dir(syrix_root())
    return os.path.join(syrix_root(), "secrets.dat")


def state_file(name: str) -> str:
    """Absolute path for a runtime mailbox file (always in state_dir)."""
    return os.path.join(state_dir(), os.path.basename(str(name or "")))


def run_file(name: str) -> str:
    return os.path.join(run_dir(), os.path.basename(str(name or "")))


def log_file(name: str) -> str:
    return os.path.join(logs_dir(), os.path.basename(str(name or "")))


def legacy_root() -> str:
    """The old runtime location: the repo directory itself (inside OneDrive)."""
    return os.path.dirname(os.path.abspath(__file__))


def resolve_runtime_path(path: str) -> str:
    """Redirect any path pointing at a known runtime mailbox into state_dir().

    Safety net for legacy call sites that still build
    ``join(<repo>, "hud_state.json")`` by hand: the basename decides, so both
    repo-root and already-redirected paths normalize to the same file.
    Non-runtime paths pass through untouched.
    """
    try:
        base = os.path.basename(str(path or ""))
        if base.lower() in RUNTIME_STATE_FILES:
            return state_file(base)
    except Exception:
        pass
    return path


def migrate_legacy_state(log_fn: Optional[Callable[[str], None]] = None) -> Dict[str, int]:
    """One-time move of runtime files out of the OneDrive repo root.

    For every runtime mailbox found at the legacy location: copy it into
    state_dir() (unless a newer copy already exists there), then rename the
    legacy file to ``<name>.pre-localappdata.bak`` so stale pollers can't
    read divergent data while a recovery copy still exists. Also relocates
    ``data/ollama_started_by_syrix.pid``. Idempotent and never raises.
    """
    summary = {"copied": 0, "parked": 0, "skipped": 0, "errors": 0}

    def _log(msg: str) -> None:
        if log_fn is not None:
            try:
                log_fn(msg)
            except Exception:
                pass

    legacy = legacy_root()
    target_dir = state_dir()
    for name in sorted(RUNTIME_STATE_FILES):
        src = os.path.join(legacy, name)
        if not os.path.isfile(src):
            continue
        dst = os.path.join(target_dir, name)
        try:
            src_mtime = os.path.getmtime(src)
            dst_mtime = os.path.getmtime(dst) if os.path.isfile(dst) else -1.0
            if src_mtime > dst_mtime:
                shutil.copy2(src, dst)
                summary["copied"] += 1
            else:
                summary["skipped"] += 1
            bak = src + _LEGACY_BAK_SUFFIX
            try:
                if os.path.isfile(bak):
                    os.unlink(bak)
                os.rename(src, bak)
                summary["parked"] += 1
            except Exception:
                summary["errors"] += 1
        except Exception:
            summary["errors"] += 1

    # PID file: data/ollama_started_by_syrix.pid → run/
    try:
        legacy_pid = os.path.join(legacy, "data", "ollama_started_by_syrix.pid")
        if os.path.isfile(legacy_pid):
            new_pid = run_file("ollama_started_by_syrix.pid")
            if not os.path.isfile(new_pid):
                shutil.copy2(legacy_pid, new_pid)
                summary["copied"] += 1
            try:
                os.unlink(legacy_pid)
                summary["parked"] += 1
            except Exception:
                summary["errors"] += 1
    except Exception:
        summary["errors"] += 1

    # Tombstone so humans browsing the repo know where state went.
    try:
        marker = os.path.join(target_dir, _MIGRATION_MARKER)
        if not os.path.isfile(marker):
            with open(marker, "w", encoding="utf-8") as fh:
                fh.write(f"runtime state migrated from {legacy}\n")
    except Exception:
        pass

    if summary["copied"] or summary["parked"] or summary["errors"]:
        _log(
            "[SYRIX_PATHS] migrate_legacy_state copied=%d parked=%d skipped=%d errors=%d root=%r"
            % (summary["copied"], summary["parked"], summary["skipped"], summary["errors"], syrix_root())
        )
    return summary


def _reset_for_tests() -> None:
    """Test hook: forget the cached root so SYRIX_STATE_DIR changes apply."""
    global _cached_root
    with _lock:
        _cached_root = None
        _ensured_dirs.clear()
