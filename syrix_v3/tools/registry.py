"""Tool definitions, and running a round of them.

Two things this does that the loop should never have to:

**Every call in a round runs at once.** A model that asks for three searches
has asked for three searches, not for the first one; running them in sequence
turns one round into three round trips' worth of latency for no extra
information. They go out together and the round takes as long as the slowest.

**A failure comes back as text the model can act on.** A tool that raises
ends the turn; a tool that returns "no results for that query — the site
refused the request" lets the model try something else. Nothing here throws
into the loop.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..budget import Budget


@dataclass
class ToolContext:
    """What a tool is allowed to know about the turn it is serving."""

    request_id: str
    budget: Budget
    session_id: str = "default"
    question: str = ""
    # Monotonic; 0 means no ceiling. This is the GATHERING deadline, already
    # short of the turn's own by the budget's answer reserve — a tool that
    # spent the last second would leave nothing for the call that writes the
    # answer, and the turn would overrun on the one step it cannot skip.
    deadline_at: float = 0.0

    def remaining(self) -> float:
        if not self.deadline_at:
            return float("inf")
        return max(0.0, self.deadline_at - time.monotonic())


@dataclass
class ToolResult:
    """What the model gets back, and what the answer may cite."""

    content: str
    sources: List[Dict[str, Any]] = field(default_factory=list)
    ok: bool = True
    detail: str = ""

    @classmethod
    def problem(cls, message: str) -> "ToolResult":
        return cls(content=message, ok=False, detail=message[:160])


@dataclass
class Tool:
    name: str
    description: str
    parameters: Dict[str, Any]
    run: Callable[[Dict[str, Any], ToolContext], ToolResult]
    # Ladder wording for the step this tool represents, e.g. "Searching the web".
    kind: str = "tool_call"
    label: str = ""

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


_REGISTRY: Dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    _REGISTRY[tool.name] = tool
    return tool


def get(name: str) -> Optional[Tool]:
    return _REGISTRY.get(str(name or "").strip())


def names(only: Optional[Sequence[str]] = None) -> List[str]:
    if only is None:
        return sorted(_REGISTRY)
    return [n for n in only if n in _REGISTRY]


def schemas(only: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    return [_REGISTRY[n].schema() for n in names(only)]


def parse_arguments(raw: Any) -> Dict[str, Any]:
    """Providers send arguments as a JSON string, an object, or nearly-JSON.

    A model that writes slightly malformed JSON has still told you what it
    wants, so a trailing-comma or single-quote slip is repaired rather than
    thrown away. Anything genuinely unreadable comes back empty and the tool
    reports a usable complaint instead of the loop crashing.
    """
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except ValueError:
        repaired = text.replace("'", '"')
        repaired = repaired.replace(",}", "}").replace(",]", "]")
        try:
            parsed = json.loads(repaired)
        except ValueError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _as_list(value: Any, limit: int) -> List[str]:
    """Accept a list, or the single string a model sends when it forgets."""
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = [str(v) for v in value]
    else:
        items = [str(value)]
    out: List[str] = []
    for item in items:
        cleaned = str(item or "").strip()
        if cleaned and cleaned not in out:
            out.append(cleaned)
        if len(out) >= limit:
            break
    return out


def run_round(
    calls: Sequence[Dict[str, Any]],
    tctx: ToolContext,
    *,
    on_step: Optional[Callable[[str, str, str], None]] = None,
) -> List[Tuple[str, str, ToolResult]]:
    """Run every call in one round concurrently.

    Returns ``(call_id, tool_name, result)`` in the order the calls arrived,
    because the model's next prompt reads better when the observations follow
    the requests.
    """
    if not calls:
        return []

    ordered = list(calls)
    results: Dict[int, Tuple[str, str, ToolResult]] = {}

    def _invoke(index: int, call: Dict[str, Any]) -> None:
        call_id = str(call.get("id") or f"call_{index}")
        name = str(call.get("name") or "")
        tool = get(name)
        if tool is None:
            results[index] = (call_id, name, ToolResult.problem(
                f"There is no tool called {name!r}. Available: {', '.join(names())}."
            ))
            return
        args = parse_arguments(call.get("arguments"))
        if on_step:
            on_step(tool.kind, tool.label or f"Using {tool.name}", json.dumps(args)[:160])
        try:
            results[index] = (call_id, name, tool.run(args, tctx))
        except Exception as exc:
            results[index] = (call_id, name, ToolResult.problem(
                f"{name} failed: {type(exc).__name__}: {exc}"
            ))

    workers = min(len(ordered), 8)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="v3tool") as pool:
        futures = [pool.submit(_invoke, i, call) for i, call in enumerate(ordered)]
        for future in as_completed(futures):
            future.result()

    return [results[i] for i in range(len(ordered)) if i in results]
