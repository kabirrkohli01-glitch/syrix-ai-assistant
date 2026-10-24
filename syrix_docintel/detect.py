"""File type detection — magic bytes first, extension as a tiebreaker.

Never trust the extension alone (the spec's rule): a `.txt` can be a renamed
ZIP, a `.png` can be a JPEG. We sniff the leading bytes, then fall back to the
extension for ambiguous text/code files that have no signature.

Returns a :class:`DetectResult` with a canonical ``kind`` (image/pdf/
spreadsheet/presentation/document/code/archive/text/unknown), a finer
``subtype`` (docx/xlsx/pptx/csv/png/py/...), and a best-guess ``mime``.

Pure stdlib.
"""
from __future__ import annotations

import io
import os
import zipfile
from dataclasses import dataclass

from . import types as T


@dataclass
class DetectResult:
    kind: str
    subtype: str
    mime: str
    ext: str


# Extensions we treat as source code (kind=code). Value = language label.
CODE_EXTS = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".jsx": "javascript", ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    ".xml": "xml", ".html": "html", ".htm": "html", ".css": "css", ".sql": "sql",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".c": "c", ".h": "c", ".hpp": "cpp",
    ".java": "java", ".go": "go", ".rs": "rust", ".rb": "ruby", ".php": "php",
    ".sh": "shell", ".ps1": "powershell", ".toml": "toml", ".ini": "ini",
    ".md": "markdown", ".markdown": "markdown",
}

# Plain-text extensions (kind=text). MD is code-ish but reads as text.
TEXT_EXTS = {".txt", ".text", ".log", ".csv", ".tsv", ".rst"}

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".heic", ".heif", ".bmp", ".tiff", ".tif"}


