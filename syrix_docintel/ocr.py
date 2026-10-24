"""OCR engine chain: PaddleOCR → Tesseract → (none).

Extracts text + a real confidence from an image. Both engines are heavy and
optional — the loaders are lazy singletons and every failure degrades to the
next engine, then to an empty result with ``method="none"`` (never a fake
value: the caller surfaces "OCR unavailable / low confidence" honestly).

Config:
  docintel_ocr_enabled  (default True)   — master switch
  docintel_ocr_engine   (default "auto") — auto|paddle|tesseract|off
"""
from __future__ import annotations

import io
import threading
from typing import Any, List, Optional, Tuple

import syrix_obs

_LOCK = threading.RLock()
_PADDLE: Any = None
_PADDLE_TRIED = False
_TESS_OK: Optional[bool] = None


def _cfg(key: str, default: Any) -> Any:
    try:
        import core_boot  # type: ignore
        return getattr(core_boot, "CONFIG", {}).get(key, default)
    except Exception:
        return default


def enabled() -> bool:
    return bool(_cfg("docintel_ocr_enabled", True)) and str(_cfg("docintel_ocr_engine", "auto")).lower() != "off"


def _engine_pref() -> str:
    return str(_cfg("docintel_ocr_engine", "auto")).lower()


def _to_pil(image: Any):
    from PIL import Image  # type: ignore
    if isinstance(image, (bytes, bytearray)):
        return Image.open(io.BytesIO(bytes(image))).convert("RGB")
    return image.convert("RGB") if hasattr(image, "convert") else image


# --------------------------------------------------------------------------
# PaddleOCR
# --------------------------------------------------------------------------
def _get_paddle() -> Any:
    global _PADDLE, _PADDLE_TRIED
    with _LOCK:
        if _PADDLE_TRIED:
            return _PADDLE
        _PADDLE_TRIED = True
        try:
            from paddleocr import PaddleOCR  # type: ignore
        except Exception as exc:
            syrix_obs.report("docintel.ocr.paddle_load", exc, level="info")
            _PADDLE = None
            return _PADDLE
        lang = str(_cfg("docintel_ocr_lang", "en") or "en")
        # PaddleOCR 3.x: the 2.x kwargs (show_log / use_angle_cls) raise
        # ValueError, which used to kill the whole engine silently. Build for
        # 3.x first — det+rec only, mobile models (CPU-friendly; the extra
        # orientation/unwarp stages are separate model downloads we don't
        # need for documents) — and keep the 2.x construction as fallback.
        # enable_mkldnn=False: paddlepaddle 3.3 oneDNN hits an unimplemented
        # PIR attribute conversion on Windows CPU (NotImplementedError in
        # onednn_instruction.cc) — detection crashes with it on.
        try:
            # PP-OCRv5 mobile covers en/zh/latin in one model, so `lang` is
            # intentionally not passed here (3.x ignores it — with a warning —
            # when explicit model names are given).
            _PADDLE = PaddleOCR(
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                text_detection_model_name="PP-OCRv5_mobile_det",
                text_recognition_model_name="PP-OCRv5_mobile_rec",
                enable_mkldnn=False,
            )
        except Exception as exc3:
            try:
                _PADDLE = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)
            except Exception as exc2:
                syrix_obs.report("docintel.ocr.paddle_load", exc2,
                                 first_error=str(exc3)[:120], level="info")
                _PADDLE = None
        return _PADDLE


def _parse_paddle_result(result: Any) -> Tuple[List[str], List[float]]:
    """Accept both result shapes: 3.x OCRResult mappings with rec_texts /
    rec_scores, and the legacy 2.x [[box, (text, score)], ...] pages."""
    lines: List[str] = []
    scores: List[float] = []
    for page in result or []:
        if page is None:
            continue
        rec_texts = None
        try:
            if hasattr(page, "get"):
                rec_texts = page.get("rec_texts")
        except Exception:
            rec_texts = None
        if rec_texts is not None:
            rec_scores = list(page.get("rec_scores") or [])
            for i, txt in enumerate(rec_texts):
                t = str(txt)
                if not t.strip():
                    continue
                lines.append(t)
                if i < len(rec_scores):
                    try:
                        scores.append(float(rec_scores[i]))
                    except (TypeError, ValueError):
                        scores.append(0.0)
            continue
        for entry in page or []:
            try:
                txt = str(entry[1][0])
                sc = float(entry[1][1])
            except Exception:
                continue
            if txt.strip():
                lines.append(txt)
                scores.append(sc)
    return lines, scores


def _paddle_ocr(image: Any) -> Optional[Tuple[str, float]]:
    eng = _get_paddle()
    if eng is None:
        return None
    try:
        import numpy as np  # type: ignore
        pil = _to_pil(image)
        arr = np.array(pil)
        if hasattr(eng, "predict"):
            result = eng.predict(arr)
        else:
            result = eng.ocr(arr, cls=True)
    except Exception as exc:
        syrix_obs.report("docintel.ocr.paddle_run", exc, level="info")
        return None
    try:
        lines, scores = _parse_paddle_result(result)
    except Exception as exc:
        syrix_obs.report("docintel.ocr.paddle_parse", exc, level="info")
        return None
    if not lines:
        return ("", 0.0)
    conf = sum(scores) / len(scores) if scores else 0.0
    return ("\n".join(lines), conf)


# --------------------------------------------------------------------------
# Tesseract
# --------------------------------------------------------------------------
def _tesseract_ocr(image: Any) -> Optional[Tuple[str, float]]:
    global _TESS_OK
    if _TESS_OK is False:
        return None
    try:
        import pytesseract  # type: ignore
        from pytesseract import Output  # type: ignore
        pil = _to_pil(image)
        data = pytesseract.image_to_data(pil, output_type=Output.DICT)
        _TESS_OK = True
    except Exception as exc:
        _TESS_OK = False
        syrix_obs.report("docintel.ocr.tesseract", exc, level="info")
        return None
    words: List[str] = []
    confs: List[float] = []
    try:
        for txt, conf in zip(data.get("text", []), data.get("conf", [])):
            t = str(txt).strip()
            if not t:
                continue
            try:
                c = float(conf)
            except Exception:
                c = -1.0
            if c < 0:
                continue
            words.append(t)
            confs.append(c / 100.0)
    except Exception:
        return None
    if not words:
        return ("", 0.0)
    return (" ".join(words), (sum(confs) / len(confs)) if confs else 0.0)


def ocr_image(image: Any) -> Tuple[str, float, str]:
    """Return ``(text, confidence_0_1, method)``. Never raises."""
    if not enabled():
        return ("", 0.0, "off")
    pref = _engine_pref()
    order: List[str] = []
    if pref == "paddle":
        order = ["paddle"]
    elif pref == "tesseract":
        order = ["tesseract"]
    else:  # auto
        order = ["paddle", "tesseract"]
    for eng in order:
        try:
            res = _paddle_ocr(image) if eng == "paddle" else _tesseract_ocr(image)
        except Exception as exc:
            syrix_obs.report("docintel.ocr.run", exc, engine=eng, level="info")
            res = None
        if res is not None and res[0].strip():
            return (res[0], res[1], eng)
    return ("", 0.0, "none")


def available() -> bool:
    """Cheap check used by the image extractor to decide whether to try OCR."""
    if not enabled():
        return False
    pref = _engine_pref()
    if pref in ("paddle", "auto"):
        if _get_paddle() is not None:
            return True
    if pref in ("tesseract", "auto"):
        return _TESS_OK is not False
    return False
