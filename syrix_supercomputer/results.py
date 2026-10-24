"""Results produced by a task — what the Results tab shows.

Deliberately concrete. "I researched the topic" is not a result; a file on disk,
a screenshot, a summary, a diff, or a test run with its exit code is. Every
entry records enough for the user to check the claim themselves, which is the whole
point: a completion report that cannot be verified is just a story.

Results live in memory for the session and are cleared with the chat. Files
recorded here are user-approved exports and are NOT deleted by that cleanup —
only the references to them are.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

KIND_FILE = "file"
KIND_SUMMARY = "summary"
KIND_SCREENSHOT = "screenshot"
KIND_CODE = "code"
KIND_TEST = "test"
KIND_SOURCE = "source"

_LOCK = threading.RLock()
_RESULTS: Dict[str, List["Result"]] = {}


@dataclass
class Result:
    kind: str
    title: str
    task_id: str = ""
    # Where to verify it: a path, a URL, or "" for inline content.
    location: str = ""
    detail: str = ""
    # For KIND_TEST: did it pass? None for kinds where it does not apply.
    passed: Optional[bool] = None
    # For KIND_CODE: lines added/removed, so a diff can be summarised honestly.
    added: int = 0
    removed: int = 0
    ts: float = field(default_factory=time.time)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title[:200],
            "task_id": self.task_id,
            "location": self.location[:400],
            "detail": self.detail[:600],
            "passed": self.passed,
            "added": self.added,
            "removed": self.removed,
            "ts": self.ts,
            "meta": dict(self.meta),
        }


def add(task_id: str, kind: str, title: str, **kw: Any) -> Result:
    r = Result(kind=str(kind), title=str(title or ""), task_id=str(task_id or ""), **kw)
    with _LOCK:
        _RESULTS.setdefault(r.task_id, []).append(r)
    return r


def file_created(task_id: str, path: str, *, detail: str = "") -> Result:
    return add(task_id, KIND_FILE, path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1],
               location=path, detail=detail)


def summary(task_id: str, title: str, text: str) -> Result:
    return add(task_id, KIND_SUMMARY, title, detail=text)


def screenshot(task_id: str, title: str, *, location: str = "") -> Result:
    return add(task_id, KIND_SCREENSHOT, title, location=location)


def _normalise_url(url: str) -> str:
    """Enough normalisation to spot the same page twice, and no more.

    Only fragments, a trailing slash and case in the scheme/host are dropped —
    query strings are load-bearing on plenty of sites, so two URLs differing by
    a query are treated as two pages.
    """
    u = str(url or "").strip()
    if not u:
        return ""
    u = u.split("#", 1)[0]
    head, sep, rest = u.partition("://")
    if sep:
        host, slash, tail = rest.partition("/")
        u = f"{head.lower()}://{host.lower()}{slash}{tail}"
    return u.rstrip("/").lower()


def source(task_id: str, title: str, url: str) -> Result:
    """Record a page the task read. Reading it twice is not two sources.

    The DBS run printed the same URL twice on its Sources line because every
    visit appended a row. A repeat visit is a fact about the run, not about the
    evidence, and it made a one-source answer look corroborated.
    """
    tid = str(task_id or "")
    key = _normalise_url(url)
    if key:
        with _LOCK:
            for r in _RESULTS.get(tid, []):
                if r.kind == KIND_SOURCE and _normalise_url(r.location) == key:
                    # Keep the better title if the first visit had none.
                    if title and not r.title:
                        r.title = str(title)
                    return r
    return add(tid, KIND_SOURCE, title, location=url)


def code_change(task_id: str, path: str, *, added: int = 0, removed: int = 0,
                detail: str = "") -> Result:
    return add(task_id, KIND_CODE, path, location=path, added=added,
               removed=removed, detail=detail)


def test_run(task_id: str, title: str, *, passed: bool, detail: str = "") -> Result:
    return add(task_id, KIND_TEST, title, passed=bool(passed), detail=detail)


def for_task(task_id: str) -> List[Dict[str, Any]]:
    with _LOCK:
        return [r.to_dict() for r in _RESULTS.get(str(task_id or ""), [])]


def recent(limit: int = 60) -> List[Dict[str, Any]]:
    """Everything from this session, newest last."""
    with _LOCK:
        flat: List[Result] = []
        for items in _RESULTS.values():
            flat.extend(items)
    flat.sort(key=lambda r: r.ts)
    return [r.to_dict() for r in flat[-max(1, int(limit)):]]


def counts() -> Dict[str, int]:
    out: Dict[str, int] = {}
    with _LOCK:
        for items in _RESULTS.values():
            for r in items:
                out[r.kind] = out.get(r.kind, 0) + 1
    return out


def clear(task_id: str = "") -> int:
    """Drop results. Files on disk are untouched — only the references go."""
    with _LOCK:
        if task_id:
            return len(_RESULTS.pop(str(task_id), []))
        n = sum(len(v) for v in _RESULTS.values())
        _RESULTS.clear()
        return n


def clear_all() -> int:
    """Forget every recorded result. Used by the New chat button.

    Files SYRIX exported to Downloads are untouched — only the references to
    them go, which is the same contract as clearing the chat.
    """
    with _LOCK:
        count = sum(len(v) for v in _RESULTS.values())
        _RESULTS.clear()
    return count


def _reset_for_tests() -> None:
    with _LOCK:
        _RESULTS.clear()


__all__ = [
    "Result", "KIND_FILE", "KIND_SUMMARY", "KIND_SCREENSHOT", "KIND_CODE",
    "KIND_TEST", "KIND_SOURCE", "add", "file_created", "summary", "screenshot",
    "source", "code_change", "test_run", "for_task", "recent", "counts", "clear",
    "clear_all",
]
