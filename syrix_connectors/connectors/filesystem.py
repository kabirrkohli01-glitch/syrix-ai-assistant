"""Local filesystem connector with explicit root containment and read-only tools."""
from __future__ import annotations

import os
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from .. import secure_store
from ..base import (
    HEALTH_AVAILABLE,
    HEALTH_CONNECTED,
    HEALTH_NEEDS_SETUP,
    PERM_READ,
    PERM_SEARCH,
    SCHEMA_VERSION,
    ActionResult,
    ConnectorAction,
    ConnectorBase,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MAX_FILE_BYTES = 1_000_000
_MAX_TEXT_CHARS = 6000
_TEXT_SUFFIXES = {
    ".txt", ".md", ".json", ".jsonl", ".csv", ".tsv", ".py", ".js", ".jsx",
    ".ts", ".tsx", ".css", ".html", ".xml", ".yaml", ".yml", ".toml", ".ini",
    ".log",
}


def _tool(name, description, props=None, required=()):
    return {
        "type": "function",
        "function": {
            "name": name, "description": description,
            "parameters": {
                "type": "object", "properties": dict(props or {}),
                "required": list(required), "additionalProperties": False,
            },
        },
    }


class FilesystemConnector(ConnectorBase):
    id = "filesystem"
    name = "Local Files"
    description = "Read and search explicitly allowed local folders; never writes or deletes"
    icon = "filesystem"
    category = "local"
    auth_kind = "local"
    schema_version = SCHEMA_VERSION

    def __init__(self):
        self._actions = OrderedDict((
            ("filesystem_list", ConnectorAction(
                "filesystem_list", "List files", "List files under an allowed local folder.",
                PERM_READ, tool_schema=_tool(
                    "filesystem_list", "List files in a configured local connector root.",
                    {"path": {"type": "string", "description": "Relative path under an allowed root."}},
                ),
            )),
            ("filesystem_search", ConnectorAction(
                "filesystem_search", "Search filenames", "Search filenames under allowed local folders.",
                PERM_SEARCH, tool_schema=_tool(
                    "filesystem_search", "Search filenames in configured local connector roots.",
                    {"query": {"type": "string"}}, ("query",)),
            )),
            ("filesystem_read", ConnectorAction(
                "filesystem_read", "Read text file", "Read one selected text file under an allowed root.",
                PERM_READ, tool_schema=_tool(
                    "filesystem_read", "Read one text file from a configured local connector root.",
                    {"path": {"type": "string"}}, ("path",)),
            )),
        ))

    def actions(self):
        return self._actions

    def setup_hint(self) -> str:
        return "Windows DPAPI is required. Allowed roots use filesystem_connector_roots in config.json."

    def health(self, *, config: Dict[str, Any]) -> str:
        if not secure_store.available():
            return HEALTH_NEEDS_SETUP
        return HEALTH_CONNECTED if secure_store.has_token(self.id) else HEALTH_AVAILABLE

    def connect(self, *, config: Dict[str, Any], progress=None) -> Tuple[bool, str]:
        roots = self._roots(config)
        if not roots:
            return False, "no_allowed_roots"
        if not secure_store.save_token(self.id, {"local_consent": True}):
            return False, "consent_store_failed"
        return True, f"{len(roots)} local root(s)"

    def disconnect(self, *, config: Dict[str, Any]) -> None:
        secure_store.delete_token(self.id)

    def claims_query(self, text: str) -> bool:
        t = str(text or "")
        return bool(
            re.search(r"\b(?:my|local|computer|pc|syrix)\b", t, re.I)
            and re.search(r"\b(?:files?|folders?|documents?|directory|directories)\b", t, re.I)
            and re.search(r"\b(?:show|list|find|search|read|open|check)\b", t, re.I)
            and not re.search(r"\b(?:do\s+not|don['’]?t|never)\b", t, re.I)
        )

    def suggest_action(self, text: str):
        t = str(text or "")
        if not self.claims_query(t):
            return None
        m = re.search(r"\b(?:find|search)\s+(?:for\s+)?([\w ._-]{2,80})", t, re.I)
        return ("filesystem_search", {"query": m.group(1).strip()}) if m else ("filesystem_list", {})

    def execute(self, action_key: str, args: Dict[str, Any], *,
                config: Dict[str, Any], request_id: str = "") -> ActionResult:
        roots = self._roots(config)
        if action_key == "filesystem_list":
            try:
                target = self._resolve(str(args.get("path") or ""), roots, allow_dir=True)
            except (FileNotFoundError, PermissionError, OSError, RuntimeError, ValueError):
                return ActionResult(
                    False, error="path_denied",
                    human_message="That folder is missing or outside the allowed local roots.",
                )
            rows = []
            for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:50]:
                rows.append(f"{'[dir]' if child.is_dir() else '[file]'} {child.name}")
            return ActionResult(True, f"Local files — {target.name or target}\n" + "\n".join(rows),
                                count=len(rows))
        if action_key == "filesystem_search":
            query = str(args.get("query") or "").strip().lower()
            if not query:
                return ActionResult(False, error="missing_query", human_message="Search text is required.")
            hits: List[str] = []
            for root in roots:
                for base, dirs, files in os.walk(root):
                    dirs[:] = [d for d in dirs if d not in {".git", "node_modules", ".venv", ".venv311"}]
                    for name in dirs + files:
                        if query in name.lower():
                            hits.append(str(Path(base, name).relative_to(root)))
                            if len(hits) >= 50:
                                break
                    if len(hits) >= 50:
                        break
            return ActionResult(True, f"Local filename matches ({len(hits)}):\n" + "\n".join(hits),
                                count=len(hits))
        if action_key == "filesystem_read":
            try:
                target = self._resolve(str(args.get("path") or ""), roots, allow_dir=False)
            except (FileNotFoundError, PermissionError, OSError, RuntimeError, ValueError):
                return ActionResult(
                    False, error="path_denied",
                    human_message="That file is missing or outside the allowed local roots.",
                )
            if target.suffix.lower() not in _TEXT_SUFFIXES:
                return ActionResult(False, error="unsupported_file_type",
                                    human_message="Only allowlisted text file types can be read.")
            if target.stat().st_size > _MAX_FILE_BYTES:
                return ActionResult(False, error="file_too_large",
                                    human_message="That file is larger than the 1 MB safety limit.")
            text = target.read_text(encoding="utf-8", errors="replace")[:_MAX_TEXT_CHARS]
            return ActionResult(True, f"{target.name}:\n{text}", count=1)
        return ActionResult(False, error="unknown_action", human_message="Unknown filesystem action.")

    @staticmethod
    def _roots(config: Dict[str, Any]) -> List[Path]:
        raw = config.get("filesystem_connector_roots")
        values: Iterable[Any] = raw if isinstance(raw, list) else [str(_REPO_ROOT)]
        roots = []
        for value in values:
            try:
                root = Path(str(value)).expanduser().resolve(strict=True)
                if root.is_dir():
                    roots.append(root)
            except (OSError, RuntimeError, ValueError):
                continue
        return roots

    @staticmethod
    def _resolve(raw: str, roots: List[Path], *, allow_dir: bool) -> Path:
        if not roots:
            raise FileNotFoundError("no_allowed_roots")
        candidate_text = str(raw or "").strip()
        candidates = []
        supplied = Path(candidate_text) if candidate_text else roots[0]
        if supplied.is_absolute():
            candidates.append(supplied)
        else:
            candidates.extend(root / supplied for root in roots)
        for candidate in candidates:
            try:
                resolved = candidate.resolve(strict=True)
                if not any(resolved == root or root in resolved.parents for root in roots):
                    continue
                if allow_dir and resolved.is_dir():
                    return resolved
                if not allow_dir and resolved.is_file():
                    return resolved
            except (OSError, RuntimeError, ValueError):
                continue
        raise FileNotFoundError("path_outside_allowed_roots_or_missing")


__all__ = ["FilesystemConnector"]
