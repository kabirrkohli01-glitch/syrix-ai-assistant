"""Everything the model needs to know before it sees the question.

A turn is not a string. It is a string plus who is asking, what was said
before, what they were talking about, what is attached, and what time it is.
The previous runtime replayed raw history as text and left the rest to be
inferred, which is why a follow-up beginning with a pronoun so often answered
about the wrong thing.

Two decisions here matter more than the rest:

History is **compacted**, not truncated. Dropping the oldest turns to fit a
budget throws away exactly the part that says what was decided, because
decisions happen early and get referred to late. Past the threshold the older
turns are replaced by a summary that keeps the decisions and the open
questions; the recent turns stay verbatim.

The message is passed through **exactly as typed**. Nothing corrects
spelling before the model sees it: a preprocessing pass that rewrites
"wht is nvda doin rn" has to guess, and a wrong guess silently replaces the
question. The model reads the original and the thread together, which is what
disambiguates it.
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import state

# Turns kept verbatim at the end of a thread. Older ones fold into the summary.
_VERBATIM_TURNS = 12
# Characters of raw history past which compaction is worth a model call.
_COMPACT_ABOVE_CHARS = 8000
_THREADS = "threads"
_MAX_STORED_TURNS = 400

_MODEL_MARKER = re.compile(r"^\s*\[\[\s*SYRIX_(?:MODEL|MODE)\s*:\s*[A-Za-z0-9_]+\s*\]\]\s*")


@dataclass
class Attachment:
    kind: str           # "document" | "image"
    ref: str            # doc_id, or a data reference the vision tool resolves
    name: str = ""


@dataclass
class TurnContext:
    """One turn, fully described."""

    text: str
    mode: str
    request_id: str
    session_id: str = "default"
    voice: bool = False
    attachments: List[Attachment] = field(default_factory=list)
    history: List[Dict[str, str]] = field(default_factory=list)
    summary: str = ""
    memory_block: str = ""
    started_at: float = field(default_factory=time.time)

    def elapsed(self) -> float:
        return time.time() - self.started_at

    def remaining(self, deadline_s: float) -> float:
        """Seconds left against a deadline. Zero deadline means no ceiling."""
        if not deadline_s:
            return float("inf")
        return max(0.0, deadline_s - self.elapsed())


def strip_markers(text: str) -> str:
    """Remove the wire's mode prefixes. They are routing, not content."""
    out = str(text or "")
    while True:
        stripped = _MODEL_MARKER.sub("", out, count=1)
        if stripped == out:
            return out.strip()
        out = stripped


def read_marker(text: str) -> str:
    """The model named by a ``[[SYRIX_MODEL:...]]`` prefix, or an empty string.

    An absent marker is not a missing value: it is how the interface sends a
    Spark turn.
    """
    match = re.search(r"\[\[\s*SYRIX_MODEL\s*:\s*([A-Za-z0-9_]+)\s*\]\]", str(text or ""))
    return match.group(1).strip().lower() if match else ""


def deep_research_requested(text: str) -> bool:
    """Whether the Deep Research checkbox was ticked.

    A third, orthogonal control: it arrives as its own ``[[SYRIX_MODE:...]]``
    marker and can accompany any model. Ticking it is an explicit request
    for depth, so it settles the mode regardless of what the switcher says.
    """
    match = re.search(r"\[\[\s*SYRIX_MODE\s*:\s*([A-Za-z0-9_]+)\s*\]\]", str(text or ""))
    return bool(match) and match.group(1).strip().lower() == "deep_research"


def _thread_file(session_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(session_id or "default"))[:64]
    return f"{safe or 'default'}.json"


def load_thread(session_id: str) -> Dict[str, Any]:
    data = state.read(_thread_file(session_id), _THREADS, default={})
    if not isinstance(data, dict):
        return {"turns": [], "summary": ""}
    data.setdefault("turns", [])
    data.setdefault("summary", "")
    return data


def append_turn(session_id: str, role: str, text: str) -> None:
    thread = load_thread(session_id)
    turns = thread["turns"]
    turns.append({"role": role, "content": str(text or ""), "ts": time.time()})
    if len(turns) > _MAX_STORED_TURNS:
        del turns[: len(turns) - _MAX_STORED_TURNS]
    state.write(thread, _thread_file(session_id), _THREADS)


def set_summary(session_id: str, summary: str) -> None:
    thread = load_thread(session_id)
    thread["summary"] = str(summary or "")
    state.write(thread, _thread_file(session_id), _THREADS)


def needs_compaction(history: List[Dict[str, str]]) -> bool:
    if len(history) <= _VERBATIM_TURNS:
        return False
    older = history[:-_VERBATIM_TURNS]
    return sum(len(t.get("content") or "") for t in older) > _COMPACT_ABOVE_CHARS


def split_for_compaction(history: List[Dict[str, str]]):
    """Return (turns to summarise, turns to keep verbatim)."""
    if len(history) <= _VERBATIM_TURNS:
        return [], history
    return history[:-_VERBATIM_TURNS], history[-_VERBATIM_TURNS:]


_COMPACT_PROMPT = """Summarise the earlier part of this conversation so it can
stand in for the turns themselves.

Keep, in plain prose:
- what he asked about, by name, including anything he pasted in full (say what
  it was and quote the few lines that matter — a later message will refer back
  to it as "it" or "the one you just read")
- what was decided, recommended or concluded
- figures, dates and names that came up
- anything still open

Drop pleasantries and phrasing. No preamble, no headings, no bullet list —
write it as a short passage someone can read and then answer a follow-up from.
Never invent anything that is not in the turns below."""


