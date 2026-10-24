"""What SYRIX durably knows about the person it works for.

One file of plain JSON, readable and editable by hand, under the runtime
state directory. The runtime this replaces had six memory implementations
behind one facade — a JSON+NPY semantic store, three separate Chroma
databases, a knowledge folder, an Obsidian vault and a trading store —
which between them meant nobody could say what SYRIX remembered or where it
came from.

Every fact records when it was learned and from what, because a fact
without provenance cannot be judged, corrected or aged. "Flying to Lisbon in
March" has to become "went to Lisbon, March 2027" once March passes,
and that is only possible if the moment it was learned is on the record.
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from .. import state

_FILE = "facts.json"
_DIR = "memory"

# What a fact is about. Kept small on purpose: a taxonomy nobody can apply
# consistently is worse than none.
KIND_PERSON = "person"       # who the user is, where the user is, how to address the user
KIND_PREFERENCE = "pref"     # how the user likes things done
KIND_PROJECT = "project"     # what the user is working on
KIND_SETUP = "setup"         # machines, tools, accounts, constraints
KIND_EVENT = "event"         # something with a date attached
KIND_OTHER = "other"

KINDS = (KIND_PERSON, KIND_PREFERENCE, KIND_PROJECT, KIND_SETUP, KIND_EVENT, KIND_OTHER)


@dataclass
class Fact:
    """One durable statement, with where it came from and when."""

    text: str
    kind: str = KIND_OTHER
    learned_at: float = field(default_factory=time.time)
    # "you said so" / "noticed in conversation" / "consolidated from N turns"
    source: str = "conversation"
    # Bumped whenever the fact is used or re-confirmed; drives which of two
    # overlapping facts survives consolidation.
    last_used: float = 0.0
    uses: int = 0
    # Set when the fact describes something that will stop being true.
    expires_at: float = 0.0
    id: str = ""

    def __post_init__(self) -> None:
        self.text = " ".join(str(self.text or "").split())
        if self.kind not in KINDS:
            self.kind = KIND_OTHER
        if not self.id:
            self.id = fingerprint(self.text)

    @property
    def stale(self) -> bool:
        return bool(self.expires_at) and time.time() > self.expires_at

    def age_days(self) -> float:
        return max(0.0, (time.time() - self.learned_at) / 86400.0)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def fingerprint(text: str) -> str:
    """Identity by meaning-ish: case, punctuation and filler do not change it.

    Not semantic, deliberately. Two genuinely different phrasings of the
    same fact are merged during consolidation, where a model can judge it;
    this only stops the same sentence being stored twice because it was
    typed with a different comma.
    """
    normalised = re.sub(r"[^a-z0-9 ]+", " ", str(text or "").lower())
    normalised = " ".join(normalised.split())
    return hashlib.sha1(normalised.encode("utf-8")).hexdigest()[:12]


def _load() -> Dict[str, Dict[str, Any]]:
    raw = state.read(_FILE, _DIR, default={})
    facts = raw.get("facts") if isinstance(raw, dict) else None
    return facts if isinstance(facts, dict) else {}


def _save(facts: Dict[str, Dict[str, Any]]) -> bool:
    return state.write({"version": 1, "facts": facts, "saved_at": time.time()},
                       _FILE, _DIR)


def all_facts(*, include_stale: bool = False) -> List[Fact]:
    out: List[Fact] = []
    for row in _load().values():
        try:
            fact = Fact(**row)
        except TypeError:
            continue
        if fact.stale and not include_stale:
            continue
        out.append(fact)
    out.sort(key=lambda f: -f.learned_at)
    return out


def get(fact_id: str) -> Optional[Fact]:
    row = _load().get(str(fact_id or ""))
    if not row:
        return None
    try:
        return Fact(**row)
    except TypeError:
        return None


def remember(
    text: str,
    *,
    kind: str = KIND_OTHER,
    source: str = "conversation",
    expires_at: float = 0.0,
) -> Optional[Fact]:
    """Store a fact. Re-learning one refreshes it rather than duplicating it."""
    fact = Fact(text=text, kind=kind, source=source, expires_at=expires_at)
    if len(fact.text) < 4:
        return None
    facts = _load()
    existing = facts.get(fact.id)
    if existing:
        # Hearing it again is evidence it is still true.
        existing["learned_at"] = time.time()
        existing["source"] = source or existing.get("source", "conversation")
        if expires_at:
            existing["expires_at"] = expires_at
        facts[fact.id] = existing
        _save(facts)
        try:
            return Fact(**existing)
        except TypeError:
            return fact
    facts[fact.id] = fact.as_dict()
    _save(facts)
    return fact


def forget(fact_id: str) -> bool:
    facts = _load()
    if str(fact_id) not in facts:
        return False
    del facts[str(fact_id)]
    return _save(facts)


def replace(old_ids: Iterable[str], fact: Fact) -> Optional[Fact]:
    """Swap several facts for one. Used by consolidation to merge duplicates."""
    facts = _load()
    for old in old_ids:
        facts.pop(str(old), None)
    facts[fact.id] = fact.as_dict()
    return fact if _save(facts) else None


def mark_used(fact_ids: Iterable[str]) -> None:
    """Record that a fact was actually put in front of the model.

    Use is the only evidence available about whether a fact is worth
    keeping, and consolidation prefers a used fact over an unused one when
    two of them say nearly the same thing.
    """
    facts = _load()
    now = time.time()
    touched = False
    for fact_id in fact_ids:
        row = facts.get(str(fact_id))
        if row is None:
            continue
        row["last_used"] = now
        row["uses"] = int(row.get("uses") or 0) + 1
        touched = True
    if touched:
        _save(facts)


def prune_stale() -> int:
    """Drop facts whose expiry has passed. Returns how many went."""
    facts = _load()
    now = time.time()
    doomed = [k for k, v in facts.items()
              if v.get("expires_at") and now > float(v["expires_at"])]
    for key in doomed:
        del facts[key]
    if doomed:
        _save(facts)
    return len(doomed)


def clear() -> None:
    _save({})
