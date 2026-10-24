"""Looking at an image.

Separate from ``gateway`` because the provider pool is text-only by
construction: its transport strips every content block that is not text,
so an image handed to it silently becomes nothing. Rather than teach the
shared transport about images and risk that stripping breaking, vision is
its own two-lane call to the same free providers.

Both lanes speak the OpenAI content-block format, so the only difference
between them is the endpoint and the model id.
"""
from __future__ import annotations

import base64
import mimetypes
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import config

# Roughly 8 MB of base64 is 6 MB of image; past that the request is slower
# than re-encoding would have been and some lanes refuse it outright.
_MAX_IMAGE_BYTES = 6 * 1024 * 1024
_MAX_IMAGES = 4


@dataclass
class Sighting:
    text: str = ""
    provider: str = ""
    model: str = ""
    latency_ms: float = 0.0
    problems: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.text)


def _lanes() -> List[Tuple[str, str, str, Dict[str, str]]]:
    """(name, base_url, model, headers) for each lane that has a key."""
    out: List[Tuple[str, str, str, Dict[str, str]]] = []

    nvidia = config.secret("NVIDIA_API_KEY")
    if nvidia:
        # Re-measured 2026-09-20, because BOTH previous defaults had been
        # retired and vision was therefore completely dead: every image
        # request answered HTTP 410 Gone, and the only symptom upstream was
        # that asking about a picture gave a vague reply. A vision lane that
        # 410s looks exactly like a model that did not look carefully.
        #   nvidia/llama-3.1-nemotron-nano-vl-8b-v1   410 Gone
        #   microsoft/phi-4-multimodal-instruct       410 Gone
        #   microsoft/phi-3-vision-128k-instruct      404, no endpoint
        #   nvidia/vila                               404, no endpoint
        #   meta/llama-3.2-90b-vision-instruct        40s timeout
        #   nvidia/nemotron-parse-2.0                 200, but it is a layout
        #                                             parser and returns
        #                                             nothing a reader can use
        # The two below both read a test image correctly — colour AND the text
        # drawn in it — at 0.8s and 6.5s.
        model = str(config.get("nvidia_default_vision_model")
                    or "meta/llama-3.2-11b-vision-instruct")
        out.append(("nvidia_nim", "https://integrate.api.nvidia.com/v1",
                    model, {"Authorization": f"Bearer {nvidia}"}))

    # A second NVIDIA multimodal model rather than a second vendor. Gemini
    # was the fallback here and was removed on 2026-09-20 at the owner's
    # instruction: no Google in SYRIX. The omni model answers 503
    # "ResourceExhausted" intermittently, which is why it is the fallback and
    # not the primary.
    if nvidia:
        alt = str(config.get("nvidia_default_multimodal_model")
                  or "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning")
        if alt and alt != model:
            out.append(("nvidia_nim_alt", "https://integrate.api.nvidia.com/v1",
                        alt, {"Authorization": f"Bearer {nvidia}"}))

    return out


def encode(path: str) -> Optional[Tuple[str, str]]:
    """(mime, base64) for a file on disk, or None if it cannot be used."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if size <= 0 or size > _MAX_IMAGE_BYTES:
        return None
    mime = mimetypes.guess_type(path)[0] or "image/png"
    if not mime.startswith("image/"):
        return None
    try:
        with open(path, "rb") as fh:
            return mime, base64.b64encode(fh.read()).decode("ascii")
    except OSError:
        return None


def _content(prompt: str, images: List[Tuple[str, str]]) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    for mime, b64 in images[:_MAX_IMAGES]:
        blocks.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
        })
    return blocks


def look(
    prompt: str,
    images: List[Tuple[str, str]],
    *,
    max_tokens: int = 1500,
    timeout_s: float = 30.0,
) -> Sighting:
    """Ask a vision model about one or more images.

    ``images`` is a list of ``(mime, base64)`` — already encoded, because
    the caller knows whether they came from disk, from a screenshot or
    straight off the wire from the interface.
    """
    if not images:
        return Sighting(problems=["no image was supplied"])

    lanes = _lanes()
    if not lanes:
        return Sighting(problems=[
            "no vision lane is configured — NVIDIA_API_KEY would enable one"
        ])

    import requests

    body_messages = [{"role": "user", "content": _content(prompt, images)}]
    problems: List[str] = []

    for name, base_url, model, headers in lanes:
        started = time.perf_counter()
        try:
            resp = requests.post(
                f"{base_url}/chat/completions",
                json={"model": model, "messages": body_messages,
                      "max_tokens": int(max_tokens), "temperature": 0.2},
                headers={"Content-Type": "application/json", **headers},
                timeout=max(5.0, timeout_s),
            )
            if resp.status_code >= 400:
                problems.append(f"{name}: HTTP {resp.status_code}")
                continue
            payload = resp.json()
        except Exception as exc:
            problems.append(f"{name}: {type(exc).__name__}")
            continue

        choices = payload.get("choices") or []
        text = ""
        if choices and isinstance(choices[0], dict):
            text = str((choices[0].get("message") or {}).get("content") or "").strip()
        if not text:
            problems.append(f"{name}: empty reply")
            continue

        return Sighting(
            text=text, provider=name, model=model,
            latency_ms=round((time.perf_counter() - started) * 1000.0, 1),
            problems=problems,
        )

    return Sighting(problems=problems or ["every vision lane refused"])
