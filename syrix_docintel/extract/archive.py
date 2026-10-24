"""Archive inspection — ZIP (stdlib), with 7z/RAR as fast-follow.

Safely enumerates the file tree, rejects dangerous payloads (executables,
path-traversal) and zip bombs, summarizes the project shape (file counts,
detected languages), and previews a bounded number of text/code members so
"review this codebase / what's the structure" has real content to reason over.
Never extracts to disk; everything stays in memory.
"""
from __future__ import annotations

import io
import os
import zipfile
from collections import Counter
from typing import Dict, List, Tuple

from .. import types as T
from ..chunk import add_page, finalize
from ..detect import CODE_EXTS, TEXT_EXTS
from ..safety import Deadline, ProcessingError, is_dangerous_member, check_zip_bomb, load_limits
from ..types import DocumentObject

_PREVIEW_MEMBERS = 40         # how many text/code files to preview as chunks
_PER_FILE_CHARS = 4000
_TOTAL_TEXT_CAP = 300_000
_MAX_MEMBER_BYTES = 512 * 1024   # skip previewing very large members


def extract(blob: bytes, doc: DocumentObject, deadline: Deadline) -> None:
    if doc.subtype == "zip":
        _extract_zip(blob, doc, deadline)
    elif doc.subtype == "7z":
        _extract_7z(blob, doc, deadline)
    elif doc.subtype == "rar":
        _extract_rar(blob, doc, deadline)
    else:
        _extract_zip(blob, doc, deadline)
    finalize(doc)


def _summarize_tree(names: List[str]) -> Tuple[Dict, str]:
    ext_counts: Counter = Counter()
    langs: Counter = Counter()
    for n in names:
        ext = os.path.splitext(n)[1].lower()
        if ext:
            ext_counts[ext] += 1
        if ext in CODE_EXTS:
            langs[CODE_EXTS[ext]] += 1
    meta = {
        "entry_count": len(names),
        "extensions": dict(ext_counts.most_common(20)),
        "languages": dict(langs.most_common(10)),
    }
    top_langs = ", ".join(f"{k} ({v})" for k, v in langs.most_common(6)) or "n/a"
    tree_preview = "\n".join(names[:200])
    summary = (
        f"Archive with {len(names)} entries. Detected languages: {top_langs}.\n"
        f"File tree (first 200):\n{tree_preview}"
    )
    return meta, summary


def _extract_zip(blob: bytes, doc: DocumentObject, deadline: Deadline) -> None:
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except Exception as exc:
        raise ProcessingError(f"Could not open ZIP archive ({exc}).")

    limits = load_limits()
    infos = zf.infolist()
    if len(infos) > limits.max_archive_entries:
        doc.status = T.STATUS_PARTIAL
        doc.add_note(f"Archive has {len(infos)} entries; only the first {limits.max_archive_entries} were inspected.")
        infos = infos[:limits.max_archive_entries]

    check_zip_bomb(infos, limits)   # raises ProcessingError on a bomb

    names = [zi.filename for zi in infos if not zi.is_dir()]
    doc.extraction_method = "zipfile"

    dangerous = [n for n in names if is_dangerous_member(n)]
    nested = [n for n in names if os.path.splitext(n)[1].lower() in (".zip", ".7z", ".rar", ".tar", ".gz")]
    meta, summary = _summarize_tree(names)
    if dangerous:
        meta["blocked_executables"] = dangerous[:30]
        doc.add_note(f"Skipped {len(dangerous)} executable/unsafe file(s) inside the archive.")
    if nested:
        meta["nested_archives"] = nested[:30]
        doc.add_note("Nested archives detected — not expanded (depth guard).")
    doc.metadata.update(meta)
    add_page(doc, 0, "Archive overview", summary, kind="text")

    # Preview a bounded set of text/code members.
    previewed = 0
    total_text = 0
    for zi in infos:
        if previewed >= _PREVIEW_MEMBERS or total_text >= _TOTAL_TEXT_CAP:
            break
        if zi.is_dir():
            continue
        name = zi.filename
        ext = os.path.splitext(name)[1].lower()
        if is_dangerous_member(name):
            continue
        if ext not in CODE_EXTS and ext not in TEXT_EXTS:
            continue
        if int(getattr(zi, "file_size", 0) or 0) > _MAX_MEMBER_BYTES:
            continue
        try:
            deadline.check("archive preview")
        except ProcessingError:
            doc.add_note("Time budget reached during archive preview.")
            break
        try:
            data = zf.read(zi)
            text = data.decode("utf-8", errors="replace")[:_PER_FILE_CHARS]
        except Exception:
            continue
        if text.strip():
            add_page(doc, 0, name, f"### {name}\n{text}", kind="code")
            previewed += 1
            total_text += len(text)

    doc.metadata["previewed_files"] = previewed
    doc.confidence = 1.0


def _extract_7z(blob: bytes, doc: DocumentObject, deadline: Deadline) -> None:
    try:
        import py7zr  # type: ignore
    except Exception as exc:
        raise ProcessingError(f"7z support unavailable (install py7zr) — {exc}.")
    try:
        with py7zr.SevenZipFile(io.BytesIO(blob), mode="r") as z:
            names = z.getnames()
    except Exception as exc:
        raise ProcessingError(f"Could not open 7z archive ({exc}).")
    doc.extraction_method = "py7zr"
    meta, summary = _summarize_tree(names)
    doc.metadata.update(meta)
    add_page(doc, 0, "Archive overview", summary, kind="text")
    doc.add_note("7z content preview is limited in this version (tree only).")
    doc.confidence = 1.0


def _extract_rar(blob: bytes, doc: DocumentObject, deadline: Deadline) -> None:
    try:
        import rarfile  # type: ignore
    except Exception as exc:
        raise ProcessingError(f"RAR support unavailable (install rarfile + an unrar binary) — {exc}.")
    try:
        rf = rarfile.RarFile(io.BytesIO(blob))
        names = rf.namelist()
    except Exception as exc:
        raise ProcessingError(f"Could not open RAR archive ({exc}).")
    doc.extraction_method = "rarfile"
    meta, summary = _summarize_tree(names)
    doc.metadata.update(meta)
    add_page(doc, 0, "Archive overview", summary, kind="text")
    doc.add_note("RAR content preview is limited in this version (tree only).")
    doc.confidence = 1.0
