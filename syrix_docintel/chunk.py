"""Chunking — turn extracted page/section text into retrieval units.

Extractors call :func:`add_page` with a page/slide/sheet's text and its source
location; long text is split into ~1.2k-char chunks that keep paragraph
boundaries, each tagged with its page + section so citations resolve to a real
location. Splitter chain (first available wins):

  1. LangChain RecursiveCharacterTextSplitter (already installed)
  2. plain paragraph-pack fallback (zero-dependency)

Deterministic, no LLM, no network. Never raises.
"""
from __future__ import annotations

import re
from typing import List

import syrix_obs

from .types import ChunkObject, DocumentObject

_TARGET_CHARS = 1200
_MAX_CHUNKS_PER_DOC = 4000    # hard cap so a pathological file can't explode memory


def _split_langchain(text: str, target: int) -> List[str]:
    from langchain_text_splitters import RecursiveCharacterTextSplitter  # type: ignore
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=target, chunk_overlap=0,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    return [c for c in splitter.split_text(text) if c.strip()]


def _split_fallback(text: str, target: int) -> List[str]:
    paras = re.split(r"\n\s*\n", text)
    out: List[str] = []
    buf = ""
    for p in paras:
        p = p.strip()
        if not p:
            continue
        if len(buf) + len(p) + 2 <= target:
            buf = (buf + "\n\n" + p) if buf else p
        else:
            if buf:
                out.append(buf)
            # A single oversized paragraph is hard-wrapped.
            while len(p) > target:
                out.append(p[:target])
                p = p[target:]
            buf = p
    if buf:
        out.append(buf)
    return out


def split_text(text: str, target: int = _TARGET_CHARS) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= target:
        return [text]
    parts = None
    with syrix_obs.swallow("docintel.chunk.langchain"):
        parts = _split_langchain(text, target)
    if parts:
        return parts
    return _split_fallback(text, target)


def add_page(doc: DocumentObject, page: int, section: str, text: str, kind: str = "text") -> None:
    """Split one page/section's text and append chunk(s) to the document."""
    text = (text or "").strip()
    if not text:
        return
    for part in split_text(text):
        if len(doc.chunks) >= _MAX_CHUNKS_PER_DOC:
            doc.add_note("chunk cap reached; document truncated for retrieval")
            return
        cid = f"{doc.id}::c{len(doc.chunks)}"
        doc.chunks.append(ChunkObject(id=cid, text=part, page=int(page or 0),
                                      section=str(section or ""), kind=kind))


def finalize(doc: DocumentObject) -> None:
    """Compute derived counts once all chunks/tables/images are populated."""
    full = doc.full_text()
    if not doc.char_count:
        doc.char_count = len(full)
    if not doc.word_count:
        doc.word_count = len(full.split())
    doc.table_count = len(doc.tables) or doc.table_count
    doc.image_count = len(doc.images) or doc.image_count
    if not doc.language:
        doc.language = _guess_language(full)


_COMMON_EN = frozenset("the and for that with this from have are was were you not".split())


def _guess_language(text: str) -> str:
    """Cheap English-vs-other heuristic (no heavy langdetect dependency)."""
    sample = (text or "")[:4000].lower()
    if not sample.strip():
        return ""
    words = re.findall(r"[a-z']+", sample)
    if not words:
        return ""
    hits = sum(1 for w in words if w in _COMMON_EN)
    return "English" if hits >= 3 else ""
