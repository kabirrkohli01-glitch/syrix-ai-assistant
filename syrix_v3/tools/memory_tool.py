"""Remembering and recalling, when the model asks for it explicitly.

Most recall happens without a tool call: what is relevant to the question
is already in the prompt before the model sees it. This exists for the two
cases that need an explicit act — being told to remember something, and
going looking for something the automatic recall did not surface.

Named ``memory_tool`` rather than ``memory`` so the module cannot shadow
the ``syrix_v3.memory`` package it depends on. That exact collision cost a
debugging session once already, when a re-exported function shadowed its
own submodule and a tool reported itself broken.
"""
from __future__ import annotations

from typing import Any, Dict

from ..memory import recall as _recall
from ..memory import store as _store
from .registry import Tool, ToolContext, ToolResult, register

_MAX_RECALL = 12
_MAX_SAVE = 8


def _as_facts(value: Any) -> list:
    """Accept one statement or several, however the model sends them."""
    if isinstance(value, (list, tuple)):
        items = [str(v) for v in value]
    else:
        items = [str(value or "")]
    out = []
    for item in items:
        cleaned = " ".join(item.split())
        if cleaned and cleaned not in out:
            out.append(cleaned)
        if len(out) >= _MAX_SAVE:
            break
    return out


def _run(args: Dict[str, Any], tctx: ToolContext) -> ToolResult:
    action = str(args.get("action") or "recall").strip().lower()
    text = str(args.get("text") or args.get("query") or "").strip()

    if action in ("save", "remember", "store"):
        # One message often carries several facts — "I take my coffee black
        # and I only trade the London open" is two — and a tool that stores
        # one string quietly keeps the first and loses the rest.
        wanted = _as_facts(args.get("text") or args.get("facts") or text)
        if not wanted:
            return ToolResult.problem(
                "Nothing to save. Pass 'text' as a statement, or a list of "
                "them: [\"He takes his coffee black.\", \"He trades the "
                "London open.\"]")
        kind = str(args.get("kind") or _store.KIND_OTHER)
        saved = [
            fact.text for fact in (
                _store.remember(one, kind=kind,
                                source="he asked you to remember it")
                for one in wanted
            ) if fact is not None
        ]
        if not saved:
            return ToolResult.problem("Those were too short to be worth storing.")
        return ToolResult(
            content="Saved:\n" + "\n".join(f"- {t}" for t in saved),
            detail="; ".join(saved)[:120],
        )

    if action in ("forget", "delete"):
        if not text:
            return ToolResult.problem("Pass the exact fact text to forget.")
        removed = _store.forget(_store.fingerprint(text))
        return ToolResult(
            content="Forgotten." if removed else "Nothing stored matched that.",
            ok=removed,
        )

    # Recall.
    if not text:
        facts = _store.all_facts()[:_MAX_RECALL]
        if not facts:
            return ToolResult(content="Nothing is stored about him yet.")
        listing = "\n".join(f"- {f.text}" for f in facts)
        return ToolResult(content=f"Everything stored ({len(facts)}):\n{listing}")

    found = _recall.relevant(text, limit=_MAX_RECALL)
    if not found:
        return ToolResult(content=f"Nothing stored about {text!r}.")
    listing = "\n".join(f"- {f.text}" for f, _ in found)
    return ToolResult(content=f"About {text!r}:\n{listing}",
                      detail=f"{len(found)} facts")


register(Tool(
    name="memory",
    description=(
        "Remember something about him, recall what is already known, or "
        "forget something that is wrong. What is relevant to the current "
        "question is already in your prompt, so use this to SAVE when he "
        "tells you something worth keeping, or to look for something "
        "specific that is not in front of you."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["recall", "save", "forget"],
                "description": "What to do. Defaults to recall.",
            },
            "text": {
                "description": (
                    "To save: the fact as a short third-person statement, "
                    "or a list of them when one message carried several. "
                    "To recall: what you are looking for. To forget: the "
                    "exact stored wording."
                ),
                "anyOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ],
            },
            "kind": {
                "type": "string",
                "enum": ["person", "pref", "project", "setup", "event", "other"],
                "description": "What the fact is about. Saving only.",
            },
        },
        "required": ["action"],
    },
    run=_run,
    kind="memory",
    label="Checking what I know",
))
