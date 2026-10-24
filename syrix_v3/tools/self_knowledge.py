"""SYRIX reading its own source.

Asked for directly: *"it will have consciousness like it knows itself, it
knows how it works, read its own code."* Less mystically, this is the
difference between an assistant that guesses at its own behaviour and one
that goes and checks. Asked why it chose Zenith, or what its search lanes
are, or why an answer came back short, it can now open the file that
decides and say what it actually does.

Read-only, and bounded to this project. It can list, search and read; it
cannot write, and nothing outside the SYRIX directory is reachable — a
tool that can read any file on the machine is a different tool with a
different risk, and the question here is only ever about itself.

Secrets are the one thing it will not hand back. ``.env`` holds provider
keys, and a model that can read them can be talked into repeating them.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from .. import config
from .registry import Tool, ToolContext, ToolResult, register

_MAX_BYTES = 60000
_MAX_MATCHES = 40
_MAX_LISTED = 200

# Never read, whatever is asked. Credentials, and the caches that hold them.
_FORBIDDEN = re.compile(
    r"(^|[\\/])(\.env(\..*)?|secrets\.dat|client_secrets\.json|"
    r"\.git[\\/]|token.*\.json|.*credentials.*|.*\.key|.*\.pem)$",
    re.I,
)
# Not source, and enormous.
_SKIP_DIRS = frozenset({
    ".git", "__pycache__", ".venv311", "node_modules", "logs", "data",
    "cache", ".pytest_cache", "dist", "artifacts", ".playwright-mcp",
    "jv_piper_cache", ".piper_tts_cache", "memory_vault", "vision_assets",
})
_SOURCE_SUFFIXES = (".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".html",
                    ".json", ".md", ".txt", ".bat", ".ps1", ".toml", ".cfg")


def _root() -> str:
    return config.root()


def _resolve(relative: str) -> Optional[str]:
    """An absolute path inside the project, or None if it escapes or is barred."""
    candidate = os.path.normpath(os.path.join(_root(), str(relative or "").strip()))
    root = os.path.normpath(_root())
    if not candidate.lower().startswith(root.lower()):
        return None
    if _FORBIDDEN.search(candidate):
        return None
    return candidate


def _walk() -> List[str]:
    out: List[str] = []
    root = _root()
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in files:
            if not name.endswith(_SOURCE_SUFFIXES):
                continue
            full = os.path.join(base, name)
            if _FORBIDDEN.search(full):
                continue
            out.append(os.path.relpath(full, root).replace("\\", "/"))
    return out


def _read(path: str, start: int, count: int) -> ToolResult:
    resolved = _resolve(path)
    if resolved is None:
        return ToolResult.problem(
            f"{path!r} is outside the project or holds credentials. This "
            "reads SYRIX's own source and nothing else."
        )
    if not os.path.isfile(resolved):
        return ToolResult.problem(f"There is no file at {path!r}.")
    try:
        with open(resolved, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError as exc:
        return ToolResult.problem(f"Could not read {path!r}: {exc}")

    total = len(lines)
    first = max(1, int(start or 1))
    last = min(total, first + max(1, int(count or 200)) - 1)
    body = "".join(lines[first - 1:last])[:_MAX_BYTES]
    header = f"{path} — lines {first}-{last} of {total}\n\n"
    return ToolResult(content=header + body,
                      detail=f"{path}:{first}-{last}")


def _search(pattern: str, limit: int) -> ToolResult:
    try:
        matcher = re.compile(pattern, re.I)
    except re.error as exc:
        return ToolResult.problem(f"That is not a valid pattern: {exc}")

    hits: List[str] = []
    for relative in _walk():
        resolved = os.path.join(_root(), relative)
        try:
            with open(resolved, encoding="utf-8", errors="replace") as fh:
                for number, line in enumerate(fh, 1):
                    if matcher.search(line):
                        hits.append(f"{relative}:{number}: {line.strip()[:200]}")
                        if len(hits) >= limit:
                            return ToolResult(
                                content="\n".join(hits) + f"\n\n(stopped at {limit})",
                                detail=f"{len(hits)} matches")
        except OSError:
            continue

    if not hits:
        return ToolResult(content=f"Nothing in the source matches {pattern!r}.")
    return ToolResult(content="\n".join(hits), detail=f"{len(hits)} matches")


def _outline() -> ToolResult:
    """What the project is made of, by directory."""
    counts: Dict[str, List[int]] = {}
    for relative in _walk():
        top = relative.split("/")[0] if "/" in relative else "(root)"
        entry = counts.setdefault(top, [0, 0])
        entry[0] += 1
        try:
            with open(os.path.join(_root(), relative), encoding="utf-8",
                      errors="replace") as fh:
                entry[1] += sum(1 for _ in fh)
        except OSError:
            continue

    rows = sorted(counts.items(), key=lambda kv: -kv[1][1])[:30]
    body = "\n".join(f"{name:28} {files:5} files  {lines:7,} lines"
                     for name, (files, lines) in rows)
    return ToolResult(
        content=("SYRIX's own source, by directory:\n\n" + body +
                 "\n\nsyrix_v3 is the current engine. syrix_core_v2 and the "
                 "the host process is the previous one."),
        detail=f"{len(counts)} directories",
    )


def _run(args: Dict[str, Any], tctx: ToolContext) -> ToolResult:
    action = str(args.get("action") or "outline").strip().lower()

    if action in ("outline", "overview", "structure"):
        return _outline()

    if action in ("list", "files"):
        pattern = str(args.get("pattern") or "").strip().lower()
        files = [f for f in _walk() if not pattern or pattern in f.lower()]
        if not files:
            return ToolResult(content=f"No source file matches {pattern!r}.")
        shown = files[:_MAX_LISTED]
        body = "\n".join(shown)
        if len(files) > len(shown):
            body += f"\n\n({len(files) - len(shown)} more)"
        return ToolResult(content=body, detail=f"{len(files)} files")

    if action in ("search", "grep", "find"):
        pattern = str(args.get("pattern") or "").strip()
        if not pattern:
            return ToolResult.problem("Pass what to search for as 'pattern'.")
        return _search(pattern, min(int(args.get("limit") or _MAX_MATCHES),
                                    _MAX_MATCHES))

    if action in ("read", "open", "show"):
        path = str(args.get("path") or "").strip()
        if not path:
            return ToolResult.problem("Pass the file as 'path', project-relative.")
        return _read(path, int(args.get("start") or 1),
                     int(args.get("lines") or 200))

    return ToolResult.problem(
        f"Unknown action {action!r}. Use outline, list, search or read."
    )


register(Tool(
    name="self_knowledge",
    description=(
        "Read SYRIX's own source code. Use it to answer anything about how "
        "you actually work rather than how you assume you work — why a mode "
        "was chosen, what your search lanes are, what a setting does, why "
        "something behaved the way it did. Start with outline, then search "
        "for the thing, then read the file. Read-only, this project only, "
        "and it will not return credentials."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["outline", "list", "search", "read"],
                "description": (
                    "outline: what the project is made of. list: filenames. "
                    "search: find a pattern across the source. read: open a file."
                ),
            },
            "pattern": {
                "type": "string",
                "description": "For search, a regular expression. For list, part of a path.",
            },
            "path": {
                "type": "string",
                "description": "For read, the project-relative path, e.g. syrix_v3/router.py",
            },
            "start": {"type": "integer", "description": "For read, the first line."},
            "lines": {"type": "integer", "description": "For read, how many lines."},
        },
        "required": ["action"],
    },
    run=_run,
    kind="tool_call",
    label="Reading my own code",
))


def _status(args: Dict[str, Any], tctx: ToolContext) -> ToolResult:
    """What is running right now — engine, lanes, budgets, fallbacks."""
    from .. import status as _status_mod
    try:
        return ToolResult(content=_status_mod.as_text(),
                          detail=_status_mod.one_line())
    except Exception as exc:
        return ToolResult.problem(
            f"Could not read my own runtime state: {type(exc).__name__}: {exc}")


register(Tool(
    name="runtime_status",
    description=(
        "What you are running right now: which engine, which model lanes "
        "are ready, how much of each free allowance is left, whether the "
        "local model is up, and what each mode is allowed to spend. Use it "
        "whenever you are asked what you are running on, why something was "
        "slow, or whether something is available — read it rather than "
        "guessing, and never name a provider you have not checked."
    ),
    parameters={"type": "object", "properties": {}},
    run=_status,
    kind="brain_route",
    label="Checking what I'm running on",
))
