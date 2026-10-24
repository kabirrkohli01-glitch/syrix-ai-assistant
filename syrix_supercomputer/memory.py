"""What the agent knows so far — the brief that survives a permission card.

The failure this exists to end
-----------------------------
The loop stops when a card is raised, and :mod:`syrix_supercomputer.resume`
restarts it once the user answers. Until now that restart began with a **blank
transcript**: every page read, every figure found, every file made was gone, and
the agent was told only "the browser is where you left it, read the page".

For a single-permission task that is survivable. For the tasks the user actually
wants — make the API key, then the PDF, then the spreadsheet, then submit the CV
— it is fatal. Such a task raises three to five cards, and the agent lost its
mind at each one, re-derived what it already knew, and burned the step budget
doing it.

So a task now keeps a **brief**: a small, structured record of what it has
established. On resume the brief is replayed into the transcript instead of an
empty slate, and the agent picks up genuinely where it left off.

Why persisting this is safe
---------------------------
The brief is *work product* — pages read, facts found, files written, decisions
taken. It is never *authority*. It cannot grant a permission, widen a scope, or
approve an action; those live in :mod:`syrix_supercomputer.permissions`, stay in
memory, and still die on task end, mode exit and restart. Writing the brief to
disk therefore leaves the permission model exactly as it was.

Everything stored here originated as untrusted page content, so
:func:`render` re-fences it on the way back into a prompt. A fact recovered from
the brief is no more trusted than it was when it was read.

Size discipline
---------------
The 2026-07-25 performance fix (``loop._trim`` / ``loop._compact``) exists
because re-sending full page digests every step cost ~21k tokens per step and
starved the rate-limited pool. The brief must not quietly undo that, so every
list is capped, every string is truncated, and :func:`render` has a hard
character ceiling. A brief is a summary, not a second transcript.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import syrix_obs as obs

from . import paths

# Caps. Chosen so a full brief renders inside ~2.5k characters, which is roughly
# one page digest — affordable once per resume, and nothing like a transcript.
MAX_FACTS = 24
MAX_PAGES = 20
MAX_FILES = 20
MAX_DECISIONS = 16
MAX_ACTIONS = 64
MAX_QUESTIONS = 8
MAX_TEXT = 400
RENDER_CHARS = 4000

_LOCK = threading.RLock()
_BRIEFS: Dict[str, "TaskMemory"] = {}


def enabled(config: Optional[Dict[str, Any]] = None) -> bool:
    return bool((config or {}).get("supercomputer_memory_enabled", True))


def _clip(text: Any, limit: int = MAX_TEXT) -> str:
    body = str(text or "").strip()
    return body[:limit]


@dataclass
class Fact:
    """One thing established, and where it came from.

    ``source`` is the URL the claim was read off. A fact with no source is still
    recorded — the agent's own computation is a legitimate fact — but it is
    rendered as such, so the model can tell "the page said" from "I worked out".
    """

    claim: str
    source: str = ""
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {"claim": self.claim, "source": self.source, "ts": self.ts}


@dataclass
class TaskMemory:
    task_id: str
    goal: str = ""
    plan: List[str] = field(default_factory=list)
    facts: List[Fact] = field(default_factory=list)
    pages_read: List[str] = field(default_factory=list)
    files_made: List[str] = field(default_factory=list)
    # Tool verbs that really completed.  This is deliberately argument-free:
    # it is enough to stop a resumed run forgetting that it already typed,
    # clicked or sent, without persisting message bodies or form contents.
    actions_done: List[str] = field(default_factory=list)
    decisions: List[str] = field(default_factory=list)
    open_questions: List[str] = field(default_factory=list)
    # The last page digest, compacted. Enough for "you were on this page doing
    # this" without re-sending the whole thing.
    last_page: str = ""
    last_url: str = ""
    steps_used: int = 0
    updated_ts: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "plan": list(self.plan),
            "facts": [f.to_dict() for f in self.facts],
            "pages_read": list(self.pages_read),
            "files_made": list(self.files_made),
            "actions_done": list(self.actions_done),
            "decisions": list(self.decisions),
            "open_questions": list(self.open_questions),
            "last_page": self.last_page,
            "last_url": self.last_url,
            "steps_used": self.steps_used,
            "updated_ts": self.updated_ts,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskMemory":
        mem = cls(task_id=str(data.get("task_id") or ""))
        mem.goal = _clip(data.get("goal"), 2000)
        mem.plan = [_clip(s, 200) for s in (data.get("plan") or [])][:12]
        for raw in (data.get("facts") or [])[:MAX_FACTS]:
            if isinstance(raw, dict):
                mem.facts.append(Fact(claim=_clip(raw.get("claim")),
                                      source=_clip(raw.get("source"), 300),
                                      ts=float(raw.get("ts") or time.time())))
        mem.pages_read = [_clip(s, 300) for s in (data.get("pages_read") or [])][:MAX_PAGES]
        mem.files_made = [_clip(s, 300) for s in (data.get("files_made") or [])][:MAX_FILES]
        mem.actions_done = [
            _clip(s, 80) for s in (data.get("actions_done") or [])
        ][:MAX_ACTIONS]
        mem.decisions = [_clip(s) for s in (data.get("decisions") or [])][:MAX_DECISIONS]
        mem.open_questions = [_clip(s) for s in (data.get("open_questions") or [])][:MAX_QUESTIONS]
        mem.last_page = _clip(data.get("last_page"), 1200)
        mem.last_url = _clip(data.get("last_url"), 300)
        try:
            mem.steps_used = int(data.get("steps_used") or 0)
        except (TypeError, ValueError):
            mem.steps_used = 0
        return mem

    def is_empty(self) -> bool:
        return not (self.facts or self.pages_read or self.files_made or self.actions_done
                    or self.decisions or self.last_page)


# ── lifecycle ──────────────────────────────────────────────────────────────
def start(task_id: str, goal: str, plan: Optional[List[str]] = None) -> TaskMemory:
    """Begin (or reset) the brief for a task."""
    mem = TaskMemory(task_id=str(task_id or ""), goal=_clip(goal, 2000),
                     plan=[_clip(s, 200) for s in (plan or [])][:12])
    with _LOCK:
        _BRIEFS[mem.task_id] = mem
    return mem


def get(task_id: str, *, load: bool = True) -> Optional[TaskMemory]:
    """The brief for a task, loading it from disk if it is not in memory.

    ``load`` exists for the resume path: the process may have handled other work
    (or, after a restart, be a different process entirely) between the card being
    raised and the user answering it.
    """
    tid = str(task_id or "")
    with _LOCK:
        mem = _BRIEFS.get(tid)
    if mem is not None or not load:
        return mem
    return _load(tid)


def ensure(task_id: str, goal: str = "") -> TaskMemory:
    """The brief, created empty if there is none. Never returns None."""
    mem = get(task_id)
    if mem is None:
        mem = start(task_id, goal)
    return mem


# ── recording ──────────────────────────────────────────────────────────────
def _dedupe_push(items: List[str], value: str, cap: int) -> None:
    """Append, drop an existing duplicate, and keep the newest ``cap``."""
    body = _clip(value)
    if not body:
        return
    lowered = body.lower()
    for i, existing in enumerate(items):
        if existing.lower() == lowered:
            items.pop(i)
            break
    items.append(body)
    del items[: max(0, len(items) - cap)]


def note_plan(task_id: str, plan: List[str]) -> None:
    mem = ensure(task_id)
    mem.plan = [_clip(s, 200) for s in (plan or [])][:12]
    mem.updated_ts = time.time()


def note_fact(task_id: str, claim: str, *, source: str = "") -> None:
    """Record something established. Duplicate claims replace, not stack."""
    body = _clip(claim)
    if not body:
        return
    mem = ensure(task_id)
    lowered = body.lower()
    for i, existing in enumerate(mem.facts):
        if existing.claim.lower() == lowered:
            mem.facts.pop(i)
            break
    mem.facts.append(Fact(claim=body, source=_clip(source, 300)))
    del mem.facts[: max(0, len(mem.facts) - MAX_FACTS)]
    mem.updated_ts = time.time()


def note_page(task_id: str, url: str, *, digest: str = "") -> None:
    """Record a page as read, and hold its compacted digest as the current one."""
    mem = ensure(task_id)
    _dedupe_push(mem.pages_read, _clip(url, 300), MAX_PAGES)
    if url:
        mem.last_url = _clip(url, 300)
    if digest:
        mem.last_page = _clip(digest, 1200)
    mem.updated_ts = time.time()


def note_file(task_id: str, path: str) -> None:
    mem = ensure(task_id)
    _dedupe_push(mem.files_made, _clip(path, 300), MAX_FILES)
    mem.updated_ts = time.time()


def note_action(task_id: str, action: str) -> None:
    """Remember that an action completed, never its arguments or content."""
    verb = _clip(action, 80).lower()
    if not verb:
        return
    mem = ensure(task_id)
    mem.actions_done.append(verb)
    del mem.actions_done[: max(0, len(mem.actions_done) - MAX_ACTIONS)]
    mem.updated_ts = time.time()


def note_decision(task_id: str, text: str) -> None:
    """A choice worth remembering: a template picked, a route abandoned, an
    approval the user gave. This is what stops a resumed run re-litigating."""
    mem = ensure(task_id)
    _dedupe_push(mem.decisions, text, MAX_DECISIONS)
    mem.updated_ts = time.time()


def note_question(task_id: str, text: str) -> None:
    mem = ensure(task_id)
    _dedupe_push(mem.open_questions, text, MAX_QUESTIONS)
    mem.updated_ts = time.time()


def resolve_question(task_id: str, text: str) -> None:
    """Drop an open question once it has been answered."""
    mem = get(task_id, load=False)
    if mem is None:
        return
    lowered = _clip(text).lower()
    mem.open_questions = [q for q in mem.open_questions if q.lower() != lowered]
    mem.updated_ts = time.time()


def note_steps(task_id: str, steps_used: int) -> None:
    mem = get(task_id, load=False)
    if mem is None:
        return
    # No try/except: a non-numeric step count is a caller bug, and the ratchet
    # is right that swallowing it silently is how those stay invisible.
    if isinstance(steps_used, (int, float)) and not isinstance(steps_used, bool):
        mem.steps_used = max(mem.steps_used, int(steps_used))


# ── rendering into a prompt ────────────────────────────────────────────────
def render(task_id: str, *, header: str = "") -> str:
    """The brief as prompt text, or "" when there is nothing worth replaying.

    Facts and page text came off the web, so the whole block is fenced as
    untrusted exactly like a live page digest. Recovering a fact from memory must
    not launder it into an instruction.
    """
    mem = get(task_id)
    if mem is None or mem.is_empty():
        return ""

    lines: List[str] = []
    if mem.plan:
        lines.append("The plan you were working to:")
        lines += [f"  {i + 1}. {s}" for i, s in enumerate(mem.plan)]
    if mem.facts:
        lines.append("")
        lines.append("What you have already established (do NOT re-derive these):")
        for f in mem.facts[-MAX_FACTS:]:
            lines.append(f"  - {f.claim}" + (f"  [source: {f.source}]" if f.source else "  [your own working]"))
    if mem.decisions:
        lines.append("")
        lines.append("Decisions already taken:")
        lines += [f"  - {d}" for d in mem.decisions[-MAX_DECISIONS:]]
    if mem.files_made:
        lines.append("")
        lines.append("Files you have already made (they exist — do not remake them):")
        lines += [f"  - {p}" for p in mem.files_made[-MAX_FILES:]]
    if mem.actions_done:
        lines.append("")
        lines.append("Actions already completed (do not repeat them):")
        lines += [f"  - {a}" for a in mem.actions_done[-MAX_ACTIONS:]]
    if mem.pages_read:
        lines.append("")
        lines.append("Pages you have already read:")
        lines += [f"  - {u}" for u in mem.pages_read[-MAX_PAGES:]]
    if mem.open_questions:
        lines.append("")
        lines.append("Still open:")
        lines += [f"  - {q}" for q in mem.open_questions[-MAX_QUESTIONS:]]
    if mem.last_page:
        lines.append("")
        lines.append(f"The page you were last on ({mem.last_url or 'unknown url'}):")
        lines.append(f"  {mem.last_page}")

    body = "\n".join(lines)[:RENDER_CHARS]
    if not body.strip():
        return ""

    title = header or "WHAT YOU ALREADY KNOW ON THIS TASK"
    try:
        from .browser import injection_guard as guard

        return guard.wrap(body, title=title)
    except Exception as exc:
        obs.report("supercomputer.memory.render_wrap", exc, level="debug")
        return f"<<<UNTRUSTED_RECALL {title}\n{body}\nUNTRUSTED_RECALL>>>"


def summarise(task_id: str, *, limit: int = 600) -> str:
    """A short plain-text digest of a finished task, for cross-task context.

    Used when the user says "go back to the first one and change X" — the new task
    needs to know what the old one concluded, not replay its whole brief.
    """
    mem = get(task_id)
    if mem is None or mem.is_empty():
        return ""
    bits: List[str] = []
    if mem.goal:
        bits.append(f"Goal: {_clip(mem.goal, 200)}")
    if mem.facts:
        bits.append("Found: " + "; ".join(f.claim for f in mem.facts[-4:]))
    if mem.files_made:
        bits.append("Made: " + ", ".join(os.path.basename(p) for p in mem.files_made[-4:]))
    if mem.open_questions:
        bits.append("Open: " + "; ".join(mem.open_questions[-2:]))
    return " | ".join(bits)[:limit]


# ── persistence ────────────────────────────────────────────────────────────
def save(task_id: str, *, config: Optional[Dict[str, Any]] = None) -> bool:
    """Write the brief to disk. Best effort — a failed save must never end a task."""
    if not enabled(config):
        return False
    mem = get(task_id, load=False)
    if mem is None:
        return False
    path = paths.task_memory_path(mem.task_id)
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(mem.to_dict(), fh, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception as exc:
        obs.report("supercomputer.memory.save", exc, task_id=mem.task_id, level="debug")
        with obs.swallow("supercomputer.memory.save_cleanup"):
            if os.path.exists(tmp):
                os.remove(tmp)
        return False


def _load(task_id: str) -> Optional[TaskMemory]:
    path = paths.task_memory_path(task_id)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        obs.report("supercomputer.memory.load", exc, task_id=task_id, level="debug")
        return None
    if not isinstance(data, dict):
        return None
    mem = TaskMemory.from_dict(data)
    if not mem.task_id:
        mem.task_id = str(task_id or "")
    with _LOCK:
        _BRIEFS[mem.task_id] = mem
    return mem


def forget(task_id: str) -> bool:
    """Drop one brief from memory and disk."""
    tid = str(task_id or "")
    with _LOCK:
        _BRIEFS.pop(tid, None)
    path = paths.task_memory_path(tid)
    try:
        if os.path.isfile(path):
            os.remove(path)
        return True
    except Exception as exc:
        obs.report("supercomputer.memory.forget", exc, task_id=tid, level="debug")
        return False


def forget_all() -> int:
    """Wipe every brief. Called by ``supercomputer_clear_data`` and mode exit."""
    with _LOCK:
        ids = list(_BRIEFS)
        _BRIEFS.clear()
    removed = 0
    with obs.swallow("supercomputer.memory.forget_all"):
        for name in os.listdir(paths.memory_dir()):
            if name.endswith(".json"):
                with obs.swallow("supercomputer.memory.forget_all_one", name=name):
                    os.remove(os.path.join(paths.memory_dir(), name))
                    removed += 1
    return removed or len(ids)


def _reset_for_tests() -> None:
    with _LOCK:
        _BRIEFS.clear()


__all__ = [
    "TaskMemory", "Fact", "enabled", "start", "get", "ensure",
    "note_plan", "note_fact", "note_page", "note_file", "note_decision",
    "note_question", "resolve_question", "note_steps",
    "render", "summarise", "save", "forget", "forget_all",
]
