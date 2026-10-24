"""Presentation extraction — PPTX (python-pptx) and ODP (odfpy).

Per slide: title, body text, table text, and speaker notes — each tagged with
its slide number so citations resolve to "Slide N". Fast-follow formats degrade
to a clear note rather than a crash.
"""
from __future__ import annotations

import io

import syrix_obs

from .. import types as T
from ..chunk import add_page, finalize
from ..safety import Deadline, ProcessingError, load_limits
from ..types import DocumentObject, TableObject


def extract(blob: bytes, doc: DocumentObject, deadline: Deadline) -> None:
    if doc.subtype == "pptx":
        _extract_pptx(blob, doc, deadline)
    elif doc.subtype == "odp":
        _extract_odp(blob, doc)
    elif doc.subtype == "ppt":
        raise ProcessingError("Legacy binary .ppt isn't supported yet — please save as .pptx or PDF.")
    else:
        _extract_pptx(blob, doc, deadline)
    finalize(doc)


def _extract_pptx(blob: bytes, doc: DocumentObject, deadline: Deadline) -> None:
    try:
        from pptx import Presentation  # type: ignore
    except Exception as exc:
        raise ProcessingError(f"PPTX support unavailable (install python-pptx) — {exc}.")
    try:
        prs = Presentation(io.BytesIO(blob))
    except Exception as exc:
        raise ProcessingError(f"Could not open PPTX ({exc}).")

    doc.extraction_method = "python-pptx"
    limits = load_limits()
    slides = list(prs.slides)
    doc.slides = len(slides)

    for idx, slide in enumerate(slides[: limits.max_pages], start=1):
        try:
            deadline.check("presentation parsing")
        except ProcessingError:
            doc.status = T.STATUS_PARTIAL
            doc.add_note(f"Time budget reached at slide {idx}.")
            break
        parts = []
        for shape in slide.shapes:
            try:
                if shape.has_text_frame and shape.text_frame.text.strip():
                    parts.append(shape.text_frame.text.strip())
                if getattr(shape, "has_table", False) and shape.has_table:
                    rows = [[c.text.strip() for c in row.cells] for row in shape.table.rows]
                    if rows:
                        tobj = TableObject(page=idx, title=f"Slide {idx} table",
                                           columns=rows[0], rows=rows[1:][:100],
                                           n_rows=max(0, len(rows) - 1), n_cols=len(rows[0]))
                        doc.tables.append(tobj)
                        parts.append(tobj.preview_md(max_rows=12))
            except Exception:
                continue
        # Speaker notes.
        with syrix_obs.swallow("docintel.pptx.notes"):
            if slide.has_notes_slide:
                notes = slide.notes_slide.notes_text_frame.text.strip()
                if notes:
                    parts.append("Speaker notes: " + notes)
        text = "\n".join(parts)
        if text.strip():
            add_page(doc, idx, f"Slide {idx}", text, kind="text")

    doc.confidence = 1.0
    if not doc.full_text().strip():
        doc.status = T.STATUS_PARTIAL
        doc.add_note("No readable text found in the presentation.")


def _extract_odp(blob: bytes, doc: DocumentObject) -> None:
    try:
        from odf.opendocument import load  # type: ignore
        from odf import teletype  # type: ignore
        from odf.text import P  # type: ignore
    except Exception as exc:
        raise ProcessingError(f"ODP support unavailable (install odfpy) — {exc}.")
    try:
        odp = load(io.BytesIO(blob))
        paras = odp.getElementsByType(P)
        text = "\n".join(teletype.extractText(p) for p in paras)
    except Exception as exc:
        raise ProcessingError(f"Could not parse ODP ({exc}).")
    doc.extraction_method = "odfpy"
    add_page(doc, 0, "", text, kind="text")
    doc.confidence = 1.0
