"""Extractor dispatch — route a DocumentObject to the right parser by kind.

Each extractor mutates the passed :class:`DocumentObject` in place (populating
``chunks``, ``tables``, ``images``, ``metadata``, counts) and may raise
``safety.ProcessingError`` for a graceful refusal. Any other exception is
caught by the orchestrator, which marks the document ``partial``/``failed``
with an honest note — extraction never fabricates content.
"""
from __future__ import annotations

from .. import types as T
from ..safety import Deadline
from ..types import DocumentObject


def run(kind: str, blob: bytes, doc: DocumentObject, deadline: Deadline) -> None:
    if kind == T.KIND_PDF:
        from . import pdf
        pdf.extract(blob, doc, deadline)
    elif kind == T.KIND_SPREADSHEET:
        from . import spreadsheet
        spreadsheet.extract(blob, doc, deadline)
    elif kind == T.KIND_DOCUMENT:
        from . import document
        document.extract(blob, doc, deadline)
    elif kind == T.KIND_PRESENTATION:
        from . import presentation
        presentation.extract(blob, doc, deadline)
    elif kind == T.KIND_IMAGE:
        from . import image
        image.extract(blob, doc, deadline)
    elif kind == T.KIND_ARCHIVE:
        from . import archive
        archive.extract(blob, doc, deadline)
    elif kind in (T.KIND_CODE, T.KIND_TEXT):
        from . import text_code
        text_code.extract(blob, doc, deadline)
    else:
        from . import text_code
        text_code.extract(blob, doc, deadline)  # best-effort: try as text
