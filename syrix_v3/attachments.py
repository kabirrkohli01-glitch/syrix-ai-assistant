"""Reading what the user attached, before the model is asked anything.

The same decision the web evidence already makes, applied to the thing that is
far more obviously relevant. Web retrieval is prefetched because making it a
tool round trip costs a Spark turn the round it needed to open a page. An
attachment is worse: the user has just handed over a file and asked about it, so the
file IS the evidence, and leaving it behind a tool call means the first model
call is spent deciding to look — and on a weak lane, often not deciding to at
all. Measured before this existed: asking about an uploaded PDF produced a
general answer and a thinking panel full of web searches, because the document
was mentioned in the prompt and nothing had read it.

Two consequences beyond correctness, and both were complaints:

**The panel tells the truth.** Steps are emitted for the real work — reading
the file, looking at the image — instead of a web search that had nothing to
do with the question.

**The file is named.** The prompt used to say `document id "doc:1f2a"` and
nothing else, so the model knew a file existed but not what it was. Now it
gets the name, the type, the page count and the extraction warnings, which is
what lets it decide whether the answer is even in there.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

# Of the evidence budget, the share attachments may take when there is also
# web evidence. An attachment the user chose to send outranks a page a search
# engine chose, so it gets the larger half.
_ATTACHMENT_SHARE = 0.65


@dataclass
class Reading:
    """What was read off the attachments for this turn."""

    block: str = ""
    citations: List[Dict[str, Any]] = field(default_factory=list)
    names: List[str] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)
    took_ms: float = 0.0

    def __bool__(self) -> bool:
        return bool(self.block)


def _docintel():
    import syrix_docintel  # noqa: WPS433 — kept plumbing, imported late
    return syrix_docintel


def describe(doc: Any) -> str:
    """One line naming a file precisely enough to reason about.

    A page count is not decoration: it is how the model knows that "what does
    it say on page 40" is answerable at all, and the extraction notes are how
    it knows not to quote a table that came out as a wall of numbers.
    """
    bits: List[str] = []
    kind = str(getattr(doc, "subtype", "") or getattr(doc, "kind", "") or "file")
    bits.append(kind.upper() if len(kind) <= 4 else kind)
    for attr, unit in (("pages", "page"), ("sheets", "sheet"),
                       ("slides", "slide")):
        count = int(getattr(doc, attr, 0) or 0)
        if count:
            bits.append(f"{count} {unit}{'s' if count != 1 else ''}")
    words = int(getattr(doc, "word_count", 0) or 0)
    if words:
        bits.append(f"~{words:,} words")
    line = f"{getattr(doc, 'name', '') or 'unnamed'} ({', '.join(bits)})"
    notes = list(getattr(doc, "notes", None) or [])
    if notes:
        line += " — read only partly: " + "; ".join(str(n) for n in notes[:2])
    return line


def metadata(doc_ids: List[str]) -> Dict[str, str]:
    """``{doc_id: one-line description}`` for whatever is loaded."""
    out: Dict[str, str] = {}
    try:
        docintel = _docintel()
    except Exception:
        return out
    for doc_id in doc_ids or []:
        try:
            doc = docintel.get_document(str(doc_id))
        except Exception:
            doc = None
        if doc is not None:
            out[str(doc_id)] = describe(doc)
    return out


def _vision_fn():
    """The callable docintel uses to read an image against the question."""
    def _look(blob: bytes, query: str) -> str:
        import base64

        from . import vision
        if not blob:
            return ""
        encoded = base64.b64encode(blob).decode("ascii")
        sighting = vision.look(
            query or "Describe this image in full, including any text in it.",
            [("image/png", encoded)],
            timeout_s=25.0,
        )
        return sighting.text or ""
    return _look


def read(
    doc_ids: List[str],
    question: str,
    *,
    request_id: str = "",
    max_chars: int = 6000,
    on_step: Optional[Callable[[str, str, str], None]] = None,
) -> Reading:
    """Pull the passages that answer ``question`` out of the attachments."""
    result = Reading()
    ids = [str(d) for d in (doc_ids or []) if str(d or "").strip()]
    if not ids:
        return result

    started = time.monotonic()
    try:
        docintel = _docintel()
    except Exception as exc:
        result.problems.append(f"the document reader is unavailable: {exc}")
        return result

    docs = []
    for doc_id in ids:
        try:
            doc = docintel.get_document(doc_id)
        except Exception:
            doc = None
        if doc is None:
            result.problems.append(f"{doc_id} is not loaded")
        else:
            docs.append(doc)

    if not docs:
        return result

    result.names = [str(getattr(d, "name", "") or d.id) for d in docs]
    if on_step:
        on_step("retrieval", "Reading " + ", ".join(result.names)[:120],
                "; ".join(describe(d) for d in docs)[:160])

    try:
        block, citations = docintel.build_grounding(
            [d.id for d in docs],
            question or "summarise this",
            vision_fn=_vision_fn(),
            request_id=request_id,
        )
    except Exception as exc:
        result.problems.append(
            f"could not read {', '.join(result.names)}: "
            f"{type(exc).__name__}: {exc}")
        result.took_ms = round((time.monotonic() - started) * 1000.0, 1)
        return result

    if not block:
        result.problems.append(
            "nothing in the attachment matched the question")
        result.took_ms = round((time.monotonic() - started) * 1000.0, 1)
        return result

    header = [
        "THE FILES HE ATTACHED, ALREADY READ",
        "",
        "Attached: " + "; ".join(describe(d) for d in docs),
        "",
        "This is the attachment itself, not a search result. It is the best "
        "evidence on this turn and the answer comes from it. Quote and cite "
        "by page or sheet where the passages below give one. If the answer to "
        "what he asked is genuinely not in here, say which file you looked in "
        "and that it does not cover it — and do NOT substitute something you "
        "remember about the topic instead.",
        "",
    ]
    body = block[:max_chars]
    if len(block) > max_chars:
        body += ("\n\n(The file is longer than this excerpt. Call the document "
                 "tool with a more specific question to reach the rest.)")
    result.block = "\n".join(header) + body
    result.citations = [
        {"url": "", "title": str(c), "provider": "attachment"}
        for c in (citations or [])
    ]
    result.took_ms = round((time.monotonic() - started) * 1000.0, 1)
    return result


def budget(evidence_chars: int, *, with_web: bool) -> Tuple[int, int]:
    """Split the evidence budget between attachments and web results."""
    total = max(1200, int(evidence_chars))
    if not with_web:
        return total, 0
    mine = int(total * _ATTACHMENT_SHARE)
    return mine, total - mine
