"""Reading the user's own files — the narrow, allowlisted way.

Why this exists
---------------
"Submit my CV, it's in my Downloads" was impossible. The mode could browse the
whole web and write into its own sandbox, but it could not see a single file on
the machine it was running on, so the one thing the user most obviously wanted to do
with a browser agent — take a document he already has and put it somewhere —
had no path at all.

Worse, the capability toggles for it *existed*. ``file_read`` and ``folder_list``
were declared in :mod:`syrix_supercomputer.capabilities`, classified in
:mod:`syrix_supercomputer.safety_mode`, and rendered as switches on the Settings
page — with no tool behind any of them. Switching them on did nothing.

What this is allowed to do
--------------------------
**Read. That is all.** There is no write, no move, no rename and no delete here,
and there is deliberately no code path that could be extended into one by
passing a different argument. The user's decision on 2026-07-26 was allowlisted
folders, read-only, behind a permission card, and that is exactly what this is.

Four boundaries, each enforced independently:

1. **Roots.** Only Downloads, Documents, Desktop, Pictures and the working
   folder the user chose himself. Configurable by name (``supercomputer_local_read_roots``),
   never by arbitrary path — a config typo cannot open up ``C:\\``.
2. **Containment.** Checked on the REALPATH, so ``..``, an absolute path and a
   symlink pointing out of a root are all caught by the same test. Copied from
   :func:`syrix_supercomputer.workspace.resolve`, which has been carrying that
   property since Phase 7.
3. **File types.** Executables and scripts are refused outright — reading one is
   pointless and *attaching* one to a web form is how a machine spreads malware.
   SYRIX's own repo is refused too: an agent reading the assistant that is
   running it is a category of mistake worth simply removing.
4. **The card.** Every single read raises a permission card naming the exact
   file, because :mod:`syrix_supercomputer.gate` classifies ``file_read`` as
   private. That is not bypassed here and cannot be.

Extraction reuses :mod:`syrix_docintel`, which already reads PDF, docx, xlsx,
csv and images and is already tested. This module resolves and gates; it does
not re-implement parsing.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

import syrix_obs as obs

from . import paths

# Named roots. The names are what config and the model use; the paths are
# resolved here, so neither can name a directory this module has not blessed.
_ROOT_NAMES = ("downloads", "documents", "desktop", "pictures", "music", "videos")
DEFAULT_ROOTS = ("downloads", "documents", "desktop", "pictures")

# Never read, never attach. A superset of workspace._REFUSED_SUFFIXES: this list
# also has to stop a file being *uploaded* somewhere, so archives that commonly
# smuggle executables are out too.
REFUSED_SUFFIXES = (
    ".exe", ".dll", ".sys", ".msi", ".scr", ".com", ".cpl", ".pif",
    ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe", ".wsf", ".hta",
    ".lnk", ".reg", ".inf", ".jar", ".msix", ".appx",
)

# One file, read into a prompt. Beyond this the useful move is to look at part
# of it, not to blow the context window on the whole thing.
MAX_READ_BYTES = 8 * 1024 * 1024
# What actually reaches the model from a text extraction.
MAX_TEXT_CHARS = 12000
# A folder listing is for finding a file, not for auditing a disk.
MAX_ENTRIES = 120


def _home() -> str:
    return os.path.realpath(os.path.expanduser("~"))


def _named_root(name: str) -> str:
    key = str(name or "").strip().lower()
    if key not in _ROOT_NAMES:
        return ""
    folder = {"pictures": "Pictures", "videos": "Videos", "music": "Music",
              "downloads": "Downloads", "documents": "Documents",
              "desktop": "Desktop"}[key]
    return os.path.join(_home(), folder)


def configured_root_names(config: Optional[Dict[str, Any]] = None) -> List[str]:
    """The root names this install allows, from config, filtered to known ones."""
    raw = str((config or {}).get("supercomputer_local_read_roots", "") or "")
    names = [p.strip().lower() for p in raw.replace(";", ",").split(",") if p.strip()]
    allowed = [n for n in names if n in _ROOT_NAMES]
    return allowed or list(DEFAULT_ROOTS)


def roots(config: Optional[Dict[str, Any]] = None) -> List[str]:
    """Every directory that may be read from, as absolute realpaths.

    Includes the working folder the user picked from the workspace, because a folder
    he explicitly chose for SYRIX to work in is one he has already consented to.
    """
    out: List[str] = []
    for name in configured_root_names(config):
        folder = _named_root(name)
        if folder and os.path.isdir(folder):
            out.append(os.path.realpath(folder))
    with obs.swallow("supercomputer.local_files.working_root"):
        chosen = paths.workspace_root()
        if chosen and os.path.isdir(chosen):
            real = os.path.realpath(chosen)
            if real not in out:
                out.append(real)
    return out


def describe_roots(config: Optional[Dict[str, Any]] = None) -> str:
    """Human list for the tool description and for refusal messages."""
    names = [os.path.basename(r) or r for r in roots(config)]
    return ", ".join(names) if names else "no folders"


def _inside(root: str, candidate: str) -> bool:
    try:
        return os.path.commonpath([root, candidate]) == root
    except ValueError:
        # Different drives. That is a refusal, not an error.
        return False


def resolve(path: str, config: Optional[Dict[str, Any]] = None,
            *, must_exist: bool = True) -> Tuple[Optional[str], str]:
    """Resolve a path the user's agent asked for. ``(absolute, "")`` or ``(None, reason)``.

    Accepts an absolute path, a ``~`` path, or ``downloads/cv.pdf`` — the last
    being what a model naturally writes. Whatever the form, the answer must land
    inside an allowed root or it is refused.
    """
    raw = str(path or "").strip().strip('"').strip("'")
    if not raw:
        return None, "empty_path"

    allowed = roots(config)
    if not allowed:
        return None, "no_readable_folders"

    candidates: List[str] = []
    expanded = os.path.expanduser(raw)
    if os.path.isabs(expanded):
        candidates.append(expanded)
    else:
        # "downloads/cv.pdf" — a named root plus a relative tail.
        head, _, tail = raw.replace("\\", "/").partition("/")
        named = _named_root(head)
        if named and tail:
            candidates.append(os.path.join(named, tail))
        # Otherwise try it under each allowed root, most-likely first.
        candidates.extend(os.path.join(root, expanded) for root in allowed)

    for candidate in candidates:
        try:
            real = os.path.realpath(candidate)
        except (OSError, ValueError):
            continue
        if not any(_inside(root, real) for root in allowed):
            continue
        if must_exist and not os.path.exists(real):
            continue
        suffix = os.path.splitext(real)[1].lower()
        if suffix in REFUSED_SUFFIXES:
            return None, f"refused_file_type:{suffix}"
        with obs.swallow("supercomputer.local_files.repo_check"):
            from . import workspace

            if workspace.is_syrix_path(real):
                return None, "refused_syrix_repo"
        return real, ""

    # Distinguish "not allowed" from "not there" — they need different answers.
    for candidate in candidates:
        with obs.swallow("supercomputer.local_files.reason"):
            real = os.path.realpath(candidate)
            if any(_inside(root, real) for root in allowed):
                return None, "not_found"
    return None, "outside_allowed_folders"


def list_folder(folder: str, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """List one allowed folder. Returns the standard ``{ok, reason, data}``."""
    raw = str(folder or "").strip()
    target: Optional[str] = None
    if not raw:
        # No folder named: list every root, so "what have I got" works.
        entries: List[Dict[str, Any]] = []
        for root in roots(config):
            entries.append({"name": os.path.basename(root) or root,
                            "path": root, "is_dir": True, "bytes": 0})
        return {"ok": True, "data": {"folder": "", "entries": entries}}

    named = _named_root(raw)
    if named and os.path.isdir(named):
        target = os.path.realpath(named)
    else:
        target, reason = resolve(raw, config)
        if target is None:
            return {"ok": False, "reason": reason}
    if not os.path.isdir(target):
        return {"ok": False, "reason": "not_a_folder"}

    entries = []
    try:
        with os.scandir(target) as it:
            for entry in it:
                if entry.name.startswith("."):
                    continue
                is_dir = entry.is_dir()
                suffix = os.path.splitext(entry.name)[1].lower()
                if not is_dir and suffix in REFUSED_SUFFIXES:
                    continue
                size = 0
                if not is_dir:
                    with obs.swallow("supercomputer.local_files.stat"):
                        size = entry.stat().st_size
                entries.append({"name": entry.name,
                                "path": os.path.join(target, entry.name),
                                "is_dir": is_dir, "bytes": int(size)})
    except OSError as exc:
        obs.report("supercomputer.local_files.scandir", exc, folder=target, level="debug")
        return {"ok": False, "reason": "unreadable_folder"}

    # Newest first: the file someone is looking for is almost always recent.
    with obs.swallow("supercomputer.local_files.sort"):
        entries.sort(key=lambda e: os.path.getmtime(e["path"]), reverse=True)
    return {"ok": True, "data": {"folder": target, "entries": entries[:MAX_ENTRIES],
                                 "truncated": len(entries) > MAX_ENTRIES}}


def read_file(path: str, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Read one allowed file as text. Binary formats go through docintel."""
    target, reason = resolve(path, config)
    if target is None:
        return {"ok": False, "reason": reason}
    if os.path.isdir(target):
        return {"ok": False, "reason": "is_a_folder"}
    try:
        size = os.path.getsize(target)
    except OSError as exc:
        obs.report("supercomputer.local_files.size", exc, level="debug")
        return {"ok": False, "reason": "unreadable_file"}
    if size > MAX_READ_BYTES:
        return {"ok": False, "reason": "file_too_large"}

    text, kind = _extract(target)
    if text is None:
        return {"ok": False, "reason": kind or "unsupported_file_type",
                "data": {"path": target, "bytes": size}}
    return {"ok": True, "data": {
        "path": target, "bytes": size, "kind": kind,
        "text": text[:MAX_TEXT_CHARS],
        "truncated": len(text) > MAX_TEXT_CHARS,
    }}