def _zip_subtype(blob: bytes) -> str:
    """Distinguish OOXML/ODF containers (all start with PK)."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
        names = zf.namelist()
    except Exception:
        return "zip"
    nameset = set(names)
    # OpenDocument stores an unzipped `mimetype` member first.
    if "mimetype" in nameset:
        try:
            mt = zf.read("mimetype").decode("ascii", "ignore")
        except Exception:
            mt = ""
        if "opendocument.text" in mt:
            return "odt"
        if "opendocument.spreadsheet" in mt:
            return "ods"
        if "opendocument.presentation" in mt:
            return "odp"
    # OOXML lays content under word/ , xl/ , ppt/.
    if any(n.startswith("word/") for n in names):
        return "docx"
    if any(n.startswith("xl/") for n in names):
        return "xlsx"
    if any(n.startswith("ppt/") for n in names):
        return "pptx"
    return "zip"


def _sniff(blob: bytes) -> str:
    """Return a coarse signature label from the leading bytes, or ''."""
    b = blob[:16]
    if b.startswith(b"%PDF"):
        return "pdf"
    if b.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if b.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if b.startswith(b"GIF87a") or b.startswith(b"GIF89a"):
        return "gif"
    if b.startswith(b"BM"):
        return "bmp"
    if b[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "webp"
    if b[:2] == b"II" and b[2:4] == b"\x2a\x00":
        return "tiff"
    if b[:2] == b"MM" and b[2:4] == b"\x00\x2a":
        return "tiff"
    # HEIC/HEIF: ISO-BMFF `ftyp` box with a heic/heif/mif1 brand.
    if blob[4:8] == b"ftyp":
        brand = blob[8:12]
        if brand in (b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1", b"heif"):
            return "heic"
    if b.startswith(b"PK\x03\x04") or b.startswith(b"PK\x05\x06") or b.startswith(b"PK\x07\x08"):
        return "pk_zip"
    if b.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "7z"
    if b.startswith(b"Rar!\x1a\x07"):
        return "rar"
    if b.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "ole"    # legacy doc/xls/ppt (OLE compound)
    return ""


_IMG_MIME = {
    "png": "image/png", "jpeg": "image/jpeg", "gif": "image/gif",
    "webp": "image/webp", "heic": "image/heic", "bmp": "image/bmp",
    "tiff": "image/tiff",
}


def detect(filename: str, blob: bytes, declared_mime: str = "") -> DetectResult:
    """Classify a file from its bytes + name. Bytes win over extension."""
    name = str(filename or "").strip()
    ext = os.path.splitext(name)[1].lower()
    sig = _sniff(blob or b"")

    # --- signature-driven (authoritative) ---
    if sig == "pdf":
        return DetectResult(T.KIND_PDF, "pdf", "application/pdf", ext)
    if sig in _IMG_MIME:
        return DetectResult(T.KIND_IMAGE, sig, _IMG_MIME[sig], ext)
    if sig == "7z":
        return DetectResult(T.KIND_ARCHIVE, "7z", "application/x-7z-compressed", ext)
    if sig == "rar":
        return DetectResult(T.KIND_ARCHIVE, "rar", "application/vnd.rar", ext)
    if sig == "pk_zip":
        sub = _zip_subtype(blob)
        if sub == "docx":
            return DetectResult(T.KIND_DOCUMENT, "docx",
                                "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ext)
        if sub == "xlsx":
            return DetectResult(T.KIND_SPREADSHEET, "xlsx",
                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ext)
        if sub == "pptx":
            return DetectResult(T.KIND_PRESENTATION, "pptx",
                                "application/vnd.openxmlformats-officedocument.presentationml.presentation", ext)
        if sub == "odt":
            return DetectResult(T.KIND_DOCUMENT, "odt", "application/vnd.oasis.opendocument.text", ext)
        if sub == "ods":
            return DetectResult(T.KIND_SPREADSHEET, "ods", "application/vnd.oasis.opendocument.spreadsheet", ext)
        if sub == "odp":
            return DetectResult(T.KIND_PRESENTATION, "odp", "application/vnd.oasis.opendocument.presentation", ext)
        return DetectResult(T.KIND_ARCHIVE, "zip", "application/zip", ext)
    if sig == "ole":
        # Legacy Office — disambiguate by extension since the OLE header is shared.
        if ext == ".doc":
            return DetectResult(T.KIND_DOCUMENT, "doc", "application/msword", ext)
        if ext == ".xls":
            return DetectResult(T.KIND_SPREADSHEET, "xls", "application/vnd.ms-excel", ext)
        if ext == ".ppt":
            return DetectResult(T.KIND_PRESENTATION, "ppt", "application/vnd.ms-powerpoint", ext)
        return DetectResult(T.KIND_DOCUMENT, "ole", "application/x-ole-storage", ext)

    # --- extension-driven (text/code have no signature) ---
    if ext == ".rtf" or (blob[:5] == b"{\\rtf"):
        return DetectResult(T.KIND_DOCUMENT, "rtf", "application/rtf", ext or ".rtf")
    if ext == ".csv":
        return DetectResult(T.KIND_SPREADSHEET, "csv", "text/csv", ext)
    if ext in IMAGE_EXTS:
        return DetectResult(T.KIND_IMAGE, ext.lstrip("."), _IMG_MIME.get(ext.lstrip("."), declared_mime or "image/*"), ext)
    if ext in CODE_EXTS:
        # md reads as text; everything else is code.
        kind = T.KIND_TEXT if CODE_EXTS[ext] == "markdown" else T.KIND_CODE
        return DetectResult(kind, CODE_EXTS[ext].lstrip("."), declared_mime or "text/plain", ext)
    if ext in TEXT_EXTS:
        return DetectResult(T.KIND_TEXT, ext.lstrip(".") or "txt", "text/plain", ext)

    # --- last resort: is it printable text? ---
    if _looks_like_text(blob):
        return DetectResult(T.KIND_TEXT, "txt", "text/plain", ext)
    return DetectResult(T.KIND_UNKNOWN, ext.lstrip(".") or "bin", declared_mime or "application/octet-stream", ext)


def _looks_like_text(blob: bytes, sample: int = 4096) -> bool:
    if not blob:
        return False
    chunk = blob[:sample]
    if b"\x00" in chunk:
        return False
    # Try strict UTF-8 first, then a printable-ratio heuristic.
    try:
        chunk.decode("utf-8")
        return True
    except UnicodeDecodeError:
        printable = sum(1 for c in chunk if 9 <= c <= 13 or 32 <= c <= 126 or c >= 160)
        return printable / max(1, len(chunk)) > 0.85
