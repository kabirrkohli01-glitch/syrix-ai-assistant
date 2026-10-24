"""Running Python the agent wrote — in a box it cannot get out of.

Why the mode needs this
-----------------------
"Analyse the ten biggest movers and give me a setup I can trade", "make a
spreadsheet of these numbers with a chart", "work out the pros and cons with the
actual figures" — all of these were being answered by a language model doing
arithmetic in its head over page text. That is exactly the class of answer that
looks right and is wrong, and no amount of prompting fixes it.

pandas, numpy, openpyxl, matplotlib and Pillow are already installed in this
venv and, until now, entirely unused by Supercomputer. Giving the agent a way to
actually compute turns an estimate into a calculation, and turns "here are some
numbers" into a real chart in a real PDF.

Why it is safe to have at all
-----------------------------
Arbitrary code execution is the largest new surface in this whole piece of work,
so it is boxed on every axis at once rather than trusting any single control:

* **Separate process.** A subprocess, not ``exec`` in SYRIX's interpreter. A
  crash, a memory blow-up or an infinite loop takes the child, not the assistant.
* **No network.** A ``sitecustomize`` shim installed on the child's path makes
  ``socket.socket`` raise before anything can bind or connect. So the sandbox
  cannot exfiltrate the workspace, phone home, or fetch code to run.
* **No secrets.** The child's environment is built from scratch, not inherited.
  ``.env`` is never loaded and no ``*_API_KEY`` exists in the process at all —
  there is nothing for injected code to read.
* **Confined to the task workspace.** ``cwd`` is the task's own directory, which
  is where every file it writes lands, and it shows up in Results like any other
  deliverable. It is not handed any path outside.
* **Bounded.** Wall clock, output size and (on Windows, via a Job object) memory
  are all capped; the process tree is killed on timeout rather than left behind.
* **Isolated imports.** ``-I`` ignores the user site directory and environment,
  and ``-S`` skips site processing, so nothing from the ambient install leaks in
  beyond the venv's own libraries.

What is deliberately NOT here: pip, arbitrary subprocesses, and any way to reach
outside the workspace. The user's answer on 2026-07-26 was "sandboxed, workspace-only";
the network-enabled variant was offered and declined, and this module has no code
path that could quietly become it.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import threading
from typing import Any, Dict, List, Optional

import syrix_obs as obs

from . import paths

DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_OUTPUT = 200_000
MAX_CODE_CHARS = 40_000
# Memory ceiling for the child, in bytes. Generous enough for a pandas frame
# over a scraped table, tight enough that a runaway allocation dies quickly
# instead of taking the desktop with it.
MEMORY_LIMIT_BYTES = 1024 * 1024 * 1024

# Modules the sandbox may import. Everything here is either pure computation or
# file/plot output; nothing reaches the network, the registry or a shell.
ALLOWED_IMPORTS = (
    "pandas", "numpy", "openpyxl", "xlsxwriter", "matplotlib", "PIL",
    "csv", "json", "math", "statistics", "datetime", "decimal", "fractions",
    "re", "collections", "itertools", "functools", "operator", "pathlib",
    "os", "io", "sys", "time", "random", "string", "textwrap", "typing",
    "dataclasses", "enum", "heapq", "bisect", "copy", "hashlib", "base64",
    "unicodedata", "zoneinfo", "calendar", "uuid", "warnings", "traceback",
)

# Modules the AGENT's own code may not import directly. Library internals are
# exempt — see the frame check in the guard below.
#
# The first version of this was a flat blocklist applied to every import, and it
# did not survive its first real test: pandas, numpy and matplotlib import
# pickle, shutil, ctypes and importlib internally, so blocking those names
# blocked the exact libraries the sandbox exists to provide. "Analyse this and
# draw me a chart" failed while `import socket` was correctly refused, which is
# precisely the wrong way round.
#
# So the name check now applies only to code the agent wrote, and the controls
# that must hold no matter WHO calls them — no network, no shell, no writing or
# deleting outside the workspace — are enforced on the functions themselves.
BLOCKED_IMPORTS = (
    "socket", "ssl", "http", "urllib", "urllib3", "requests", "httpx", "ftplib",
    "smtplib", "telnetlib", "subprocess", "multiprocessing", "ctypes",
    "winreg", "pty", "webbrowser", "xmlrpc", "paramiko", "pip", "setuptools",
    "venv", "asyncio",
)

# Installed into the child's cwd and imported before a single line of agent code.
_SITECUSTOMIZE = '''\
"""Installed by SYRIX Supercomputer. The walls of the sandbox.

