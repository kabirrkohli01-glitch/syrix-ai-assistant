"""What SYRIX remembers between conversations.

Three layers, all plain JSON under the runtime state directory:

* **Facts** — durable statements about the person, each with when it was
  learned and where from.
* **Threads** — per-conversation state, kept in ``context`` because it is
  part of building a turn rather than part of remembering.
* **Consolidation** — the background pass that notices new facts, merges
  duplicates and ages facts forward from future to past. Never runs inside
  a turn.

The store starts empty by decision. Six overlapping memory implementations
were inherited and none of them could say where a given belief came from;
migrating that would have imported the confusion along with the content.
"""
from __future__ import annotations

from . import consolidate, recall, store
from .store import Fact

__all__ = ["Fact", "consolidate", "recall", "store"]
