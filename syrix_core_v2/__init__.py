"""SYRIX Core V2 — the plumbing the v3 core kept from the previous runtime.

v3 replaced the old reasoning layer and kept four contracts from it: the
free-lane provider pool (``brain_pool``), the thinking-panel event protocol
(``activity_feed``), the reply envelope every interface surface reads
(``response_bus``) and the readiness signal (``readiness``).

This repository carries only those modules. The desktop host process, and the
seam through which it hands a turn to ``syrix_v3.turn.handle``, are not part
of it.
"""

from __future__ import annotations

from .readiness import is_ready, mark_ready, mark_starting, status as readiness_status
from .response_bus import ReplyEnvelope, ReplyStatus

__all__ = [
    "ReplyEnvelope",
    "ReplyStatus",
    "is_ready",
    "mark_ready",
    "mark_starting",
    "readiness_status",
]

__version__ = "3.0.0"