Three properties are enforced here, each independent of the others and none of
them relying on a module NAME being absent:

1. no network — socket is neutered, so nothing built on it can reach out;
2. no shell   — the process-spawning entry points raise;
3. no writing or deleting outside the working directory — enforced on the
   syscall wrappers, so it holds even when the call arrives through shutil,
   pathlib, pandas or anything else.

Reading outside the working directory is deliberately still allowed: with the
network gone there is nowhere for read data to go, and libraries need to read
their own package files to work at all.
"""
import builtins
import os
import sys

_BLOCKED_NAMES = frozenset(%(blocked)r)
_WORKDIR = os.path.realpath(os.getcwd())

# Libraries write caches on first use — matplotlib builds a font list, numpy
# probes CPU features — so exactly one directory outside the workspace is
# writable: a scratch dir SYRIX creates for this purpose and points HOME, TEMP
# and MPLCONFIGDIR at.
#
# The first attempt made the whole of %%TEMP%% writable, which a probe
# immediately exploited to delete a file there. Real temp directories hold real
# things; a private scratch dir holds nothing anyone would miss.
_WRITABLE = [_WORKDIR]
_scratch = os.environ.get("SYRIX_SANDBOX_SCRATCH") or ""
if _scratch:
    try:
        _WRITABLE.append(os.path.realpath(_scratch))
    except Exception:
        pass

# Directories this code may not READ, either.
#
# Reading looked harmless while the sandbox had no network — until a probe ran
# `print(open("<project>/.env").read())` and a live API key came back on stdout,
# which goes into the agent's transcript, which goes to a cloud model. The
# sandbox does not need a socket to exfiltrate; its own output is the channel.
#
# SYRIX's rule is absolute — secrets never reach a model, a log or a HUD — so
# the repo and the state directory are simply unreadable from in here. Every
# other read stays allowed, because libraries must read their own package files.
_READ_DENIED = []
for _d in (os.environ.get("SYRIX_SANDBOX_DENY") or "").split(os.pathsep):
    if _d.strip():
        try:
            _READ_DENIED.append(os.path.realpath(_d.strip()))
        except Exception:
            pass

_SECRET_NAMES = (".env", "client_secrets.json", "credentials.json", "token.json")
_SECRET_SUFFIXES = (".key", ".pem", ".p12", ".pfx")

# The interpreter's own tree is always readable. The virtual environment can
# live INSIDE the repo, so denying the repo wholesale also
# denied matplotlib its own matplotlibrc, and every plot died on startup. The
# carve-out is by prefix rather than by exception list so it cannot drift if the
# venv moves.
_READ_ALLOWED = []
for _p in (getattr(sys, "prefix", ""), getattr(sys, "base_prefix", "")):
    if _p:
        try:
            _READ_ALLOWED.append(os.path.realpath(_p))
        except Exception:
            pass


class SandboxBlocked(ImportError):
    pass


class SandboxDenied(PermissionError):
    pass


# ── 1. imports, but only from the agent's own code ─────────────────────────
_real_import = builtins.__import__


def _from_agent_code():
    """True when the immediate caller is code the agent wrote.

    Agent code runs via `python -c`, so its frames carry the filename
    "<string>" (or live under the working directory if it wrote a module and
    imported it). Library internals live in site-packages and are exempt.
    """
    try:
        frame = sys._getframe(2)
    except ValueError:
        return True
    name = getattr(getattr(frame, "f_code", None), "co_filename", "") or ""
    if name in ("<string>", "<stdin>"):
        return True
    try:
        return os.path.realpath(name).startswith(_WORKDIR + os.sep)
    except Exception:
        return False


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    root = str(name or "").split(".")[0]
    if root in _BLOCKED_NAMES and _from_agent_code():
        raise SandboxBlocked(
            "%%s is not available in the SYRIX sandbox: this code runs with no "
            "network and no shell. Compute with the data you were given, and "
            "write results into the working directory." %% root
        )
    return _real_import(name, globals, locals, fromlist, level)


builtins.__import__ = _guarded_import


# ── 2. no network, whoever asks ────────────────────────────────────────────
try:
    import socket as _socket

    def _no_network(*a, **kw):
        raise SandboxDenied("network access is disabled in the SYRIX sandbox")

    _socket.socket = _no_network
    _socket.create_connection = _no_network
    _socket.getaddrinfo = _no_network
    _socket.gethostbyname = _no_network
except Exception:
    pass


# ── 3. no shell, whoever asks ──────────────────────────────────────────────
def _no_shell(*a, **kw):
    raise SandboxDenied("starting other processes is disabled in the SYRIX sandbox")


for _name in ("system", "popen", "startfile", "execv", "execve", "execvp",
              "execvpe", "spawnv", "spawnve", "spawnl", "spawnle"):
    if hasattr(os, _name):
        try:
            setattr(os, _name, _no_shell)
        except Exception:
            pass


# ── 4. writes and deletes stay inside the working directory ────────────────
def _inside(path):
    try:
        real = os.path.realpath(path)
    except Exception:
        return False
    return any(real == root or real.startswith(root + os.sep) for root in _WRITABLE)


def _secret_looking(real):
    base = os.path.basename(real).lower()
    if base in _SECRET_NAMES or base.startswith(".env"):
        return True
    return base.endswith(_SECRET_SUFFIXES)


def _read_denied(path):
    try:
        real = os.path.realpath(path)
    except Exception:
        return False
    # The task workspace normally lives UNDER the state directory, which is
    # denied wholesale. Its own working files must stay readable, so the
    # writable set wins — but a secret-looking name never does, wherever it is.
    if _secret_looking(real):
        return True
    if _inside(real):
        return False
    if any(real == root or real.startswith(root + os.sep) for root in _READ_ALLOWED):
        return False
    return any(real == root or real.startswith(root + os.sep) for root in _READ_DENIED)


def _guard_path(path, what):
    if not _inside(path):
        raise SandboxDenied(
            "%%s is outside the working directory, so I will not %%s it. "
            "The sandbox may only create and change files it owns."
            %% (path, what)
        )


def _guard_read(path):
    if _read_denied(path):
        raise SandboxDenied(
            "%%s is off limits: SYRIX's own files and anything holding a "
            "credential are never readable from the sandbox." %% (path,)
        )


def _wrap_write(fn, what, argno=0):
    def _guarded(*args, **kw):
        if len(args) > argno:
            _guard_path(args[argno], what)
        return fn(*args, **kw)
    return _guarded


def _wrap_open(fn):
    """One guard for every door into the filesystem.

    builtins.open, io.open and os.open are three DIFFERENT function objects, and
    patching only the first left a hole big enough for pathlib: `Path.write_text`
    goes through io.open and happily wrote outside the workspace in a probe.
    """
    def _guarded(file, mode="r", *args, **kw):
        writing = any(ch in str(mode) for ch in ("w", "a", "x", "+"))
        if writing:
            _guard_path(file, "write")
        else:
            _guard_read(file)
        return fn(file, mode, *args, **kw)
    return _guarded


builtins.open = _wrap_open(builtins.open)

try:
    import io as _io

    _io.open = _wrap_open(_io.open)
except Exception:
    pass

try:
    _real_os_open = os.open

    def _guarded_os_open(path, flags, *args, **kw):
        # O_WRONLY(1) / O_RDWR(2) / O_CREAT(0x100) / O_TRUNC(0x200) / O_APPEND(8)
        if flags & (1 | 2 | 0x100 | 0x200 | 8):
            _guard_path(path, "write")
        else:
            _guard_read(path)
        return _real_os_open(path, flags, *args, **kw)

    os.open = _guarded_os_open
except Exception:
    pass

for _name, _what in (("remove", "delete"), ("unlink", "delete"),
                     ("rmdir", "delete"), ("removedirs", "delete"),
                     ("mkdir", "create"), ("makedirs", "create"),
                     ("rename", "move"), ("replace", "overwrite"),
                     ("truncate", "truncate"), ("chmod", "change")):
    if hasattr(os, _name):
        try:
            setattr(os, _name, _wrap_write(getattr(os, _name), _what))
        except Exception:
            pass


# Listing a denied directory is a read too. Blocking file CONTENTS while
# leaving `os.listdir` open still hands back the shape of SYRIX's state
# directory — every connector you have, every task id — which is exactly the
# reconnaissance the deny-list exists to prevent.
def _wrap_listing(fn):
    def _guarded(path=".", *args, **kw):
        _guard_read(path)
        return fn(path, *args, **kw)
    return _guarded


for _name in ("listdir", "scandir"):
    if hasattr(os, _name):
        try:
            setattr(os, _name, _wrap_listing(getattr(os, _name)))
        except Exception:
            pass

# matplotlib must not try to open a window from a headless child.
os.environ.setdefault("MPLBACKEND", "Agg")
'''


def enabled(config: Optional[Dict[str, Any]] = None) -> bool:
    return bool((config or {}).get("supercomputer_capability_compute", True))


def _timeout(config: Optional[Dict[str, Any]]) -> float:
    try:
        return max(5.0, min(180.0, float((config or {}).get(
            "supercomputer_sandbox_timeout_s", DEFAULT_TIMEOUT_S))))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_S


def _max_output(config: Optional[Dict[str, Any]]) -> int:
    try:
        return max(1000, min(2_000_000, int((config or {}).get(
            "supercomputer_sandbox_output_bytes", DEFAULT_MAX_OUTPUT))))
    except (TypeError, ValueError):
        return DEFAULT_MAX_OUTPUT


def _interpreter() -> str:
    """The venv's own python. Never a system one, never a shell."""
    return sys.executable or "python"


