"""Image extraction — dimensions and HEIC decode.

Understanding an image — "explain this chart", "read this handwriting",
"what's wrong in this screenshot", and any text in it — happens at question
time: the vision model looks at the pixels against the actual question (see
grounding.py). The raw bytes are kept by the orchestrator so that path runs.

OCR used to run here, at upload. Its first use loads PaddleOCR — measured
19.3 s — and the image showed as still analysing for all of it, so a question
sent in that window went without the image and was answered "I can't view
it". Paddle's loader also started `ver` and `where` processes that flashed
console windows. OCR now runs only when the vision model cannot be used (see
grounding.py), unless docintel_image_ocr_at_upload turns it back on.
"""
from __future__ import annotations

import io

import syrix_obs

from .. import types as T
from ..chunk import add_page, finalize
from ..safety import Deadline, ProcessingError
from ..types import DocumentObject, ImageRef

_HEIF_REGISTERED = False


def _ensure_heif() -> None:
    global _HEIF_REGISTERED
    if _HEIF_REGISTERED:
        return
    with syrix_obs.swallow("docintel.image.heif_register"):
        import pillow_heif  # type: ignore
        pillow_heif.register_heif_opener()
    _HEIF_REGISTERED = True


def extract(blob: bytes, doc: DocumentObject, deadline: Deadline) -> None:
    from PIL import Image  # type: ignore
    if doc.subtype in ("heic", "heif"):
        _ensure_heif()
    try:
        im = Image.open(io.BytesIO(blob))
        w, h = im.size
    except Exception as exc:
        raise ProcessingError(f"Could not open image ({exc}).")

    doc.pages = 1
    doc.image_count = 1
    doc.metadata["width"] = int(w)
    doc.metadata["height"] = int(h)
    doc.metadata["mode"] = getattr(im, "mode", "")
    doc.extraction_method = "pillow"

    # OCR at upload only when configured to; see the module docstring.
    from .. import ocr as _ocr
    at_upload = False
    with syrix_obs.swallow("docintel.image.ocr_cfg"):
        import core_boot  # type: ignore
        at_upload = bool(getattr(core_boot, "CONFIG", {}).get("docintel_image_ocr_at_upload", False))
    if at_upload and _ocr.enabled():
        try:
            text, conf, method = _ocr.ocr_image(im.convert("RGB"))
        except Exception as exc:
            text, conf, method = "", 0.0, "none"
            doc.add_note(f"OCR failed ({type(exc).__name__}).")
        if text.strip():
            doc.metadata["ocr_used"] = True
            doc.metadata["ocr_method"] = method
            doc.images.append(ImageRef(page=1, ocr_text=text[:2000], ocr_confidence=round(conf, 3),
                                       width=int(w), height=int(h)))
            add_page(doc, 1, "OCR text", text, kind="ocr")
            doc.confidence = round(max(0.4, conf), 3) if conf else 0.6
        else:
            doc.confidence = 1.0   # image loaded fine; understanding happens via vision at Q-time
    else:
        doc.confidence = 1.0

    finalize(doc)
