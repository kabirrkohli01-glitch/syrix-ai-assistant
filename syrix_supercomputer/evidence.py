"""Everything a task established, kept outside the transcript.

The failure this exists to end
------------------------------
A long task is one conversation, and ``loop._trim`` keeps ten messages and two
full page digests — it has to, or every step re-sends twenty thousand tokens.
So on a task with ten subjects, the findings for subject one are gone by
subject four, and the final write-up is composed from whatever happened to be
at the tail of the transcript. Measured live: a four-way voice-API comparison
opened fourteen pages, correctly identified all four products, and still could
not produce the comparison.

:mod:`syrix_supercomputer.memory` already keeps a *brief* — but it is a brief:
capped at 24 facts and rendered inside 4,000 characters, deliberately, because
its job is to be replayed into a prompt on every resume. Ten stocks with four
readings each is 40 findings that must all survive and must be retrievable by
subject. That is a different job, so this is a different store.

What it is
----------
An append-only record of findings, each attributed to the stage that found it
and the page it was read off. Persisted next to the task so a resume continues
instead of re-deriving. The final writer reads **the whole store**, grouped by
stage, rather than the tail of a conversation.

What it is NOT
--------------
Not authority. A finding cannot grant a permission, widen a scope or approve an
action — those live in :mod:`syrix_supercomputer.permissions`, stay in memory
and still die with ``boot_id``. Everything here originated as untrusted page
content and is re-fenced by :func:`render` on the way back into a prompt, for
the same reason ``memory.render`` does it: recovering a fact from storage must
not launder it into an instruction.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, List, Optional

import syrix_obs as obs

from . import paths

# Kinds of finding. Anything else is stored as a plain fact.
KIND_FACT = "fact"
KIND_FILE = "file"
KIND_GAP = "gap"          # something looked for and not found — stated, never dropped
KIND_FIGURE = "figure"    # a number, price, level, score
KINDS = (KIND_FACT, KIND_FILE, KIND_GAP, KIND_FIGURE)

# Caps. Much larger than the brief's, because this is never sent whole into a
# step prompt — only the relevant stage is, and the writer gets it once.
MAX_PER_STAGE = 60
MAX_TOTAL = 400
MAX_CLAIM = 600
MAX_SOURCE = 300
# Ceiling on one render, so a writer prompt cannot become the thing that
# starves the pool it needs.
RENDER_CHARS = 14000

_LOCK = threading.RLock()
_STORES: Dict[str, "TaskEvidence"] = {}


def enabled(config: Optional[Dict[str, Any]] = None) -> bool:
    return bool((config or {}).get("supercomputer_evidence_enabled", True))


def _clip(text: Any, limit: int) -> str:
    return str(text or "").strip()[:limit]


@dataclass
class Finding:
    claim: str
    source: str = ""
    stage: str = ""
    kind: str = KIND_FACT
    ts: float = field(default_factory=time.time)

    def line(self) -> str:
        body = self.claim
        if self.kind == KIND_GAP:
            body = f"NOT FOUND: {body}"
        return f"- {body}" + (f"  [source: {self.source}]" if self.source else "")


@dataclass
class TaskEvidence:
    task_id: str
    goal: str = ""
    findings: List[Finding] = field(default_factory=list)
    # Stage id -> human label, so the writer can group by something readable.
    stage_titles: Dict[str, str] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not self.findings

    def stages(self) -> List[str]:
        seen: List[str] = []
        for f in self.findings:
            if f.stage not in seen:
                seen.append(f.stage)
        return seen


def _store(task_id: str) -> Optional[TaskEvidence]:
    with _LOCK:
        return _STORES.get(str(task_id or ""))


def start(task_id: str, goal: str = "") -> TaskEvidence:
    """Open (or reopen) the store for a task."""
    tid = str(task_id or "")
    with _LOCK:
        ev = _STORES.get(tid)
        if ev is None:
            ev = TaskEvidence(task_id=tid, goal=_clip(goal, 400))
            _STORES[tid] = ev
        elif goal and not ev.goal:
            ev.goal = _clip(goal, 400)
        return ev


def name_stage(task_id: str, stage_id: str, title: str) -> None:
    ev = _store(task_id)
    if ev is None:
        return
    with _LOCK:
        ev.stage_titles[str(stage_id or "")] = _clip(title, 140)


def add(task_id: str, claim: str, *, source: str = "", stage: str = "",
        kind: str = KIND_FACT) -> bool:
    """Record one finding. Duplicates within a stage are dropped.

    Returns False when there was nothing to record or the store is full, so a
    caller can tell "stored" from "silently ignored".
    """
    ev = _store(task_id)
    body = _clip(claim, MAX_CLAIM)
    if ev is None or not body:
        return False
    src = _clip(source, MAX_SOURCE)
    sid = str(stage or "")
    with _LOCK:
        if len(ev.findings) >= MAX_TOTAL:
            return False
        same_stage = [f for f in ev.findings if f.stage == sid]
        if len(same_stage) >= MAX_PER_STAGE:
            return False
        for existing in same_stage:
            if existing.claim == body and existing.source == src:
                return False
        ev.findings.append(Finding(
            claim=body, source=src, stage=sid,
            kind=str(kind if kind in KINDS else KIND_FACT)))
    return True


def add_gap(task_id: str, what: str, *, stage: str = "", why: str = "") -> bool:
    """Record something looked for and NOT found.

    Stored as evidence deliberately: a gap that is written down gets reported,
    and a gap that lives only in the agent's head gets quietly dropped from the
    write-up. That silent dropping is the whole reason the coverage gate exists.
    """
    body = _clip(what, MAX_CLAIM)
    if why:
        body = f"{body} — {_clip(why, 200)}"
    return add(task_id, body, stage=stage, kind=KIND_GAP)


def for_stage(task_id: str, stage_id: str) -> List[Finding]:
    ev = _store(task_id)
    if ev is None:
        return []
    with _LOCK:
        return [f for f in ev.findings if f.stage == str(stage_id or "")]


def all_findings(task_id: str) -> List[Finding]:
    ev = _store(task_id)
    if ev is None:
        return []
    with _LOCK:
        return list(ev.findings)


def gaps(task_id: str) -> List[str]:
    return [f.claim for f in all_findings(task_id) if f.kind == KIND_GAP]


def sources(task_id: str) -> List[str]:
    out: List[str] = []
    for f in all_findings(task_id):
        if f.source and f.source not in out:
            out.append(f.source)
    return out


def count(task_id: str) -> int:
    return len(all_findings(task_id))


def render(task_id: str, *, stage_id: str = "", header: str = "",
           limit: int = RENDER_CHARS) -> str:
    """The store as prompt text, grouped by stage and fenced as page content.

    ``stage_id`` renders only that stage — what a worker needs while it is
    running. Empty renders everything, which is what the final writer gets, and
    is the entire point: the write-up is composed from what the task
    established, not from the tail of a conversation.
    """
    ev = _store(task_id)
    if ev is None or ev.is_empty():
        return ""
    with _LOCK:
        wanted = [f for f in ev.findings
                  if not stage_id or f.stage == str(stage_id)]
        titles = dict(ev.stage_titles)
    if not wanted:
        return ""

    lines: List[str] = []
    for sid in _ordered_stages(wanted):
        rows = [f for f in wanted if f.stage == sid]
        if not rows:
            continue
        label = titles.get(sid) or sid or "General"
        lines.append(f"\n{label}:")
        lines.extend(f.line() for f in rows)

    body = "\n".join(lines).strip()
    if len(body) > max(500, int(limit)):
        body = body[:max(500, int(limit))].rsplit("\n", 1)[0] + "\n  …(truncated)"
    title = header or "WHAT THIS TASK HAS ESTABLISHED"
    return _fence(f"{title}\n{body}")


def _ordered_stages(findings: Iterable[Finding]) -> List[str]:
    seen: List[str] = []
    for f in findings:
        if f.stage not in seen:
            seen.append(f.stage)
    return seen


def _fence(text: str) -> str:
    """Re-fence as untrusted content. A fact recovered from storage is no more
    trusted than it was when it was read off the page."""
    with obs.swallow("supercomputer.evidence.fence"):
        from .browser import injection_guard as guard

        return guard.wrap(text)
    return text


# ── persistence ────────────────────────────────────────────────────────────
def _path(task_id: str) -> str:
    return paths.task_evidence_path(task_id)


def save(task_id: str, *, config: Optional[Dict[str, Any]] = None) -> bool:
    """Write the store beside the task. Work product only — never authority."""
    if not enabled(config):
        return False
    ev = _store(task_id)
    if ev is None or ev.is_empty():
        return False
    with obs.swallow("supercomputer.evidence.save", task_id=str(task_id)):
        path = _path(task_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with _LOCK:
            payload = {
                "task_id": ev.task_id, "goal": ev.goal,
                "stage_titles": dict(ev.stage_titles),
                "findings": [asdict(f) for f in ev.findings],
                "saved_at": time.time(),
            }
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)
        return True
    return False


def load(task_id: str, *, config: Optional[Dict[str, Any]] = None) -> bool:
    """Bring a task's evidence back after a restart or a permission halt."""
    if not enabled(config):
        return False
    with obs.swallow("supercomputer.evidence.load", task_id=str(task_id)):
        with open(_path(task_id), "r", encoding="utf-8") as fh:
            data = json.load(fh) or {}
        ev = TaskEvidence(task_id=str(data.get("task_id") or task_id),
                          goal=_clip(data.get("goal"), 400))
        ev.stage_titles = {str(k): _clip(v, 140)
                           for k, v in (data.get("stage_titles") or {}).items()}
        for row in (data.get("findings") or [])[:MAX_TOTAL]:
            if not isinstance(row, dict):
                continue
            ev.findings.append(Finding(
                claim=_clip(row.get("claim"), MAX_CLAIM),
                source=_clip(row.get("source"), MAX_SOURCE),
                stage=str(row.get("stage") or ""),
                kind=str(row.get("kind") or KIND_FACT),
                ts=float(row.get("ts") or time.time()),
            ))
        with _LOCK:
            _STORES[str(task_id or "")] = ev
        return True
    return False


def forget(task_id: str) -> bool:
    with _LOCK:
        return _STORES.pop(str(task_id or ""), None) is not None


def forget_all() -> int:
    with _LOCK:
        n = len(_STORES)
        _STORES.clear()
        return n


def snapshot(task_id: str) -> Dict[str, Any]:
    """Small view for the HUD: how much has been gathered, and from where."""
    findings = all_findings(task_id)
    return {
        "findings": len(findings),
        "gaps": sum(1 for f in findings if f.kind == KIND_GAP),
        "sources": len(sources(task_id)),
        "stages": len({f.stage for f in findings}),
    }


__all__ = [
    "KIND_FACT", "KIND_FILE", "KIND_GAP", "KIND_FIGURE",
    "Finding", "TaskEvidence", "enabled", "start", "name_stage",
    "add", "add_gap", "for_stage", "all_findings", "gaps", "sources", "count",
    "render", "save", "load", "forget", "forget_all", "snapshot",
]
