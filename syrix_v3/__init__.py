"""SYRIX v3 — the turn engine.

One entry point, ``turn.handle(text) -> ReplyEnvelope``, reached by typed
chat, by Voice Mode and by the probe. Everything between a message arriving
and an answer leaving lives in this package.

The three modes are budgets over one loop, not three engines. A mode answers
as itself and never runs another mode's work: ``budget.py`` holds the whole
difference between them.

Model calls go through ``lanes``, v3's own transport: measured free lanes,
raced rather than rotated, admitted against each provider's reported limits,
streamed as they answer, with the local model as the floor.

Three things outside this package are contract rather than implementation and
are used, not reimplemented:

    syrix_core_v2.activity_feed the thinking panel's event protocol
    syrix_core_v2.response_bus  the envelope every HUD surface reads
    syrix_web                   the one governed door to the network
"""
from __future__ import annotations

__all__ = ["turn"]
