"""Looking at a picture — a pasted screenshot, a photo, a chart.

Images reach a turn two ways: pasted into the composer, where they arrive
as base64 on the wire, and attached as files, where the document pipeline
OCRs the text but cannot say what the picture shows. Text extracted from a
screenshot of a chart is a handful of axis labels; what the user wants to know is
what the chart is doing.

The images for the current turn are registered by ``turn`` before the model
runs, so the model refers to them by the index it was given rather than by
a path it would have to be told.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Tuple

from .. import vision
from .registry import Tool, ToolContext, ToolResult, register

# request_id -> [(label, mime, base64)]. Cleared when the turn registers a
# new set, so an image can never leak from one turn into the next.
_PENDING: Dict[str, List[Tuple[str, str, str]]] = {}
_LOCK = threading.Lock()


def register_images(request_id: str, images: List[Dict[str, Any]]) -> List[str]:
    """Make this turn's images available to the tool. Returns their labels."""
    prepared: List[Tuple[str, str, str]] = []
    for i, image in enumerate(images or [], 1):
        if not isinstance(image, dict):
            continue
        b64 = str(image.get("b64") or image.get("data") or "").strip()
        if not b64:
            continue
        if b64.startswith("data:") and "," in b64:
            b64 = b64.split(",", 1)[1]
        mime = str(image.get("mime") or "image/png")
        label = str(image.get("name") or f"image {i}")
        prepared.append((label, mime, b64))

    with _LOCK:
        if prepared:
            _PENDING[str(request_id)] = prepared
        else:
            _PENDING.pop(str(request_id), None)
    return [label for label, _, _ in prepared]


def release(request_id: str) -> None:
    with _LOCK:
        _PENDING.pop(str(request_id), None)


def _run(args: Dict[str, Any], tctx: ToolContext) -> ToolResult:
    with _LOCK:
        available = list(_PENDING.get(tctx.request_id) or [])

    if not available:
        return ToolResult.problem(
            "There is no image on this turn. If he attached a document, "
            "read it with the document tool instead."
        )

    question = str(args.get("question") or args.get("prompt")
                   or tctx.question or "Describe this image.").strip()

    wanted = args.get("images")
    if wanted:
        picked = []
        for index in (wanted if isinstance(wanted, (list, tuple)) else [wanted]):
            try:
                position = int(index) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= position < len(available):
                picked.append(available[position])
        chosen = picked or available
    else:
        chosen = available

    sighting = vision.look(
        question,
        [(mime, b64) for _, mime, b64 in chosen],
        timeout_s=min(30.0, max(8.0, tctx.remaining())),
    )

    if not sighting:
        return ToolResult.problem(
            "Could not look at " + ", ".join(label for label, _, _ in chosen)
            + ": " + "; ".join(sighting.problems or ["no lane answered"])
        )

    labels = ", ".join(label for label, _, _ in chosen)
    return ToolResult(
        content=f"Looking at {labels}:\n\n{sighting.text}",
        detail=f"{sighting.provider} · {sighting.latency_ms:.0f}ms",
    )


register(Tool(
    name="look",
    description=(
        "Look at an image on this turn — a pasted screenshot, a photo, a "
        "chart — and describe or read what is in it. Use it whenever an "
        "image is attached and the answer could depend on what it shows. "
        "Text pulled out by OCR is not the same as seeing the picture: a "
        "screenshot of a chart OCRs to a few axis labels."
    ),
    parameters={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": (
                    "What to find out. Be specific — 'what is the trend and "
                    "what are the axis ranges' beats 'describe this'."
                ),
            },
            "images": {
                "type": "array",
                "items": {"type": "integer"},
                "description": (
                    "Which images, by their number as listed in your prompt. "
                    "Omit to look at all of them."
                ),
            },
        },
        "required": ["question"],
    },
    run=_run,
    kind="tool_call",
    label="Looking at the image",
))
