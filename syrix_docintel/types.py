"""Normalized document objects for SYRIX Document Intelligence.

Every uploaded file — image, PDF, spreadsheet, doc, code, archive — is parsed
into a single :class:`DocumentObject`. Downstream layers (chunking, retrieval,
grounding, the HUD attachment card) only ever see this shape, so adding a new
file type is a matter of writing one extractor that returns a DocumentObject.

Pure stdlib. Serializable to/from plain dicts so the disk cache and the HUD
state file can round-trip a document without importing this module.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def _clip(s: Any, n: int) -> str:
    try:
        t = str(s)
    except Exception:
        return ""
    return t if len(t) <= n else t[:n]


def _plural(n: int, singular: str) -> str:
    return f"{n:,} {singular}" if n == 1 else f"{n:,} {singular}s"


@dataclass
class TableObject:
    """One extracted table (spreadsheet sheet, PDF/doc table, ...)."""
    page: int = 0            # 1-indexed source page/slide (0 = unknown)
    sheet: str = ""          # sheet name for spreadsheets
    title: str = ""
    columns: List[str] = field(default_factory=list)
    rows: List[List[str]] = field(default_factory=list)   # already row-capped by the extractor
    n_rows: int = 0          # true row count (rows[] may be a capped preview)
    n_cols: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "page": self.page, "sheet": self.sheet, "title": self.title,
            "columns": self.columns, "rows": self.rows,
            "n_rows": self.n_rows, "n_cols": self.n_cols,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TableObject":
        return cls(
            page=int(d.get("page") or 0), sheet=str(d.get("sheet") or ""),
            title=str(d.get("title") or ""),
            columns=list(d.get("columns") or []), rows=list(d.get("rows") or []),
            n_rows=int(d.get("n_rows") or 0), n_cols=int(d.get("n_cols") or 0),
        )

    def preview_md(self, max_rows: int = 12) -> str:
        """Render a compact markdown table for the grounding context block."""
        cols = self.columns or (["c%d" % i for i in range(self.n_cols)] if self.n_cols else [])
        lines: List[str] = []
        if self.title:
            lines.append(f"**{self.title}**")
        if cols:
            lines.append("| " + " | ".join(_clip(c, 40) for c in cols) + " |")
            lines.append("| " + " | ".join("---" for _ in cols) + " |")
        for r in self.rows[:max_rows]:
            lines.append("| " + " | ".join(_clip(c, 40) for c in r) + " |")
        if self.n_rows > max_rows:
            lines.append(f"_… {self.n_rows - max_rows} more rows_")
        return "\n".join(lines)


@dataclass
class ImageRef:
    """An image inside a document, or the uploaded image itself."""
    page: int = 0
    ocr_text: str = ""
    ocr_confidence: float = 0.0
    caption: str = ""        # vision-model description
    width: int = 0
    height: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "page": self.page, "ocr_text": self.ocr_text,
            "ocr_confidence": self.ocr_confidence, "caption": self.caption,
            "width": self.width, "height": self.height,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ImageRef":
        return cls(
            page=int(d.get("page") or 0), ocr_text=str(d.get("ocr_text") or ""),
            ocr_confidence=float(d.get("ocr_confidence") or 0.0),
            caption=str(d.get("caption") or ""),
            width=int(d.get("width") or 0), height=int(d.get("height") or 0),
        )


@dataclass
class ChunkObject:
    """One retrieval unit. `page`/`section` power the citation markers."""
    id: str
    text: str
    page: int = 0            # 1-indexed source page/slide/sheet (0 = unknown)
    section: str = ""        # heading path or sheet name
    kind: str = "text"       # text|table|ocr|caption|code

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "text": self.text, "page": self.page,
                "section": self.section, "kind": self.kind}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ChunkObject":
        return cls(
            id=str(d.get("id") or ""), text=str(d.get("text") or ""),
            page=int(d.get("page") or 0), section=str(d.get("section") or ""),
            kind=str(d.get("kind") or "text"),
        )

    def citation(self, doc_name: str) -> str:
        loc = ""
        if self.section:
            loc = f" §{_clip(self.section, 40)}"
        elif self.page:
            loc = f" p.{self.page}"
        return f"[{doc_name}{loc}]"


# Canonical document kinds (also used by detect.py).
KIND_IMAGE = "image"
KIND_PDF = "pdf"
KIND_SPREADSHEET = "spreadsheet"
KIND_PRESENTATION = "presentation"
KIND_DOCUMENT = "document"
KIND_CODE = "code"
KIND_ARCHIVE = "archive"
KIND_TEXT = "text"
KIND_UNKNOWN = "unknown"

STATUS_READY = "ready"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"


@dataclass
class DocumentObject:
    id: str
    name: str
    kind: str = KIND_UNKNOWN
    mime: str = ""
    subtype: str = ""        # docx|xlsx|pptx|csv|png|zip|py|...
    size: int = 0
    sha256: str = ""
    pages: int = 0
    sheets: int = 0
    slides: int = 0
    image_count: int = 0
    table_count: int = 0
    language: str = ""
    word_count: int = 0
    char_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    tables: List[TableObject] = field(default_factory=list)
    images: List[ImageRef] = field(default_factory=list)
    chunks: List[ChunkObject] = field(default_factory=list)
    summary: str = ""
    statistics: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    extraction_method: str = ""
    processing_time: float = 0.0
    notes: List[str] = field(default_factory=list)   # extraction warnings / partial failures
    status: str = STATUS_READY
    error: str = ""
    created_ts: float = field(default_factory=time.time)

    # -- helpers ---------------------------------------------------------
    def add_note(self, msg: str) -> None:
        m = _clip(msg, 200)
        if m and m not in self.notes:
            self.notes.append(m)

    def full_text(self) -> str:
        """Concatenate every chunk's text in document order."""
        return "\n\n".join(c.text for c in self.chunks if c.text)

    def card_line(self) -> str:
        """Type-adaptive subtitle for the HUD attachment card.

        e.g. "PDF • 84 pages • 16 tables"  /  "Spreadsheet • 5 sheets"
             "Image • 1920×1080"  /  "Archive • 214 files".
        """
        seg: List[str] = []
        label = {
            KIND_PDF: "PDF", KIND_SPREADSHEET: "Spreadsheet",
            KIND_PRESENTATION: "Presentation", KIND_DOCUMENT: "Word",
            KIND_IMAGE: "Image", KIND_CODE: "Code", KIND_ARCHIVE: "Archive",
            KIND_TEXT: "Text", KIND_UNKNOWN: (self.subtype or "File").upper(),
        }.get(self.kind, self.kind.title())
        seg.append(label)
        if self.kind == KIND_PDF and self.pages:
            seg.append(_plural(self.pages, "page"))
        if self.kind == KIND_DOCUMENT and self.pages:
            seg.append(_plural(self.pages, "page"))
        elif self.kind == KIND_DOCUMENT and self.word_count:
            seg.append(_plural(self.word_count, "word"))
        if self.kind == KIND_SPREADSHEET and self.sheets:
            seg.append(_plural(self.sheets, "sheet"))
        if self.kind == KIND_PRESENTATION and self.slides:
            seg.append(_plural(self.slides, "slide"))
        if self.kind == KIND_IMAGE:
            w = int(self.metadata.get("width") or 0)
            h = int(self.metadata.get("height") or 0)
            if w and h:
                seg.append(f"{w}×{h}")
            if self.metadata.get("ocr_used"):
                seg.append("OCR")
        if self.kind == KIND_ARCHIVE:
            n = int(self.metadata.get("entry_count") or 0)
            if n:
                seg.append(_plural(n, "file"))
        if self.table_count and self.kind in (KIND_PDF, KIND_DOCUMENT):
            seg.append(_plural(self.table_count, "table"))
        if self.kind in (KIND_TEXT, KIND_CODE) and self.word_count:
            seg.append(_plural(self.word_count, "word"))
        return " • ".join(seg)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "kind": self.kind, "mime": self.mime,
            "subtype": self.subtype, "size": self.size, "sha256": self.sha256,
            "pages": self.pages, "sheets": self.sheets, "slides": self.slides,
            "image_count": self.image_count, "table_count": self.table_count,
            "language": self.language, "word_count": self.word_count,
            "char_count": self.char_count, "metadata": self.metadata,
            "tables": [t.to_dict() for t in self.tables],
            "images": [i.to_dict() for i in self.images],
            "chunks": [c.to_dict() for c in self.chunks],
            "summary": self.summary, "statistics": self.statistics,
            "confidence": self.confidence, "extraction_method": self.extraction_method,
            "processing_time": self.processing_time, "notes": self.notes,
            "status": self.status, "error": self.error, "created_ts": self.created_ts,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DocumentObject":
        return cls(
            id=str(d.get("id") or ""), name=str(d.get("name") or ""),
            kind=str(d.get("kind") or KIND_UNKNOWN), mime=str(d.get("mime") or ""),
            subtype=str(d.get("subtype") or ""), size=int(d.get("size") or 0),
            sha256=str(d.get("sha256") or ""), pages=int(d.get("pages") or 0),
            sheets=int(d.get("sheets") or 0), slides=int(d.get("slides") or 0),
            image_count=int(d.get("image_count") or 0), table_count=int(d.get("table_count") or 0),
            language=str(d.get("language") or ""), word_count=int(d.get("word_count") or 0),
            char_count=int(d.get("char_count") or 0), metadata=dict(d.get("metadata") or {}),
            tables=[TableObject.from_dict(x) for x in (d.get("tables") or [])],
            images=[ImageRef.from_dict(x) for x in (d.get("images") or [])],
            chunks=[ChunkObject.from_dict(x) for x in (d.get("chunks") or [])],
            summary=str(d.get("summary") or ""), statistics=dict(d.get("statistics") or {}),
            confidence=float(d.get("confidence") or 1.0),
            extraction_method=str(d.get("extraction_method") or ""),
            processing_time=float(d.get("processing_time") or 0.0),
            notes=list(d.get("notes") or []), status=str(d.get("status") or STATUS_READY),
            error=str(d.get("error") or ""), created_ts=float(d.get("created_ts") or time.time()),
        )

    def card(self, upload_id: str = "") -> Dict[str, Any]:
        """Minimal payload the HUD renders as an attachment card (no bulk text)."""
        return {
            "upload_id": upload_id, "doc_id": self.id, "name": self.name,
            "kind": self.kind, "subtype": self.subtype, "status": self.status,
            "subtitle": self.card_line(), "confidence": round(self.confidence, 2),
            "error": self.error, "notes": self.notes[:3],
        }
