"""Saving what SYRIX made out of the sandbox and onto the machine.

Everything the agent produces lands in the task workspace first — that is what
makes it safe to produce freely. This module is the one door out of it, and it
is deliberately narrow:

* the source must resolve **inside the task workspace**, through the same
  realpath containment that governs writing there, so "export" can never be
  turned into "copy any file on this machine";
* the destination must be one of exactly two places — the user's Downloads folder,
  or the working folder he chose himself. Never a path the agent picked;
* executables are refused, whatever was approved. Approving "save that PDF" is
  not approval to drop an installer in Downloads;
* an existing file is never overwritten. A name collision gets " (2)", because
  silently replacing something the user already had is not a save, it is a loss.

Permission is not this module's job — :mod:`syrix_supercomputer.gate` raises the
card under the ``export`` action before anything here is called.
"""
from __future__ import annotations

import os
import re
import shutil
from typing import Any, Dict, Optional, Tuple

import syrix_obs as obs

from . import paths, workspace

# Refused whatever was approved. Same list the download path uses — the reason
# is identical, and two lists that could drift is one list too many.
UNSAFE_SUFFIXES = frozenset({
    ".exe", ".msi", ".bat", ".cmd", ".com", ".scr", ".cpl", ".ps1", ".psm1",
    ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta", ".dll", ".sys",
    ".reg", ".inf", ".lnk", ".jar", ".apk", ".app", ".dmg", ".pkg", ".deb", ".rpm",
})

DEST_DOWNLOADS = "downloads"
DEST_WORKING = "working_folder"


def downloads_dir() -> str:
    """The user's Downloads folder."""
    return os.path.join(os.path.expanduser("~"), "Downloads")


def destinations() -> Dict[str, str]:
    """The only two places an export may land, resolved now."""
    return {DEST_DOWNLOADS: downloads_dir(), DEST_WORKING: paths.workspace_root()}


def resolve_destination(name: str) -> Tuple[Optional[str], str]:
    """``(folder, "")`` for a known destination, else ``(None, reason)``.

    An unknown name is refused rather than defaulted: defaulting would mean a
    typo silently sends a file somewhere the user did not choose.
    """
    key = str(name or DEST_DOWNLOADS).strip().lower()
    folders = destinations()
    if key not in folders:
        return None, f"unknown_destination:{key}"
    folder = folders[key]
    if not os.path.isdir(folder):
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as exc:
            obs.report("supercomputer.export.mkdir", exc, folder=folder)
            return None, "destination_unavailable"
    if not os.access(folder, os.W_OK):
        return None, "destination_not_writable"
    return folder, ""


def _unique(folder: str, filename: str) -> str:
    """A path that does not already exist. Never overwrites."""
    stem, ext = os.path.splitext(filename)
    candidate = os.path.join(folder, filename)
    n = 2
    while os.path.exists(candidate) and n < 200:
        candidate = os.path.join(folder, f"{stem} ({n}){ext}")
        n += 1
    return candidate


def save(task_id: str, relative: str, *, to: str = DEST_DOWNLOADS) -> Dict[str, Any]:
    """Copy one file out of the task workspace. ``{ok, reason, data}``."""
    source, reason = workspace.resolve(task_id, relative)
    if source is None:
        return {"ok": False, "reason": reason}
    if not os.path.isfile(source):
        return {"ok": False, "reason": "not_found"}

    name = os.path.basename(source)
    if os.path.splitext(name)[1].lower() in UNSAFE_SUFFIXES:
        return {"ok": False, "reason": "refused_executable"}

    folder, why = resolve_destination(to)
    if folder is None:
        return {"ok": False, "reason": why}

    target = _unique(folder, name)
    try:
        shutil.copy2(source, target)
    except OSError as exc:
        obs.report("supercomputer.export.copy", exc, source=source, target=target)
        return {"ok": False, "reason": f"copy_failed:{type(exc).__name__}"}

    return {"ok": True, "data": {
        "path": target, "name": os.path.basename(target),
        "folder": folder, "bytes": os.path.getsize(target),
    }}


__all__ = ["save", "destinations", "resolve_destination", "downloads_dir",
           "DEST_DOWNLOADS", "DEST_WORKING", "UNSAFE_SUFFIXES"]
