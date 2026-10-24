"""Isolated per-task coding workspaces.

Every task that writes files gets its own directory under
``%LOCALAPPDATA%\\SYRIX\\supercomputer\\workspaces\\<task_id>\\``. Generated code
lives there and nowhere else.

The containment rule is enforced by path resolution, not by convention:
:func:`resolve` realpaths the candidate and refuses anything that does not land
inside the workspace root. That closes ``..`` traversal, absolute paths, and
symlinks that point outside — the three ways "write a file" becomes "write any
file".

SYRIX's own repo is refused outright unless the user has both enabled the
``work_on_syrix`` capability and approved that exact path, because the most
expensive possible failure here is an agent editing the assistant that is
running it.
"""
from __future__ import annotations

import os
import shutil
import threading
from typing import Any, Dict, List, Optional, Tuple

import syrix_obs as obs

from . import paths

# Files we will not create regardless of anything else: executables, scripts
# that run on double-click, and anything that could persist beyond the sandbox.
_REFUSED_SUFFIXES = (
    ".exe", ".dll", ".sys", ".msi", ".scr", ".com", ".cpl",
    ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe", ".js.lnk", ".lnk",
    ".reg", ".inf",
)
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_FILES = 400

_LOCK = threading.RLock()


def root_for(task_id: str) -> str:
    """The workspace root for a task, created on demand."""
    return paths.task_workspace(task_id)


def syrix_repo_root() -> str:
    return os.path.realpath(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def resolve(task_id: str, relative: str) -> Tuple[Optional[str], str]:
    """Resolve ``relative`` inside the task workspace.

    Returns ``(absolute_path, "")`` or ``(None, reason)``. Containment is
    checked on the REALPATH, so ``..`` traversal, an absolute path, and a
    symlink escape are all caught by the same test.
    """
    rel = str(relative or "").strip().replace("\\", "/")
    if not rel:
        return None, "empty_path"
    if rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
        return None, "absolute_path_refused"

    root = os.path.realpath(root_for(task_id))
    candidate = os.path.realpath(os.path.join(root, rel))

    # os.path.commonpath raises on different drives, which is itself a refusal.
    try:
        inside = os.path.commonpath([root, candidate]) == root
    except ValueError:
        return None, "outside_workspace"
    if not inside:
        return None, "outside_workspace"

    suffix = os.path.splitext(candidate)[1].lower()
    if suffix in _REFUSED_SUFFIXES:
        return None, f"refused_file_type:{suffix}"
    return candidate, ""


def is_syrix_path(path: str) -> bool:
    """True when a path is inside SYRIX's own repo."""
    try:
        target = os.path.realpath(str(path or ""))
        repo = syrix_repo_root()
        return os.path.commonpath([repo, target]) == repo
    except (ValueError, OSError):
        return False


def write(task_id: str, relative: str, content: str) -> Dict[str, Any]:
    """Create or replace a file inside the task workspace."""
    target, reason = resolve(task_id, relative)
    if target is None:
        return {"ok": False, "reason": reason}
    body = str(content or "")
    if len(body.encode("utf-8", errors="replace")) > MAX_FILE_BYTES:
        return {"ok": False, "reason": "file_too_large"}

    with _LOCK:
        if len(listing(task_id)) >= MAX_FILES and not os.path.exists(target):
            return {"ok": False, "reason": "too_many_files"}
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            existed = os.path.isfile(target)
            with open(target, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(body)
        except OSError as exc:
            obs.report("supercomputer.workspace.write", exc, path=target)
            return {"ok": False, "reason": f"write_failed:{type(exc).__name__}"}

    return {
        "ok": True,
        "data": {
            "path": target,
            "relative": relative,
            "bytes": len(body.encode("utf-8", errors="replace")),
            "lines": body.count("\n") + (1 if body and not body.endswith("\n") else 0),
            "replaced": existed,
        },
    }


def write_bytes(task_id: str, relative: str, blob: bytes) -> Dict[str, Any]:
    """Create or replace a binary file inside the task workspace.

    Same containment and refused-suffix rules as :func:`write` — this exists
    because a PDF, a spreadsheet and a .docx are not text, not because binary
    files get a looser path check.
    """
    target, reason = resolve(task_id, relative)
    if target is None:
        return {"ok": False, "reason": reason}
    body = bytes(blob or b"")
    if len(body) > MAX_FILE_BYTES:
        return {"ok": False, "reason": "file_too_large"}

    with _LOCK:
        if len(listing(task_id)) >= MAX_FILES and not os.path.exists(target):
            return {"ok": False, "reason": "too_many_files"}
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            existed = os.path.isfile(target)
            with open(target, "wb") as fh:
                fh.write(body)
        except OSError as exc:
            obs.report("supercomputer.workspace.write_bytes", exc, path=target)
            return {"ok": False, "reason": f"write_failed:{type(exc).__name__}"}

    return {
        "ok": True,
        "data": {"path": target, "relative": relative, "bytes": len(body),
                 "lines": 0, "replaced": existed},
    }


def read(task_id: str, relative: str) -> Dict[str, Any]:
    target, reason = resolve(task_id, relative)
    if target is None:
        return {"ok": False, "reason": reason}
    if not os.path.isfile(target):
        return {"ok": False, "reason": "not_found"}
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as fh:
            return {"ok": True, "data": {"path": target, "content": fh.read(MAX_FILE_BYTES)}}
    except OSError as exc:
        obs.report("supercomputer.workspace.read", exc, path=target)
        return {"ok": False, "reason": f"read_failed:{type(exc).__name__}"}


def listing(task_id: str) -> List[Dict[str, Any]]:
    """Every file in the workspace, relative to its root."""
    root = root_for(task_id)
    out: List[Dict[str, Any]] = []
    for base, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(base, name)
            try:
                out.append({
                    "relative": os.path.relpath(full, root).replace("\\", "/"),
                    "bytes": os.path.getsize(full),
                })
            except OSError as exc:
                obs.report("supercomputer.workspace.stat", exc, path=full, level="debug")
    return sorted(out, key=lambda f: f["relative"])


def clear(task_id: str) -> int:
    """Delete a task's workspace. Only ever called on explicit instruction —
    exported work is the user's, and chat cleanup does not touch it."""
    root = root_for(task_id)
    n = len(listing(task_id))
    try:
        shutil.rmtree(root, ignore_errors=True)
    except OSError as exc:
        obs.report("supercomputer.workspace.clear", exc, path=root)
    return n


def snapshot(task_id: str) -> Dict[str, Any]:
    files = listing(task_id)
    return {
        "root": root_for(task_id),
        "files": files,
        "count": len(files),
        "bytes": sum(f["bytes"] for f in files),
    }


__all__ = [
    "root_for", "resolve", "is_syrix_path", "syrix_repo_root",
    "write", "write_bytes", "read", "listing", "clear", "snapshot",
    "MAX_FILE_BYTES", "MAX_FILES",
]
