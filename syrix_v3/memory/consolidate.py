"""The background pass that keeps what SYRIX knows true and small.

Three jobs, none of which may ever run inside a turn:

**Notice.** Read the recent conversation and write down what is durably
true about the person — not what was discussed, what is now known. Doing
this per-turn would put a model call in front of every answer; doing it in
arrears costs nothing the user waits for.

**Merge.** Two phrasings of one fact are one fact. Left alone, a store
accumulates "the user uses a Windows PC", "the user's machine is Windows", "the user is on
Windows 11" and then spends three lines of every prompt saying it.

**Age forward.** A fact about the future stops being true by becoming a
fact about the past. "Flying to Dubai in October" has to become "went to
Dubai, October 2026" once October has gone, or SYRIX is permanently three
months behind. This is the part that a store without timestamps cannot do
at all, and the reason every fact carries when it was learned.

One model call per job, on the fast tier, and only when there is something
to do.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .. import gateway, state
from . import store
from .store import Fact

_MARK_FILE = "consolidation.json"
_DIR = "memory"

# Enough conversation to be worth reading, and not so much that one pass
# has to reason over an afternoon.
_MIN_TURNS = 8
_MAX_TURNS = 60
_MAX_FACTS_PER_PASS = 10

_TIER = "T1_FAST"

_NOTICE_PROMPT = """Read this conversation and write down what is now durably true about the user.

A durable fact is something that will still be true next week and that would
change how you answer a future question: who he is, where he is, how he
wants things done, what he is working on, what hardware or accounts he has,
and dated commitments he has made.

NOT durable, and never write these down:
- anything about the topic being discussed rather than about him
- anything he asked you to do in this conversation
- passwords, keys, card numbers, addresses, medical details
- guesses. If it was not said, it is not a fact.

Return JSON only, no prose:
{"facts": [{"text": "...", "kind": "person|pref|project|setup|event", "expires": "YYYY-MM-DD or null"}]}

"text" is one short sentence in the third person: "He trades the London open."
"expires" is set only when the fact stops being true on a known date.
Return {"facts": []} if nothing durable was said."""

_MERGE_PROMPT = """These stored facts may overlap. Merge any that say the same thing.

Return JSON only:
{"merges": [{"keep": "the single best wording", "replaces": ["id", "id"], "kind": "person|pref|project|setup|event"}]}

Only merge facts that genuinely say the same thing. Two facts about the same
subject that carry different information are two facts. Return {"merges": []}
if none overlap."""

_AGE_PROMPT = """Each of these facts was written about something in the future, and that date has now passed.

Rewrite each one as what is now true, in the past tense. If the fact no
longer says anything useful once the date has gone, mark it for deletion.

Today is {today}.

