"""Reading what the user attached.

A thin front for ``syrix_docintel``, which is kept rather than rebuilt: PDF,
docx, xlsx, pptx, csv, zip and OCR, with the extraction traps already solved
in it — text hiding inside Form XObjects, letter-spaced headings, a word
space arriving as a large negative kern. Rediscovering those would be a
week and would make the reader worse in the meantime.

What is added here is the part that was missing. Attached documents were
grounded into the prompt automatically and there was no way for the model
to go back and look again, so a question about page forty of a report it
had already skimmed produced a guess. This is a tool: it can be called, and
called again with a different question, and it says plainly when it could
not read something rather than failing the turn.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .registry import Tool, ToolContext, ToolResult, _as_list, register

_MAX_DOCS = 6


def _docintel():
    import syrix_docintel  # noqa: WPS433 — kept plumbing, imported late
    return syrix_docintel


def _describe(doc: Any) -> str:
    parts = [f"{doc.name} ({doc.kind or 'file'}"]
    if doc.pages:
        parts.append(f", {doc.pages} pages")
    if doc.sheets:
        parts.append(f", {doc.sheets} sheets")
    if doc.slides:
        parts.append(f", {doc.slides} slides")
    if doc.word_count:
        parts.append(f", ~{doc.word_count:,} words")
    parts.append(")")
    line = "".join(parts)
    if doc.notes:
        # Extraction warnings are part of the answer. A table that came out
        # as a wall of numbers is worth knowing about before quoting it.
        line += " — partial: " + "; ".join(str(n) for n in doc.notes[:3])
    return line


def _run(args: Dict[str, Any], tctx: ToolContext) -> ToolResult:
    doc_ids = _as_list(args.get("doc_ids") or args.get("documents"), _MAX_DOCS)
    question = str(args.get("question") or args.get("query") or tctx.question or "").strip()

    docintel = _docintel()

    if not doc_ids:
        return ToolResult.problem(
            "No document was named. Attachments on this turn are listed in "
            "your prompt with their ids; pass those as 'doc_ids'."
        )

    found = []
    missing: List[str] = []
    for doc_id in doc_ids:
        doc = docintel.get_document(doc_id)
        if doc is None:
            missing.append(doc_id)
        else:
            found.append(doc)

    if not found:
        return ToolResult.problem(
            "None of those documents are loaded: " + ", ".join(missing) +
            ". They may have failed to process, or the ids may be stale."
        )

    try:
        block, citations = docintel.build_grounding(
            [d.id for d in found], question or "summarise this",
            request_id=tctx.request_id,
        )
    except Exception as exc:
        return ToolResult.problem(
            f"Could not read {', '.join(d.name for d in found)}: "
            f"{type(exc).__name__}: {exc}"
        )

    header = "Read " + "; ".join(_describe(d) for d in found)
    if missing:
        header += "\nNot loaded: " + ", ".join(missing)

    if not block:
        return ToolResult(
            content=(
                header + "\n\nNothing in them matched that question. Either "
                "ask about something else in the file, or say that the file "
                "does not cover it — do not answer from memory instead."
            ),
            ok=False,
            detail="no matching passage",
        )

    return ToolResult(
        content=header + "\n\n" + block,
        sources=[{"url": "", "title": c, "provider": "attachment"}
                 for c in (citations or [])],
        detail=f"{len(found)} file(s), {len(block)} chars",
    )


register(Tool(
    name="document",
    description=(
        "Read the files he attached — PDF, Word, Excel, PowerPoint, CSV, "
        "text, code, archives, and scanned images by OCR. Ask it a "
        "question and it returns the passages that answer it, with page "
        "references. Use it before answering anything that could depend on "
        "an attachment, and again with a different question rather than "
        "guessing at a part you have not looked at."
    ),
    parameters={
        "type": "object",
        "properties": {
            "doc_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Ids of the attachments, as listed in your prompt.",
            },
            "question": {
                "type": "string",
                "description": (
                    "What to look for. Be specific — 'the total in the "
                    "revenue table' finds more than 'the document'."
                ),
            },
        },
        "required": ["doc_ids"],
    },
    run=_run,
    kind="tool_call",
    label="Reading the attachment",
))
