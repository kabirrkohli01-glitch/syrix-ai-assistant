"""The local model, started only when it is needed and stopped when it is not.

The local lane is what makes SYRIX unlimited: every cloud lane runs to a
published allowance and refills, and underneath them is a model on this
machine that does not. Eight of the forty baseline questions died because
that floor was absent — the turn was correctly pinned to the local lane and
the local lane was not listening.

Leaving it running solves that and costs something real. A loaded model
holds VRAM, and VRAM held is a GPU that will not clock down, which is heat,
fan and watts on a machine that is also used for games. So it is supervised
rather than left up:

* **Started on demand.** Nothing launches it at boot. The first turn that
  cannot reach a cloud lane starts it, waits for it, and answers.
* **Unloaded quickly.** ``OLLAMA_KEEP_ALIVE`` is short, so the weights leave
  VRAM within a minute of the last answer and the card idles again.
* **Stopped when idle.** The server process itself is shut down after a
  longer quiet period, so nothing is resident at all between sessions.
* **Never outlives SYRIX.** Registered with ``atexit`` and shut down
  explicitly by the host, with a pid file so a process orphaned by a crash
  is cleaned up on the next start rather than surviving indefinitely.
* **Never kills a server it did not start.** If Ollama is already listening
  when SYRIX arrives, it is used and left alone on the way out.
"""
from __future__ import annotations

import atexit
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Optional

from . import config, state
import syrix_obs

_HOST = "127.0.0.1"
_PORT = 11434
_BASE = f"http://{_HOST}:{_PORT}"
_PIDFILE = "ollama.pid"
_DIR = "runtime"

# Weights leave VRAM this long after the last token. Short on purpose: the
# reload costs a few seconds on a lane that is already the fallback, and
# holding a card awake costs watts for as long as it is held.
_KEEP_ALIVE = "45s"
# The server process itself is stopped after this much quiet.
_IDLE_STOP_S = 300.0
# How long to wait for a cold start before giving up on it for this turn.
_START_TIMEOUT_S = 25.0

_lock = threading.RLock()
_child: Optional[subprocess.Popen] = None
_ours = False
_last_used = 0.0
_watchdog: Optional[threading.Thread] = None
_stopping = False


@dataclass
class Status:
    running: bool = False
    ours: bool = False
    pid: int = 0
    idle_s: float = 0.0
    detail: str = ""


def _binary() -> str:
    configured = str(config.get("ollama_binary", "") or "").strip()
    if configured and os.path.exists(configured):
        return configured
    candidates = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe"),
        os.path.join(os.environ.get("ProgramFiles", ""), "Ollama", "ollama.exe"),
        "ollama",
    ]
    for path in candidates:
        if path == "ollama" or (path and os.path.exists(path)):
            return path
    return "ollama"


def is_listening(timeout: float = 1.0) -> bool:
    """Whether something is already answering on the Ollama port."""
    import socket
    try:
        with socket.create_connection((_HOST, _PORT), timeout=max(0.2, timeout)):
            return True
    except OSError:
        return False


def _write_pid(pid: int) -> None:
    state.write({"pid": int(pid), "at": time.time()}, _PIDFILE, _DIR)


def _read_pid() -> int:
    row = state.read(_PIDFILE, _DIR, default={})
    try:
        return int(row.get("pid") or 0)
    except (TypeError, ValueError):
        return 0


def _clear_pid() -> None:
    state.write({}, _PIDFILE, _DIR)