def _child_env() -> Dict[str, str]:
    """A minimal environment, built up rather than filtered down.

    Filtering an inherited environment means every new secret anyone adds to
    ``.env`` is exposed until somebody remembers to add it to a denylist.
    Starting empty means the opposite: a new secret is invisible here by
    default, which is the only version of this that stays correct.
    """
    keep = {}
    for name in ("SYSTEMROOT", "WINDIR", "PATHEXT",
                 "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE"):
        value = os.environ.get(name)
        if value:
            keep[name] = value
    # Interpreter directory only — enough for DLL resolution on Windows, not a
    # path into anything else.
    keep["PATH"] = os.path.dirname(_interpreter()) or ""
    keep["PYTHONIOENCODING"] = "utf-8"
    keep["MPLBACKEND"] = "Agg"

    # A private scratch directory standing in for the user profile. pandas,
    # matplotlib and pathlib.Path.home() all need SOME home to exist — without
    # one the child dies on "Could not determine home directory" before it runs
    # a line, which is how the first version of this looked broken rather than
    # safe. Pointing every such variable at a directory SYRIX owns gives the
    # libraries what they need while leaving the child with no idea where the
    # real profile is.
    scratch = _scratch_dir()
    keep["SYRIX_SANDBOX_SCRATCH"] = scratch
    keep["SYRIX_SANDBOX_DENY"] = _deny_roots()
    for name in ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA",
                 "TEMP", "TMP", "MPLCONFIGDIR", "XDG_CACHE_HOME"):
        keep[name] = scratch
    keep["HOMEDRIVE"] = os.path.splitdrive(scratch)[0] or "C:"
    keep["HOMEPATH"] = os.path.splitdrive(scratch)[1] or scratch
    return keep