def compact_if_needed(session_id: str) -> bool:
    """Fold the older turns into a summary once the thread outgrows the prompt.

    Wired at last. ``needs_compaction`` and ``set_summary`` existed and nothing
    ever called them, so the docstring at the top of this module described
    behaviour the code did not have: history was simply truncated by the
    transport, which keeps every system message and drops the OLDEST ordinary
    turns to fit. Measured — the user pasted a cover letter, asked two follow-ups, and
    the third answer was "I don't see a cover letter or any previous messages
    from you", because the letter was the oldest turn and had been dropped.

    Runs after the answer has been written, so it never delays a reply, and only
    on the turn that crosses the threshold rather than every turn.
    """
    try:
        thread = load_thread(session_id)
        history = [{"role": t.get("role", "user"), "content": t.get("content", "")}
                   for t in thread.get("turns", [])]
        if not needs_compaction(history):
            return False
        older, _ = split_for_compaction(history)
        if not older:
            return False

        transcript = "\n\n".join(
            f"{t.get('role', 'user')}: {str(t.get('content') or '')[:2500]}"
            for t in older if t.get("content")
        )[:20000]
        existing = str(thread.get("summary") or "")
        if existing:
            transcript = ("Earlier summary:\n" + existing[:2000] + "\n\n"
                          + transcript)

        from . import gateway
        completion = gateway.complete(
            [{"role": "system", "content": _COMPACT_PROMPT},
             {"role": "user", "content": transcript}],
            tier="T2_BALANCED", max_tokens=700, temperature=0.2,
            prompt_chars=24000, timeout_s=8.0,
            allow_local_fallback=False,
        )
        summary = " ".join(str(completion.text or "").split())
        if len(summary) < 40:
            return False
        set_summary(session_id, summary[:4000])
        return True
    except Exception:
        # A thread that could not be summarised is not a failed turn; the
        # answer has already been sent.
        return False


def build(
    raw_text: str,
    *,
    mode: str,
    session_id: str = "default",
    request_id: str = "",
    voice: bool = False,
    doc_ids: Optional[List[str]] = None,
    images: Optional[List[Dict[str, Any]]] = None,
) -> TurnContext:
    thread = load_thread(session_id)
    attachments: List[Attachment] = []
    # The file's own name and shape, not just its id. The prompt used to say
    # `document id "doc:1f2a"` and nothing more, so the model knew a file was
    # there but not whether it was a CV, a spreadsheet or a scan — and could
    # not tell whether "what does page 40 say" was even answerable.
    described: Dict[str, str] = {}
    if doc_ids:
        try:
            from . import attachments as _attachments
            described = _attachments.metadata([str(d) for d in doc_ids])
        except Exception:
            described = {}
    for doc_id in doc_ids or []:
        attachments.append(Attachment(kind="document", ref=str(doc_id),
                                      name=described.get(str(doc_id), "")))
    for i, image in enumerate(images or []):
        ref = str((image or {}).get("frame_id") or (image or {}).get("url") or f"image_{i}")
        attachments.append(Attachment(kind="image", ref=ref, name=str((image or {}).get("name") or "")))

    question = strip_markers(raw_text)
    try:
        from .memory import recall as _recall
        memory_block = _recall.block(question)
    except Exception:
        # Recall failing must never cost the turn its answer.
        memory_block = ""

    return TurnContext(
        text=question,
        memory_block=memory_block,
        mode=mode,
        request_id=str(request_id or uuid.uuid4().hex[:12]),
        session_id=str(session_id or "default"),
        voice=bool(voice),
        attachments=attachments,
        history=[{"role": t.get("role", "user"), "content": t.get("content", "")}
                 for t in thread.get("turns", [])],
        summary=str(thread.get("summary") or ""),
    )


def messages(
    ctx: TurnContext,
    system: str,
    *,
    evidence_block: str = "",
) -> List[Dict[str, Any]]:
    """The message list handed to a lane.

    The summary and the evidence ride in their own system messages rather
    than being glued onto the persona, so each can be budgeted and replaced
    independently. Evidence sits immediately before the question, which is
    where it is most likely to be attended to and least likely to be trimmed.
    """
    out: List[Dict[str, Any]] = [{"role": "system", "content": system}]

    if ctx.summary:
        out.append({
            "role": "system",
            "content": "Earlier in this conversation:\n" + ctx.summary,
        })

    _, verbatim = split_for_compaction(ctx.history)
    for turn in verbatim:
        role = turn.get("role")
        content = str(turn.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            out.append({"role": role, "content": content})

    if ctx.attachments:
        # Named precisely, because the tools address them by id and by
        # position. A model told only that "a file is attached" has to guess
        # what to pass, and guesses an id that does not exist.
        lines = ["Attached to this message:"]
        images = 0
        for item in ctx.attachments:
            if item.kind == "document":
                lines.append(f'- document id "{item.ref}"'
                             + (f' — {item.name}' if item.name else ""))
            else:
                images += 1
                lines.append(f"- image {images}"
                             + (f" — {item.name}" if item.name else ""))
        lines.append("")
        lines.append(
            "Documents are read for you before this turn starts and the "
            "passages are below — answer from those. Call the document tool "
            "only to reach a part of the file the excerpt did not cover, "
            "passing the ids above and a specific question. Images: use the "
            "look tool with the numbers above. Never describe an attachment "
            "you have not actually seen the contents of, and never answer "
            "about the topic in general when he asked about the file."
        )
        out.append({"role": "system", "content": "\n".join(lines)})

    if evidence_block:
        out.append({"role": "system", "content": evidence_block})

    out.append({"role": "user", "content": ctx.text})
    return out
