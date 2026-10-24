"""Stay out of the way of whatever the user is actually doing.

The requirement is exact: a task running in the background must not stop him
playing a demanding game. That rules out the usual answer — "it only uses one
core" — because the cost is not the agent's arithmetic, it is a Chromium
browser laying out pages, a Python process holding the GPU awake, and a loop
that never pauses.

Three levers, in order of how much they buy:

1. **Pace.** Between steps the loop waits. Normally that wait is nothing; with
   a full-screen application in front it becomes a real gap, which hands the
   GPU and the scheduler back for most of every second. A task takes longer and
   the game does not stutter, which is the intended trade.
2. **Priority.** The backend drops to below-normal while a task runs, so the
   operating system prefers the foreground application whenever they compete.
   Restored when the task ends.
3. **Concurrency.** Read-only workers are capped, and capped harder under
   pressure, so "parallel research" never becomes four Chromium renderers.

How pressure is detected
------------------------
By asking the window manager, which is free, and NEVER by polling ``nvidia-smi``
— that was removed from SYRIX deliberately (it was spawning a process eight
times a second) and must not come back through this door. A full-screen window
that is not SYRIX is a game, a video or a presentation; all three mean "leave
the machine alone". The answer is cached for a few seconds because the loop
asks once per step and the truth does not change that fast.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from typing import Any, Dict, Optional, Tuple

import syrix_obs as obs

# ── modes ──────────────────────────────────────────────────────────────────
MODE_AUTO = "auto"            # get out of the way when something demanding runs
MODE_PERFORMANCE = "performance"  # never throttle; the user is watching it work
MODE_ECO = "eco"              # always gentle, even on an idle machine
MODES = (MODE_AUTO, MODE_PERFORMANCE, MODE_ECO)

_DEFAULT_MODE = MODE_AUTO

# Seconds of pause between agent steps, by how much room there is.
# Zero when nothing else is running: an unpressured machine should be fast.
PACE_FREE = 0.0
PACE_ECO = 1.2
# Under real pressure the loop yields for most of every step.
PACE_PRESSED = 2.5

# How many read-only workers may run at once. Two is enough to overlap network
# waits; four would just contend.
WORKERS_FREE = 2
WORKERS_PRESSED = 1

# The foreground check is cheap but not free, and the answer is stable.
_PRESSURE_TTL_S = 4.0

_LOCK = threading.RLock()
_mode: str = _DEFAULT_MODE
_pressure_cache: Tuple[float, bool] = (0.0, False)
_priority_lowered = False


def mode() -> str:
    with _LOCK:
        return _mode


def set_mode(value: str) -> str:
    global _mode
    v = str(value or "").strip().lower()
    if v in MODES:
        with _LOCK:
            _mode = v
    return mode()


def reset_mode(config: Optional[Dict[str, Any]] = None) -> str:
    global _mode
    configured = str((config or {}).get("supercomputer_resource_mode", "") or "").strip().lower()
    with _LOCK:
        _mode = configured if configured in MODES else _DEFAULT_MODE
    return mode()


def enabled(config: Optional[Dict[str, Any]] = None) -> bool:
    return bool((config or {}).get("supercomputer_resource_governor_enabled", True))


# ── pressure ───────────────────────────────────────────────────────────────
def _foreground_is_fullscreen() -> bool:
    """Is a window that is NOT ours covering a whole monitor?

    Windows only, by design: this is where the user games. Anywhere else the answer
    is False and the governor simply never engages, which is the safe default.
    """
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False

        # Ours? Then the user is watching Supercomputer work, not gaming.
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if int(pid.value) == os.getpid():
            return False

        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        screen_w = user32.GetSystemMetrics(0)
        screen_h = user32.GetSystemMetrics(1)
        if screen_w <= 0 or screen_h <= 0:
            return False
        # Covers the monitor within a couple of pixels: full screen, or a
        # borderless-window game, which is how most of them run now.
        return width >= screen_w - 2 and height >= screen_h - 2
    except Exception as exc:
        obs.report("supercomputer.resources.foreground", exc, level="debug")
        return False


def under_pressure(config: Optional[Dict[str, Any]] = None) -> bool:
    """Is something demanding in front? Cached, so a step may ask freely."""
    global _pressure_cache
    current = mode()
    if current == MODE_PERFORMANCE or not enabled(config):
        return False
    if current == MODE_ECO:
        return True
    now = time.monotonic()
    with _LOCK:
        stamp, value = _pressure_cache
        if now - stamp < _PRESSURE_TTL_S:
            return value
    # Defensive a second time, on purpose: this is called once per agent step,
    # and a governor that throws inside the loop it is pacing would kill the
    # task it exists to be polite about. Unknown means "no pressure", which
    # keeps the task running at full speed rather than stalling it forever.
    try:
        found = _foreground_is_fullscreen()
    except Exception as exc:
        obs.report("supercomputer.resources.pressure", exc, level="debug")
        found = False
    with _LOCK:
        _pressure_cache = (now, found)
    return found


def step_pause(config: Optional[Dict[str, Any]] = None) -> float:
    """How long the loop should wait before its next step."""
    if not enabled(config):
        return PACE_FREE
    current = mode()
    if current == MODE_PERFORMANCE:
        return PACE_FREE
    if under_pressure(config):
        return float(config.get("supercomputer_pace_pressed_s", PACE_PRESSED)
                     if config else PACE_PRESSED)
    if current == MODE_ECO:
        return PACE_ECO
    return PACE_FREE


def max_workers(config: Optional[Dict[str, Any]] = None) -> int:
    """How many read-only workers may run at once."""
    if not enabled(config):
        return WORKERS_FREE
    return WORKERS_PRESSED if under_pressure(config) else WORKERS_FREE


# ── process priority ───────────────────────────────────────────────────────
def lower_priority(config: Optional[Dict[str, Any]] = None) -> bool:
    """Ask the OS to prefer the foreground application over this process.

    Idempotent, and a no-op where it is not supported. Deliberately NOT
    "idle" priority: the backend still serves normal chat and voice, and
    starving those to be polite to a game would be the wrong trade.
    """
    global _priority_lowered
    if not enabled(config):
        return False
    with _LOCK:
        if _priority_lowered:
            return True
    ok = _set_priority(below_normal=True)
    if ok:
        with _LOCK:
            _priority_lowered = True
    return ok


def restore_priority(config: Optional[Dict[str, Any]] = None) -> bool:
    """Back to normal when no task is running. Idempotent."""
    global _priority_lowered
    with _LOCK:
        if not _priority_lowered:
            return False
    ok = _set_priority(below_normal=False)
    with _LOCK:
        _priority_lowered = False
    return ok


def _set_priority(*, below_normal: bool) -> bool:
    with obs.swallow("supercomputer.resources.priority"):
        import psutil

        proc = psutil.Process(os.getpid())
        if sys.platform.startswith("win"):
            proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if below_normal
                      else psutil.NORMAL_PRIORITY_CLASS)
        else:
            proc.nice(5 if below_normal else 0)
        return True
    return False


def snapshot(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """What the settings panel and the Island show."""
    return {
        "mode": mode(),
        "modes": list(MODES),
        "pressure": under_pressure(config),
        "pause_s": step_pause(config),
        "workers": max_workers(config),
        "priority_lowered": _priority_lowered,
        "enabled": enabled(config),
    }


def _reset_for_tests() -> None:
    global _mode, _pressure_cache, _priority_lowered
    with _LOCK:
        _mode = _DEFAULT_MODE
        _pressure_cache = (0.0, False)
        _priority_lowered = False


__all__ = [
    "MODE_AUTO", "MODE_PERFORMANCE", "MODE_ECO", "MODES",
    "PACE_FREE", "PACE_ECO", "PACE_PRESSED",
    "mode", "set_mode", "reset_mode", "enabled", "under_pressure",
    "step_pause", "max_workers", "lower_priority", "restore_priority",
    "snapshot",
]