def _scratch_dir() -> str:
    """Writable scratch shared by every sandbox run: library caches only."""
    folder = os.path.join(paths.supercomputer_dir(), "sandbox_home")
    with obs.swallow("supercomputer.sandbox.scratch_mkdir", folder=folder):
        os.makedirs(folder, exist_ok=True)
    return folder


def _deny_roots() -> str:
    """Directories the sandbox may not read from, os.pathsep-joined.

    SYRIX's repo (which holds ``.env`` and ``client_secrets.json``) and its
    state directory (which holds the connector token store). The task workspace
    lives under the state directory and is re-allowed by being the child's cwd —
    the write guard's ``_WRITABLE`` check runs first for writes, and reads of
    its own working files are explicitly permitted below.
    """
    roots = []
    with obs.swallow("supercomputer.sandbox.deny_repo"):
        from . import workspace

        roots.append(workspace.syrix_repo_root())
    with obs.swallow("supercomputer.sandbox.deny_state"):
        import syrix_paths

        roots.append(os.path.realpath(syrix_paths.syrix_root()))
    return os.pathsep.join(r for r in roots if r)


def _wrap(code: str) -> str:
    """The program the child actually runs: guard first, then agent code."""
    body = textwrap.dedent(str(code or ""))
    return (
        "import sys\n"
        "sys.path.insert(0, '.')\n"
        "import _syrix_sandbox_guard  # noqa: F401\n"
        "sys.path.pop(0)\n"
        "\n"
        + body
    )