def _kill(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True
    except Exception:
        return False


def reap_orphan() -> bool:
    """Kill a server left behind by a previous SYRIX that did not exit cleanly.

    Called at start-up. Only ever touches a pid this module recorded, so a
    server started outside SYRIX is never taken away while it is in use —
    and if that pid is gone but the port is dead, the whole stray family
    goes with it, since none of them is serving anything.
    """
    pid = _read_pid()
    if pid <= 0:
        return False
    killed = _kill(pid)
    _clear_pid()
    if not is_listening():
        _sweep_strays()
    return killed


def _sweep_strays() -> int:
    """Kill every ollama process when none of them is serving.

    Seven were found running at once while this was being built: the
    supervisor's own, the tray app, and several the CLI starts as a side
    effect of commands like ``ollama list``. Between them they held 6.6 GB
    of an 8 GB card, so the next model load found 488 MB free and fell back
    to CPU — which is how a lane that answers in five seconds on the GPU
    came to take a hundred and thirty on the processor.

    Only ever called when nothing is listening on the port, so a live
    server is never in scope.
    """
    killed = 0
    try:
        listing = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq ollama.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except Exception:
        return 0
    for line in listing.splitlines():
        parts = [p.strip('" ') for p in line.split('","')]
        if len(parts) < 2 or not parts[1].isdigit():
            continue
        if _kill(int(parts[1])):
            killed += 1
    return killed


def _spawn() -> bool:
    global _child, _ours
    # Nothing is listening, so anything still resident is a zombie holding
    # VRAM the model about to load will need.
    _sweep_strays()
    env = dict(os.environ)
    env["OLLAMA_KEEP_ALIVE"] = _KEEP_ALIVE
    # One model resident, one request at a time. This lane exists to keep
    # answering when everything else is spent, not to be fast, and both
    # settings keep it out of the way of whatever else is using the card.
    env.setdefault("OLLAMA_MAX_LOADED_MODELS", "1")
    env.setdefault("OLLAMA_NUM_PARALLEL", "1")
    try:
        _child = subprocess.Popen(
            [_binary(), "serve"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        _child = None
        return False
    _ours = True
    _write_pid(_child.pid)
    return True


def ensure_running(*, timeout_s: float = _START_TIMEOUT_S) -> bool:
    """Make the local lane answerable. Returns whether it is."""
    global _last_used
    with _lock:
        if is_listening():
            _last_used = time.time()
            _start_watchdog()
            return True
        if not _spawn():
            return False

    deadline = time.monotonic() + max(2.0, timeout_s)
    while time.monotonic() < deadline:
        if is_listening(timeout=0.5):
            with _lock:
                _last_used = time.time()
            _start_watchdog()
            return True
        time.sleep(0.4)

    # It did not come up. Do not leave a half-started process behind.
    stop()
    return False


def note_use() -> None:
    """Record that the local lane just answered, deferring the idle stop."""
    global _last_used
    with _lock:
        _last_used = time.time()


def _start_watchdog() -> None:
    global _watchdog
    with _lock:
        if not _ours or (_watchdog and _watchdog.is_alive()):
            return
        _watchdog = threading.Thread(target=_watch, name="v3ollama", daemon=True)
        _watchdog.start()


def _watch() -> None:
    while True:
        time.sleep(15.0)
        with _lock:
            if _stopping or not _ours or _child is None:
                return
            idle = time.time() - _last_used
        if idle >= _IDLE_STOP_S:
            stop()
            return


def stop() -> bool:
    """Shut down the server, but only if this process started it."""
    global _child, _ours, _stopping
    with _lock:
        if not _ours or _child is None:
            _clear_pid()
            return False
        _stopping = True
        child = _child
        _child = None
        _ours = False

    # The whole tree, always — not terminate() with a tree-kill fallback.
    # The server holds the loaded model in a separate runner process, and
    # terminating only the parent orphaned that runner: the port closed,
    # status read "not running", and 5.1 GB of VRAM stayed occupied by a
    # process nothing was tracking any more.
    try:
        _kill(child.pid)
        try:
            child.wait(timeout=6)
        except Exception as exc:
            syrix_obs.report("v3.local_model.stop", exc, level="debug")
    except Exception as exc:
        syrix_obs.report("v3.local_model.stop", exc, level="debug")
    finally:
        # Anything left is unreachable by definition: the server is down.
        if not is_listening():
            _sweep_strays()
        _clear_pid()
        with _lock:
            _stopping = False
    return True


def status() -> Status:
    with _lock:
        running = is_listening()
        return Status(
            running=running,
            ours=_ours,
            pid=(_child.pid if _child else 0),
            idle_s=round(time.time() - _last_used, 1) if _last_used else 0.0,
            detail=("started by SYRIX" if _ours
                    else "already running" if running
                    else "not running"),
        )


atexit.register(stop)