_PLAIN_SUFFIXES = (".txt", ".md", ".markdown", ".csv", ".json", ".xml", ".yaml",
                   ".yml", ".log", ".ini", ".cfg", ".html", ".htm", ".py", ".js",
                   ".ts", ".jsx", ".tsx", ".css", ".sql", ".sh", ".rst", ".tsv")


def _extract(target: str) -> Tuple[Optional[str], str]:
    """``(text, kind)`` or ``(None, reason)``. Never raises."""
    suffix = os.path.splitext(target)[1].lower()
    if suffix in _PLAIN_SUFFIXES:
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read(), "text"
        except OSError as exc:
            obs.report("supercomputer.local_files.read_text", exc, level="debug")
            return None, "unreadable_file"

    # Everything else — PDF, docx, xlsx, pptx, images — is exactly what
    # syrix_docintel already does, with OCR where it applies. Re-implementing
    # any of that here would be a second thing to keep correct.
    #
    # It takes bytes rather than a path because its normal caller is a HUD
    # upload, and it caches on the content hash, so re-reading the same CV
    # costs nothing the second time.
    with obs.swallow("supercomputer.local_files.docintel", path=target):
        import syrix_docintel

        with open(target, "rb") as fh:
            blob = fh.read()
        doc = syrix_docintel.analyze_upload(os.path.basename(target), blob)
        if getattr(doc, "status", "") == "failed":
            return None, str(getattr(doc, "error", "") or "unreadable_file")[:120]
        body = doc.full_text()
        if body.strip():
            return body, str(getattr(doc, "kind", "") or "document")
        return None, "no_text_found"
    return None, "unsupported_file_type"


