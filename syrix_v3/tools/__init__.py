"""The tools SYRIX can call, and the machinery that runs them.

Deliberately few and deliberately non-overlapping. A bloated tool set is the
most reliable way to make a capable model pick badly: if two tools could
plausibly serve the same request, the model has to guess, and it guesses
differently on identical inputs. The previous runtime had fifteen registered
functions, twenty-seven dispatch adapters and a separate MCP bridge, with
substantial overlap between all three.
"""
from __future__ import annotations

from .registry import Tool, ToolContext, ToolResult, get, names, run_round, schemas

# Importing a tool module registers it. Kept here so any importer of the
# package sees the same set, rather than each caller remembering which
# modules to pull in.
from . import calculate  # noqa: E402,F401
from . import docs  # noqa: E402,F401
from . import look  # noqa: E402,F401
from . import memory_tool  # noqa: E402,F401
from . import self_knowledge  # noqa: E402,F401
from . import web  # noqa: E402,F401

__all__ = [
    "Tool", "ToolContext", "ToolResult",
    "get", "names", "run_round", "schemas",
]
