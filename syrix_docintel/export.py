"""Edited downloadable versions — "rewrite this document and give me the file".

This module is deliberately LLM-free: the caller (syrix_host's doc-grounded
turn) detects the intent here, asks the brain for the rewritten body with
:func:`build_rewrite_prompt`, then hands the result to :func:`write_export`,
which writes a fresh file into ``%LOCALAPPDATA%\\SYRIX\\exports``.

Format policy (v1, honest about limits):
  * document / pdf sources  → .docx (python-docx), headings/bullets preserved
    from the markdown-ish body the prompt requests.
  * text / code / markdown  → same-family plain text (.md / .txt).
  * spreadsheets / slides / images → .md notes file (a faithful cell-level or
    slide-level rewrite is not implemented — the caller says so in chat).

Safety: output filenames are sanitised (no traversal), bodies are size-capped,
and nothing from the source document is ever executed.
"""
from __future__ import annotations

import os
import re
import time
from typing import Optional, Tuple

from . import types as T
from .types import DocumentObject

# "rewrite/improve … (and) … downloadable/file/version" and close variants.
_EXPORT_INTENT_RE = re.compile(
    r"(?:\b(?:rewrite|revise|improve|polish|proofread|correct|fix|edit|redo|"
    r"clean\s*up|enhance|reword|rephrase|modernize|modernise)\b.{0,100}?"
    r"\b(?:downloadable|download|editable|edited|new|updated)?\s*"
    r"(?:version|copy|file|document|docx?|draft)\b"
    r"|\b(?:create|make|generate|give\s+me|produce|save|export)\b.{0,60}?"
    r"\b(?:edited|improved|corrected|revised|rewritten|updated|polished|cleaned|"
    r"downloadable)\b.{0,60}?\b(?:version|copy|file|document|draft)\b"
    r"|\bdownloadable\s+version\b)",
    re.IGNORECASE | re.DOTALL)

_LANGUAGE_ONLY_RE = re.compile(
    r"\b(?:only|just)\b.{0,40}\b(?:language|wording|grammar|phrasing|spelling|style)\b"
    r"|\b(?:language|wording|grammar|phrasing|spelling|style)\b.{0,20}\bonly\b"
    r"|keep\s+(?:the\s+)?(?:same\s+)?(?:document|content|structure|meaning)",
    re.IGNORECASE)


def detect_export_intent(text: str) -> bool:
    return bool(_EXPORT_INTENT_RE.search(str(text or "")))


def _cfg_int(key: str, default: int) -> int:
    try:
        import core_boot  # type: ignore
        return int(getattr(core_boot, "CONFIG", {}).get(key, default))
    except Exception:
        return default


def export_dir() -> str:
    try:
        import syrix_paths
        root = syrix_paths.syrix_root()
    except Exception:
        root = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "SYRIX")
    path = os.path.join(root, "exports")
    os.makedirs(path, exist_ok=True)
    return path


def build_rewrite_prompt(doc: DocumentObject, instruction: str) -> str:
    """Prompt asking the brain for the full rewritten body, structure intact."""
    cap = max(4000, _cfg_int("docintel_export_max_chars", 60000))
    body = doc.full_text()[:cap]
    truncated = len(doc.full_text()) > cap
    language_only = bool(_LANGUAGE_ONLY_RE.search(str(instruction or "")))
    rules = [
        "Keep the document's meaning, facts, names and numbers exactly as they are.",
        "Keep the same overall structure: the same sections in the same order, "
        "headings as markdown headings (#, ##), lists as markdown lists.",
        "Do not add new sections, opinions or invented content.",
        "Output ONLY the rewritten document body in markdown — no preamble, no "
        "commentary, no code fences.",
    ]
    if language_only:
        rules.insert(0, "Change ONLY the language: clearer wording, fixed grammar and "
                        "spelling. Do not restructure or shorten.")
    prompt = (
        f"Rewrite the document below according to this instruction from the user:\n"
        f"INSTRUCTION: {str(instruction or '').strip()[:500]}\n\n"
        "Rules:\n" + "\n".join(f"- {r}" for r in rules) + "\n\n"
        f"=== DOCUMENT: {doc.name} ===\n{body}\n=== END DOCUMENT ==="
    )
    if truncated:
        prompt += "\n\n(Note: the document was truncated to its first part for this rewrite.)"
    return prompt


def _safe_stem(name: str) -> str:
    stem = os.path.basename(str(name or "document"))
    stem = stem.rsplit(".", 1)[0]
    stem = re.sub(r"[^\w\s\-.]", "", stem, flags=re.UNICODE).strip() or "document"
    return stem[:80]


def _target_ext(doc: DocumentObject) -> str:
    if doc.kind in (T.KIND_DOCUMENT, T.KIND_PDF):
        return ".docx"
    if doc.kind in (T.KIND_TEXT, T.KIND_CODE):
        sub = (doc.subtype or "").lower()
        if sub in ("md", "markdown"):
            return ".md"
        if doc.kind == T.KIND_CODE:
            return "." + (sub or "txt")
        return ".txt"
    return ".md"


_MD_H_RE = re.compile(r"^(#{1,4})\s+(.*)$")
_MD_LI_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*)$")


def _write_docx(path: str, body: str) -> None:
    import docx  # type: ignore
    d = docx.Document()
    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        m = _MD_H_RE.match(line)
        if m:
            d.add_heading(_strip_md_inline(m.group(2)), level=min(4, len(m.group(1))))
            continue
        m = _MD_LI_RE.match(line)
        if m:
            d.add_paragraph(_strip_md_inline(m.group(1)), style="List Bullet")
            continue
        d.add_paragraph(_strip_md_inline(line))
    d.save(path)


def _strip_md_inline(s: str) -> str:
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", s)
    s = re.sub(r"`([^`]+)`", r"\1", s)
    return s


def write_export(doc: DocumentObject, new_text: str) -> Tuple[Optional[str], str]:
    """Write the rewritten body to a fresh file. Returns (path, note); path is
    None with an explanatory note on failure. Never raises."""
    body = str(new_text or "").strip()
    if not body:
        return None, "the rewrite came back empty"
    body = body[: max(4000, _cfg_int("docintel_export_max_chars", 60000) * 2)]
    ext = _target_ext(doc)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    fname = f"{_safe_stem(doc.name)} (edited by SYRIX) {stamp}{ext}"
    path = os.path.join(export_dir(), fname)
    try:
        if ext == ".docx":
            try:
                _write_docx(path, body)
            except Exception:
                # Degrade to markdown rather than losing the user's rewrite.
                path = path[: -len(ext)] + ".md"
                with open(path, "w", encoding="utf-8") as f:
                    f.write(body + "\n")
        else:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(body + "\n")
    except Exception as exc:
        import syrix_obs
        syrix_obs.report("docintel.export.write", exc, name=doc.name)
        return None, f"could not write the file ({type(exc).__name__})"
    return path, ""
