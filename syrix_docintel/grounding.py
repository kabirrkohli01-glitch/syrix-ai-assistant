"""Grounding — turn attached documents + a question into a bounded, cited
context block that gets injected into the brain prompt.

Three layers per turn:
  * **Document map** — a compact structural overview of every attached file
    (type, size, sections/sheets/slides, table inventory, extraction warnings,
    OCR confidence). This is what makes "what is in this file / summarise it /
    what's on page 3" answerable and keeps citations honest.
  * **Images** — if a vision callback is available we let the vision model read
    the actual pixels against the user's question (far better than a pre-baked
    caption); OCR text is added as backup. This is what makes "explain this
    chart / read this handwriting / what's wrong in this screenshot" work.
  * **Everything else** — retrieve the most relevant chunks (retrieve.py) and
    lay them out with ``[name p.N]`` citation markers. Spreadsheet questions
    about formulas/calculations additionally surface the real formula map.

The whole block is capped to a character budget so a huge file never blows the
provider context. Emits real Thinking-Panel events for each stage.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

import syrix_obs

from . import registry, retrieve, thinking, types as T
from .types import DocumentObject

VisionFn = Callable[[bytes, str], str]

# A1-style cell reference in the user's question ("explain D12").
_CELL_REF_RE = re.compile(r"\b[A-Z]{1,3}[1-9][0-9]{0,6}\b")
_FORMULA_QUERY_RE = re.compile(
    r"\b(formula|formulas|calculat|computed?|derive|reference|=sum|=if|vlookup|xlookup)\b",
    re.IGNORECASE)


def _cfg_int(key: str, default: int) -> int:
    try:
        import core_boot  # type: ignore
        return int(getattr(core_boot, "CONFIG", {}).get(key, default))
    except Exception:
        return default


def _human_size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


def _doc_outline(d: DocumentObject, max_items: int = 12) -> List[str]:
    """Distinct section headings / sheet names / slide labels, in order."""
    seen: List[str] = []
    for c in d.chunks:
        s = (c.section or "").strip()
        if not s or s in seen:
            continue
        loc = f" (p.{c.page})" if c.page and d.kind in (T.KIND_PDF, T.KIND_PRESENTATION) else ""
        seen.append(s + loc if loc and not s.startswith("Slide") else s)
        if len(seen) >= max_items:
            break
    return seen


def _doc_overview(d: DocumentObject) -> str:
    """Compact structural map of one document for the context header."""
    head: List[str] = [f"--- FILE: {d.name} ---"]
    line = d.card_line()
    if d.size:
        line += f" • {_human_size(d.size)}"
    head.append(line)
    for key in ("title", "author"):
        v = d.metadata.get(key)
        if v:
            head.append(f"{key.title()}: {str(v)[:120]}")
    outline = _doc_outline(d)
    if outline:
        label = ("Sheets" if d.kind == T.KIND_SPREADSHEET
                 else "Slides" if d.kind == T.KIND_PRESENTATION
                 else "Sections")
        head.append(f"{label}: " + " | ".join(outline))
    if d.tables and d.kind in (T.KIND_PDF, T.KIND_DOCUMENT):
        inv = ", ".join(
            (t.title or "table") + (f" (p.{t.page})" if t.page else "")
            for t in d.tables[:8])
        head.append(f"Tables: {inv}" + (" …" if len(d.tables) > 8 else ""))
    if d.metadata.get("sheet_refs"):
        refs = d.metadata["sheet_refs"]
        seg = "; ".join(f"{k} → {', '.join(v)}" for k, v in list(refs.items())[:6])
        head.append(f"Cross-sheet formula references: {seg}")
    if d.metadata.get("has_macros"):
        head.append("Contains VBA macros (NOT executed — values only).")
    if d.metadata.get("ocr_used"):
        conf = d.metadata.get("ocr_confidence")
        try:
            pct = f" (~{round(float(conf) * 100)}% confidence)" if conf else ""
        except Exception:
            pct = ""
        head.append(f"Parts were read with OCR{pct} — recognised text may contain errors; "
                    "treat exact figures/spellings from OCR as uncertain.")
    if d.status == T.STATUS_PARTIAL and d.notes:
        head.append("Extraction warnings: " + "; ".join(d.notes[:2]))
    return "\n".join(head)


def _relevant_formulas(d: DocumentObject, query: str, cap: int = 20) -> List[str]:
    """Formula cells worth showing for this question (exact cell asks first)."""
    formulas: Dict[str, str] = d.metadata.get("formulas") or {}
    if not formulas:
        return []
    wanted_cells = {m.group(0).upper() for m in _CELL_REF_RE.finditer(query or "")}
    picked: List[str] = []
    if wanted_cells:
        for cell, f in formulas.items():
            coord = cell.split("!", 1)[-1].upper()
            if coord in wanted_cells:
                picked.append(f"{cell} = {f}")
    if not picked and _FORMULA_QUERY_RE.search(query or ""):
        picked = [f"{cell} = {f}" for cell, f in list(formulas.items())[:cap]]
    return picked[:cap]


def _ocr_fallback(doc: Any, blob: bytes, request_id: str = "") -> str:
    """Local OCR of an image, run only when vision gave nothing. Never raises."""
    try:
        import io as _io

        from PIL import Image  # type: ignore

        from . import ocr as _ocr
        if not _ocr.enabled():
            return ""
        thinking.reading(f"Reading the text in {doc.name}", request_id=request_id)
        text, _conf, _method = _ocr.ocr_image(Image.open(_io.BytesIO(blob)).convert("RGB"))
        return str(text or "").strip()
    except Exception as exc:
        syrix_obs.report("docintel.grounding.ocr_fallback", exc, doc_id=getattr(doc, "id", ""))
        return ""


def build_grounding(doc_ids: List[str], query: str, *, vision_fn: Optional[VisionFn] = None,
                    request_id: str = "") -> Tuple[str, List[str]]:
    """Return ``(context_block, citations)``. Empty context if nothing usable."""
    docs = registry.get_many([str(x) for x in (doc_ids or [])])
    if not docs:
        return "", []

    budget = _cfg_int("docintel_context_char_budget", 12000)
    k = _cfg_int("docintel_retrieve_k", 12)
    names = ", ".join(d.name for d in docs)
    # No "Reading N attached file(s)" step here. Every caller already announces
    # the read and names the file — the v3 prefetch emits
    # "Reading Kabirr_Logistics_Q3_Review.pdf (PDF, 1 page, ~85 words)" and the
    # document tool carries its own label — so this added a second, vaguer line
    # saying the same thing one row above. The panel is read as a narrative of
    # what is happening; the same event twice reads as work done twice.

    sections: List[str] = []
    citations: List[str] = []
    used = 0

    def _push(block: str) -> bool:
        nonlocal used
        if not block:
            return False
        if used + len(block) > budget:
            return False
        sections.append(block)
        used += len(block)
        return True

    # --- document map: every attached file gets a structural overview ---
    for d in docs:
        _push(_doc_overview(d) + "\n")

    image_docs = [d for d in docs if d.kind == T.KIND_IMAGE]
    text_docs = [d for d in docs if d.kind != T.KIND_IMAGE]

    # --- images: vision reading against the actual question ---
    for d in image_docs:
        blob = registry.get_blob(d.id)
        vision_text = ""
        if vision_fn is not None and blob:
            thinking.reading(f"Looking at {d.name}", request_id=request_id)
            try:
                vision_text = str(vision_fn(blob, query) or "").strip()
            except Exception as exc:
                syrix_obs.report("docintel.grounding.vision", exc, doc_id=d.id)
        parts = [f"=== IMAGE: {d.name} ==="]
        if vision_text:
            parts.append("SYRIX looked at this image and observed:\n" + vision_text)
            citations.append(f"[{d.name}]")
        # OCR backup text (if any).
        ocr_text = "\n".join(c.text for c in d.chunks if c.kind == "ocr")
        if not vision_text and not ocr_text.strip() and blob:
            # The vision model could not be used — Private mode, no lane, an
            # outage — so read the text off the pixels locally instead.
            ocr_text = _ocr_fallback(d, blob, request_id)
        if ocr_text.strip():
            conf = 0.0
            try:
                conf = max((float(i.ocr_confidence or 0.0) for i in d.images), default=0.0)
            except Exception:
                conf = 0.0
            conf_note = f" (OCR confidence ~{round(conf * 100)}% — may contain errors)" if conf else ""
            parts.append(f"Text read from the image (OCR){conf_note}:\n" + ocr_text[:1500])
            if f"[{d.name}]" not in citations:
                citations.append(f"[{d.name}]")
        if not vision_text and not ocr_text.strip():
            parts.append("(No text detected and no vision model available for this image.)")
        _push("\n".join(parts) + "\n")

    # --- documents: retrieved chunks with citations ---
    if text_docs:
        # Note any that failed to extract, honestly.
        for d in text_docs:
            if d.status == T.STATUS_FAILED or (not d.chunks and d.error):
                _push(f"[{d.name}] could not be read: {d.error or 'extraction failed'}.\n")
        # Formula surfacing for spreadsheet calculation questions.
        for d in text_docs:
            if d.kind != T.KIND_SPREADSHEET:
                continue
            fx = _relevant_formulas(d, query)
            if fx:
                _push(f"[{d.name}] formulas (cell = formula):\n" + "\n".join(fx) + "\n")
                cite = f"[{d.name}]"
                if cite not in citations:
                    citations.append(cite)
        hits = retrieve.retrieve(text_docs, query, k_total=k)
        if hits:
            thinking.rank(f"Selected {len(hits)} relevant section(s)", request_id=request_id)
        for h in hits:
            cite = h.chunk.citation(h.doc.name)
            marker = " (OCR — may contain errors)" if h.chunk.kind == "ocr" else ""
            piece = f"{cite}{marker}\n{h.chunk.text.strip()}\n"
            if used + len(piece) > budget:
                continue
            sections.append(piece)
            used += len(piece)
            if cite not in citations:
                citations.append(cite)

    if not sections:
        return "", []

    multi = len(docs) > 1
    header = (
        "The user attached the file(s) below. Answer the user's question using ONLY "
        "this material.\n"
        "Rules:\n"
        "- Cite where each fact comes from, inline, like [filename p.N], "
        "[filename Slide N] or [filename SheetName] — matching the markers below.\n"
        "- If the material does not contain the answer, say so plainly — never invent "
        "content, numbers or quotes.\n"
        "- Text marked as OCR may contain recognition errors: flag uncertainty instead "
        "of asserting exact figures read from scans or photos.\n"
        + ("- When comparing files, attribute every claim to the specific file it "
           "came from.\n" if multi else "")
        + "- For financial or numeric analysis, state the assumptions behind any "
        "suggestion and show the calculation.\n\n"
        "=== ATTACHED MATERIAL ===\n"
    )
    footer = "\n=== END ATTACHED MATERIAL ==="
    # Not "Composing a grounded answer": nothing has been composed at this
    # point. This fires when the passages have been assembled, which is before
    # the model has been called at all, so the panel showed it and then showed
    # "Thinking" underneath — reading as though the answer were written twice.
    # Say what actually just happened.
    thinking.rank(f"Passages ready from {names[:120]}", request_id=request_id)
    return header + "\n".join(sections) + footer, citations