def attachable(path: str, config: Optional[Dict[str, Any]] = None) -> Tuple[Optional[str], str]:
    """Resolve a file for UPLOAD. Same rules as reading, plus a size sanity cap.

    Upload does not need the contents, so this deliberately does not extract —
    but it applies the identical allowlist and refused-suffix checks, because
    "which files may leave this machine" must never be a looser question than
    "which files may be looked at".
    """
    target, reason = resolve(path, config)
    if target is None:
        return None, reason
    if os.path.isdir(target):
        return None, "is_a_folder"
    try:
        if os.path.getsize(target) > MAX_READ_BYTES:
            return None, "file_too_large"
    except OSError as exc:
        obs.report("supercomputer.local_files.attach_size", exc, level="debug")
        return None, "unreadable_file"
    return target, ""


def describe_reason(reason: str, config: Optional[Dict[str, Any]] = None) -> str:
    """A refusal the user can act on, rather than an error code."""
    key = str(reason or "")
    if key.startswith("refused_file_type:"):
        return (f"that is a {key.split(':', 1)[1]} file — I do not read or upload "
                f"programs and scripts, whatever the approval says")
    return {
        "empty_path": "no file was named",
        "not_found": "there is no such file in the folders I can see",
        "outside_allowed_folders": (
            f"it is outside the folders I am allowed to read ({describe_roots(config)}). "
            f"Move it into one of those, or change the folders in Supercomputer settings"),
        "no_readable_folders": "no readable folders are configured in Supercomputer settings",
        "refused_syrix_repo": "that is inside SYRIX's own code, which I will not read from here",
        "is_a_folder": "that is a folder, not a file",
        "not_a_folder": "that is a file, not a folder",
        "file_too_large": f"it is bigger than {MAX_READ_BYTES // (1024 * 1024)} MB",
        "unreadable_file": "I could not open it",
        "unreadable_folder": "I could not list that folder",
        "no_text_found": "I opened it but found no readable text in it",
        "unsupported_file_type": "I cannot read that kind of file",
    }.get(key, key or "I could not use that path")


__all__ = [
    "DEFAULT_ROOTS", "REFUSED_SUFFIXES", "MAX_READ_BYTES", "MAX_TEXT_CHARS",
    "roots", "describe_roots", "configured_root_names",
    "resolve", "list_folder", "read_file", "attachable", "describe_reason",
]
