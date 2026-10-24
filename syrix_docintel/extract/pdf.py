"""PDF extraction — text via pypdfium2, scanned pages fall back to OCR.

Per page we pull the embedded text layer; if a page has little/no text (a
scanned image), we rasterize it and run OCR. Every chunk is tagged with its
1-indexed page so citations resolve to "p.N". Honors the page cap and the
wall-clock deadline (returns a partial result rather than hanging).

Tables: a second, budget-bounded pass with pdfplumber (optional dependency)
turns detected tables into TableObjects + `kind="table"` chunks so questions
like "what does the table on page 3 show" ground on real rows instead of the
flattened text soup pypdfium2 produces for tabular layouts.
"""
from __future__ import annotations

import io
import logging

import syrix_obs

from .. import types as T
from ..chunk import add_page, finalize
from ..safety import Deadline, ProcessingError, load_limits
from ..types import DocumentObject, ImageRef, TableObject

_MIN_TEXT_CHARS = 12   # below this a page is treated as scanned → OCR
_TABLE_PASS_MAX_PAGES = 40   # pdfplumber is slow; only sweep the first N pages
_TABLE_MAX_ROWS = 60         # rows kept per extracted table (preview cap)


def _extract_tables(blob: bytes, doc: DocumentObject, deadline: Deadline,
                    text_pages: set) -> None:
    """Best-effort table pass. Only pages that had a native text layer are
    swept (scanned pages have no vector table structure for pdfplumber to
    find). Every failure degrades silently to the plain-text extraction."""
    try:
        import pdfplumber  # type: ignore
    except Exception:
        return
    # pdfminer warns once per glyph run about fonts missing a FontBBox — a
    # common, harmless trait of exported CVs and Office PDFs. One upload
    # wrote ~90 such lines and buried real problems in the runtime log.
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    try:
        pl = pdfplumber.open(io.BytesIO(blob))
    except Exception as exc:
        syrix_obs.report("docintel.pdf.tables_open", exc, level="info")
        return
    try:
        for i, page in enumerate(pl.pages[:_TABLE_PASS_MAX_PAGES]):
            if deadline.expired():
                break
            if (i + 1) not in text_pages:
                continue
            try:
                tables = page.extract_tables() or []
            except Exception:
                continue
            for rows in tables:
                rows = [[("" if c is None else str(c).strip()) for c in r] for r in (rows or []) if r]
                # Require real table shape: 2+ rows, 2+ columns, some content.
                if len(rows) < 2 or max(len(r) for r in rows) < 2:
                    continue
                if not any(any(c for c in r) for r in rows[1:]):
                    continue
                cols = rows[0]
                body = rows[1:]
                tobj = TableObject(page=i + 1, title=f"Table (p.{i + 1})",
                                   columns=cols, rows=body[:_TABLE_MAX_ROWS],
                                   n_rows=len(body), n_cols=max(len(r) for r in rows))
                doc.tables.append(tobj)
                add_page(doc, i + 1, f"Table (p.{i + 1})",
                         tobj.preview_md(max_rows=15), kind="table")
    finally:
        with syrix_obs.swallow("docintel.pdf.tables_close"):
            pl.close()


def extract(blob: bytes, doc: DocumentObject, deadline: Deadline) -> None:
    try:
        import pypdfium2 as pdfium  # type: ignore
    except Exception as exc:
        raise ProcessingError(f"PDF support unavailable ({exc}).")

    try:
        pdf = pdfium.PdfDocument(blob)
    except Exception as exc:
        raise ProcessingError(f"Could not open PDF ({exc}).")

    limits = load_limits()
    try:
        n_pages = len(pdf)
    except Exception:
        n_pages = 0
    doc.pages = n_pages
    doc.extraction_method = "pypdfium2"

    # Document metadata (best-effort).
    with syrix_obs.swallow("docintel.pdf.metadata"):
        meta = pdf.get_metadata_dict()  # type: ignore[attr-defined]
        if isinstance(meta, dict):
            for k in ("Title", "Author", "CreationDate", "ModDate", "Producer"):
                v = meta.get(k)
                if v:
                    doc.metadata[k.lower()] = str(v)[:200]

    process_n = min(n_pages, limits.max_pages)
    if n_pages > limits.max_pages:
        doc.status = T.STATUS_PARTIAL
        doc.add_note(f"Only the first {limits.max_pages} of {n_pages} pages were analyzed.")

    ocr_pages = 0
    page_confidences = []
    text_pages: set = set()   # 1-indexed pages that had a native text layer
    from .. import ocr as _ocr

    for i in range(process_n):
        try:
            deadline.check("PDF text extraction")
        except ProcessingError:
            doc.status = T.STATUS_PARTIAL
            doc.add_note(f"Time budget reached at page {i + 1}; remaining pages skipped.")
            break
        page_text = ""
        try:
            page = pdf[i]
            textpage = page.get_textpage()
            page_text = textpage.get_text_range() or ""
        except Exception as exc:
            doc.add_note(f"Page {i + 1} text extraction failed ({type(exc).__name__}).")
            page_text = ""

        if len(page_text.strip()) >= _MIN_TEXT_CHARS:
            text_pages.add(i + 1)
        elif _ocr.enabled():
            # Likely scanned — rasterize + OCR.
            try:
                bitmap = pdf[i].render(scale=2.0)
                pil = bitmap.to_pil()
                otext, oconf, method = _ocr.ocr_image(pil)
                if otext.strip():
                    page_text = otext
                    ocr_pages += 1
                    page_confidences.append(oconf)
                    doc.images.append(ImageRef(page=i + 1, ocr_text=otext[:400],
                                               ocr_confidence=round(oconf, 3)))
            except Exception as exc:
                doc.add_note(f"OCR failed on page {i + 1} ({type(exc).__name__}).")

        add_page(doc, i + 1, "", page_text, kind="text" if (i + 1) in text_pages else "ocr")

    if text_pages:
        _extract_tables(blob, doc, deadline, text_pages)

    if ocr_pages:
        doc.metadata["ocr_used"] = True
        doc.metadata["ocr_pages"] = ocr_pages
        avg_ocr = (sum(page_confidences) / len(page_confidences)) if page_confidences else 0.0
        doc.metadata["ocr_confidence"] = round(avg_ocr, 3)
        doc.extraction_method = "pypdfium2+ocr"
        # Confidence reflects the OCR'd portion; native-text pages are trusted.
        native = max(0, process_n - ocr_pages)
        doc.confidence = round((native * 1.0 + ocr_pages * avg_ocr) / max(1, process_n), 3)
    else:
        doc.confidence = 1.0 if doc.full_text().strip() else 0.0

    if not doc.full_text().strip():
        doc.status = T.STATUS_PARTIAL if doc.status != T.STATUS_FAILED else doc.status
        doc.add_note("No extractable text found — the PDF may be image-only and OCR was unavailable.")

    with syrix_obs.swallow("docintel.pdf.close"):
        pdf.close()
    finalize(doc)