Return JSON only:
{{"updates": [{{"id": "...", "text": "the rewritten fact, or null to delete"}}]}}"""

# Dates that can be read out of a fact without a model call.
_MONTHS = ("january february march april may june july august september "
           "october november december").split()
_DATE_HINT = re.compile(
    r"\b(" + "|".join(_MONTHS) + r"|\d{1,2}(?:st|nd|rd|th)?\s+\w+|\d{4}-\d{2}-\d{2})\b",
    re.I,
)
_FUTURE_TENSE = re.compile(
    r"\b(will|going to|flying to|travelling to|traveling to|due to|plans to|"
    r"planning to|is to|expects to|upcoming|next (?:week|month|year))\b", re.I)


@dataclass
class Report:
    noticed: List[str] = field(default_factory=list)
    merged: int = 0
    aged: int = 0
    pruned: int = 0
    skipped: str = ""

    def did_anything(self) -> bool:
        return bool(self.noticed or self.merged or self.aged or self.pruned)


def _marks() -> Dict[str, Any]:
    raw = state.read(_MARK_FILE, _DIR, default={})
    return raw if isinstance(raw, dict) else {}


def _set_mark(session_id: str, turns_seen: int) -> None:
    marks = _marks()
    marks[str(session_id)] = {"turns_seen": int(turns_seen), "at": time.time()}
    state.write(marks, _MARK_FILE, _DIR)


def _ask(prompt: str, payload: str, *, max_tokens: int = 1200) -> Dict[str, Any]:
    """One model call that must answer with JSON. Never raises."""
    try:
        completion = gateway.complete(
            [{"role": "system", "content": prompt},
             {"role": "user", "content": payload}],
            tier=_TIER, max_tokens=max_tokens, temperature=0.1,
            prompt_chars=18000, allow_toolless_fallback=True,
        )
    except Exception:
        return {}
    body = (completion.text or "").strip()
    match = re.search(r"\{.*\}", body, re.S)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _expiry_from(value: Any) -> float:
    text = str(value or "").strip()
    if not text or text.lower() in ("null", "none", ""):
        return 0.0
    try:
        return time.mktime(time.strptime(text[:10], "%Y-%m-%d"))
    except Exception:
        return 0.0


def notice(session_id: str, turns: List[Dict[str, str]]) -> List[str]:
    """Write down what the conversation revealed about the user."""
    seen = int((_marks().get(str(session_id)) or {}).get("turns_seen") or 0)
    fresh = turns[seen:]
    if len(fresh) < _MIN_TURNS:
        return []

    window = fresh[-_MAX_TURNS:]
    transcript = "\n".join(
        f"{t.get('role', 'user')}: {str(t.get('content') or '')[:600]}"
        for t in window
    )
    result = _ask(_NOTICE_PROMPT, transcript)
    written: List[str] = []
    for row in (result.get("facts") or [])[:_MAX_FACTS_PER_PASS]:
        if not isinstance(row, dict):
            continue
        fact = store.remember(
            row.get("text") or "",
            kind=str(row.get("kind") or store.KIND_OTHER),
            source=f"noticed in conversation, {time.strftime('%d %b %Y')}",
            expires_at=_expiry_from(row.get("expires")),
        )
        if fact:
            written.append(fact.text)

    _set_mark(session_id, len(turns))
    return written


def merge_duplicates() -> int:
    """Fold facts that say the same thing into one."""
    facts = store.all_facts()
    if len(facts) < 4:
        return 0
    listing = "\n".join(f"{f.id}: {f.text}" for f in facts[:120])
    result = _ask(_MERGE_PROMPT, listing)

    merged = 0
    by_id = {f.id: f for f in facts}
    for row in (result.get("merges") or []):
        if not isinstance(row, dict):
            continue
        keep = str(row.get("keep") or "").strip()
        replaces = [str(r) for r in (row.get("replaces") or []) if str(r) in by_id]
        if not keep or len(replaces) < 2:
            continue
        # The survivor inherits the oldest learning date — that is when this
        # became true — and the highest use count, which is the evidence
        # that it was worth keeping.
        originals = [by_id[r] for r in replaces]
        fact = Fact(
            text=keep,
            kind=str(row.get("kind") or originals[0].kind),
            learned_at=min(f.learned_at for f in originals),
            source="consolidated from " + str(len(originals)) + " notes",
            uses=max(f.uses for f in originals),
            expires_at=max(f.expires_at for f in originals),
        )
        if store.replace(replaces, fact):
            merged += len(replaces) - 1
    return merged


def _needs_ageing(fact: Fact) -> bool:
    if fact.expires_at and time.time() > fact.expires_at:
        return True
    if not _DATE_HINT.search(fact.text):
        return False
    return bool(_FUTURE_TENSE.search(fact.text)) and fact.age_days() > 30


def age_forward() -> int:
    """Rewrite facts about a future that has now happened."""
    candidates = [f for f in store.all_facts(include_stale=True) if _needs_ageing(f)]
    if not candidates:
        return 0

    listing = "\n".join(f"{f.id}: {f.text} (written {time.strftime('%d %b %Y', time.localtime(f.learned_at))})"
                        for f in candidates[:40])
    result = _ask(_AGE_PROMPT.format(today=time.strftime("%d %B %Y")), listing)

    changed = 0
    by_id = {f.id: f for f in candidates}
    for row in (result.get("updates") or []):
        if not isinstance(row, dict):
            continue
        original = by_id.get(str(row.get("id") or ""))
        if original is None:
            continue
        text = row.get("text")
        if text in (None, "", "null"):
            if store.forget(original.id):
                changed += 1
            continue
        replacement = Fact(
            text=str(text), kind=original.kind,
            learned_at=original.learned_at,
            source="aged forward " + time.strftime("%d %b %Y"),
            uses=original.uses,
        )
        if store.replace([original.id], replacement):
            changed += 1
    return changed


def run(session_id: str = "", turns: Optional[List[Dict[str, str]]] = None) -> Report:
    """One full pass. Safe to call whenever the machine is idle."""
    report = Report()
    try:
        if session_id and turns:
            report.noticed = notice(session_id, turns)
        report.merged = merge_duplicates()
        report.aged = age_forward()
        report.pruned = store.prune_stale()
    except Exception as exc:
        report.skipped = f"{type(exc).__name__}: {exc}"
    return report
