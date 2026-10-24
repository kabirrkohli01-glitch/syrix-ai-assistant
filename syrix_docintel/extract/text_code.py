"""Plain-text, markdown, and source-code extraction.

Decodes with encoding detection, chunks the text, and — for code — pulls a
lightweight structural summary (imports, function/class names) into
``metadata`` so the brain can reason about a file's shape without executing it.
"""
from __future__ import annotations

import re
from typing import List

import syrix_obs

from .. import types as T
from ..chunk import add_page, finalize
from ..safety import Deadline
from ..types import DocumentObject


def _decode(blob: bytes) -> str:
    if not blob:
        return ""
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError:
        _ = None  # not utf-8 → fall through to the detector chain below
    with syrix_obs.swallow("docintel.text.charset_normalizer"):
        import charset_normalizer  # type: ignore
        res = charset_normalizer.from_bytes(blob).best()
        if res is not None:
            return str(res)
    with syrix_obs.swallow("docintel.text.chardet"):
        import chardet  # type: ignore
        enc = (chardet.detect(blob) or {}).get("encoding") or "utf-8"
        return blob.decode(enc, errors="replace")
    return blob.decode("utf-8", errors="replace")


_IMPORT_RE = {
    "python": re.compile(r"^[ \t]*(?:from[ \t]+[\w\.]+[ \t]+import[ \t]+.+|import[ \t]+[\w\., \t]+)$", re.M),
    "javascript": re.compile(r"^[ \t]*(?:import\s+.+from\s+.+|const\s+\w+\s*=\s*require\()", re.M),
    "typescript": re.compile(r"^\s*import\s+.+from\s+.+", re.M),
    "java": re.compile(r"^\s*import\s+[\w\.]+;", re.M),
}
_DEF_RE = {
    "python": re.compile(r"^\s*(?:def|class)\s+(\w+)", re.M),
    "javascript": re.compile(r"(?:function\s+(\w+)|(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?\()", re.M),
    "typescript": re.compile(r"(?:function\s+(\w+)|class\s+(\w+)|(?:export\s+)?(?:const|let)\s+(\w+)\s*=)", re.M),
    "java": re.compile(r"(?:class|interface)\s+(\w+)|\b\w+\s+(\w+)\s*\([^;{]*\)\s*\{", re.M),
    "cpp": re.compile(r"(?:class|struct)\s+(\w+)|\b\w[\w:]*\s+(\w+)\s*\([^;{]*\)\s*\{", re.M),
}


def _code_structure(text: str, lang: str) -> dict:
    out: dict = {"language": lang}
    imp = _IMPORT_RE.get(lang)
    if imp:
        out["imports"] = [m.group(0).strip() for m in imp.finditer(text)][:60]
    dre = _DEF_RE.get(lang)
    if dre:
        names: List[str] = []
        for m in dre.finditer(text):
            for g in m.groups():
                if g:
                    names.append(g)
        out["symbols"] = names[:120]
        out["symbol_count"] = len(names)
    return out


def extract(blob: bytes, doc: DocumentObject, deadline: Deadline) -> None:
    text = _decode(blob)
    lines = text.count("\n") + 1
    doc.metadata.setdefault("line_count", lines)
    if doc.kind == T.KIND_CODE:
        lang = doc.subtype or "code"
        struct = _code_structure(text, lang)
        doc.metadata["code"] = struct
        doc.language = lang
        # Keep the whole file as chunks; code is usually small enough.
        add_page(doc, 0, "", text, kind="code")
    else:
        add_page(doc, 0, "", text, kind="text")
    doc.extraction_method = "text-decode"
    doc.confidence = 1.0
    finalize(doc)