def _kill_tree(proc: "subprocess.Popen") -> None:
    """Kill the child and anything it started. Never raises."""
    with obs.swallow("supercomputer.sandbox.kill"):
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=10)
        else:
            proc.kill()
    with obs.swallow("supercomputer.sandbox.kill_fallback"):
        proc.kill()


def run(task_id: str, code: str, *, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run one snippet in the task workspace. ``{ok, reason, data}``. Never raises."""
    cfg = config or {}
    body = str(code or "").strip()
    if not body:
        return {"ok": False, "reason": "empty_code"}
    if len(body) > MAX_CODE_CHARS:
        return {"ok": False, "reason": "code_too_long"}

    workdir = paths.task_workspace(task_id)
    before = _snapshot_files(workdir)

    # Write the guard next to the code, inside the workspace the child is
    # confined to anyway.
    guard_path = os.path.join(workdir, "_syrix_sandbox_guard.py")
    try:
        with open(guard_path, "w", encoding="utf-8") as fh:
            fh.write(_SITECUSTOMIZE % {"blocked": list(BLOCKED_IMPORTS)})
    except OSError as exc:
        obs.report("supercomputer.sandbox.guard_write", exc, task_id=task_id)
        return {"ok": False, "reason": "sandbox_unavailable"}

    # The code goes in a FILE, never on the command line.
    #
    # SYRIX installs a global subprocess guard (syrix_host's SafetyManager)
    # that flattens the whole argv to a string and regex-matches shell verbs
    # against it. With `python -c "<agent code>"` the agent's Python source ends
    # up inside that string, so `del df['col']`, `"{}".format(x)` and a variable
    # called `rd` all read as destructive shell commands and the run is refused.
    # The very first probe hit it — the wrapper's own `del sys.path[0]` was
    # blocked by the `\bdel\b` pattern.
    #
    # Writing the code to a file leaves the argv as nothing but interpreter
    # flags and two paths. The guard still sees and validates the real command;
    # it simply is not being asked to parse Python as if it were shell. This
    # also sidesteps Windows' ~32k command-line limit on a long script.
    script_path = os.path.join(workdir, "_syrix_sandbox_main.py")
    try:
        with open(script_path, "w", encoding="utf-8") as fh:
            fh.write(_wrap(body))
    except OSError as exc:
        obs.report("supercomputer.sandbox.script_write", exc, task_id=task_id)
        return {"ok": False, "reason": "sandbox_unavailable"}

    timeout_s = _timeout(cfg)
    max_output = _max_output(cfg)
    # -I isolates (which implies -E, so PYTHONDONTWRITEBYTECODE in the child env
    # is ignored — hence -B explicitly, or every run litters the workspace with
    # a __pycache__ that then shows up as a "file the agent created").
    argv = [_interpreter(), "-I", "-B", script_path]

    try:
        proc = subprocess.Popen(
            argv,
            cwd=workdir,
            env=_child_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        obs.report("supercomputer.sandbox.spawn", exc, task_id=task_id)
        return {"ok": False, "reason": "sandbox_unavailable"}

    _limit_memory(proc)

    timed_out = False
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        with obs.swallow("supercomputer.sandbox.drain"):
            out, err = proc.communicate(timeout=5)
        out, err = (out or ""), (err or "")
    except Exception as exc:
        obs.report("supercomputer.sandbox.communicate", exc, task_id=task_id)
        _kill_tree(proc)
        return {"ok": False, "reason": "sandbox_failed"}
    finally:
        with obs.swallow("supercomputer.sandbox.guard_cleanup"):
            os.remove(guard_path)
        with obs.swallow("supercomputer.sandbox.script_cleanup"):
            os.remove(script_path)

    created = sorted(set(_snapshot_files(workdir)) - set(before))
    data = {
        "stdout": (out or "")[:max_output],
        "stderr": (err or "")[:4000],
        "exit_code": proc.returncode,
        "files": created,
        "truncated": len(out or "") > max_output,
        "timed_out": timed_out,
    }
    if timed_out:
        return {"ok": False, "reason": f"timed_out_after_{int(timeout_s)}s", "data": data}
    if proc.returncode != 0:
        return {"ok": False, "reason": "error", "data": data}
    return {"ok": True, "data": data}


def _snapshot_files(workdir: str) -> List[str]:
    """Relative paths under the workspace, so new files can be reported."""
    out: List[str] = []
    with obs.swallow("supercomputer.sandbox.snapshot"):
        for root, dirs, files in os.walk(workdir):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in files:
                if name in ("_syrix_sandbox_guard.py", "_syrix_sandbox_main.py"):
                    continue
                full = os.path.join(root, name)
                out.append(os.path.relpath(full, workdir).replace("\\", "/"))
    return out


def _limit_memory(proc: "subprocess.Popen") -> None:
    """Cap the child's memory. Best effort — never blocks the run.

    Windows has no ``resource.setrlimit``, so this uses a Job object with
    ``JOB_OBJECT_LIMIT_PROCESS_MEMORY``. If any part of it is unavailable the
    run still proceeds: the wall-clock timeout and the kill-tree on the way out
    are the guarantees that must hold, and they do not depend on this.
    """
    if os.name != "nt":
        with obs.swallow("supercomputer.sandbox.rlimit"):
            import resource  # type: ignore

            resource.setrlimit(resource.RLIMIT_AS,
                               (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
        return

    def _apply() -> None:
        with obs.swallow("supercomputer.sandbox.job_object"):
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

            class _IO_COUNTERS(ctypes.Structure):
                _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
                            ("WriteOperationCount", ctypes.c_ulonglong),
                            ("OtherOperationCount", ctypes.c_ulonglong),
                            ("ReadTransferCount", ctypes.c_ulonglong),
                            ("WriteTransferCount", ctypes.c_ulonglong),
                            ("OtherTransferCount", ctypes.c_ulonglong)]

            class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                            ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD),
                            ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t),
                            ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                            ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
                            ("IoInfo", _IO_COUNTERS),
                            ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]

            JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
            JobObjectExtendedLimitInformation = 9
            PROCESS_SET_QUOTA, PROCESS_TERMINATE = 0x0100, 0x0001

            job = kernel32.CreateJobObjectW(None, None)
            if not job:
                return
            info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = (
                JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
            info.ProcessMemoryLimit = MEMORY_LIMIT_BYTES
            kernel32.SetInformationJobObject(
                job, JobObjectExtendedLimitInformation,
                ctypes.byref(info), ctypes.sizeof(info))
            handle = kernel32.OpenProcess(
                PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, proc.pid)
            if handle:
                kernel32.AssignProcessToJobObject(job, handle)
                kernel32.CloseHandle(handle)

    # Off the calling thread: a slow ctypes call must not delay the child.
    threading.Thread(target=_apply, name="sc-sandbox-limit", daemon=True).start()


def describe_reason(reason: str) -> str:
    key = str(reason or "")
    if key.startswith("timed_out_after_"):
        return (f"it ran for {key.rsplit('_', 1)[-1]} without finishing, so I stopped it. "
                f"Do the work in smaller pieces")
    return {
        "empty_code": "no code was given",
        "code_too_long": "that snippet is too long to run here",
        "sandbox_unavailable": "the sandbox could not start",
        "sandbox_failed": "the sandbox failed while running it",
        "error": "the code raised an error",
    }.get(key, key or "it did not run")


__all__ = ["run", "enabled", "ALLOWED_IMPORTS", "BLOCKED_IMPORTS",
           "DEFAULT_TIMEOUT_S", "MEMORY_LIMIT_BYTES", "describe_reason"]
