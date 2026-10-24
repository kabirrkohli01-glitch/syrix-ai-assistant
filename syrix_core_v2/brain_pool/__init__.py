"""SYRIX free brain pool — Phase 1 of the master plan.

Public surface:
    configure(cfg, log_fn)   — build/rebuild the singleton from CONFIG
    get_pool()               — the singleton (None until configured)
    chat(...) / stream(...)  — convenience passthroughs
    status()                 — health/quota snapshot for the HUD brain panel

``syrix_host_brain`` delegates here when CONFIG["brain_pool_enabled"] is true; the
legacy Groq→Ollama chain remains the fallback when the pool raises.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Optional

from .pool import BrainPool, PoolExhausted, PoolResult  # noqa: F401
from .providers import (  # noqa: F401
    T0_LOCAL,
    T1_FAST,
    T2_BALANCED,
    T3_STRONG,
    normalize_tier,
)

_lock = threading.Lock()
_pool: Optional[BrainPool] = None


def configure(cfg: Dict[str, Any], log_fn: Optional[Callable[[str], None]] = None) -> BrainPool:
    global _pool
    with _lock:
        _pool = BrainPool(cfg, log_fn)
        _pool.sync_models_async()
        return _pool


def get_pool() -> Optional[BrainPool]:
    return _pool


def chat(tier: str, messages, **kwargs) -> PoolResult:
    pool = _pool
    if pool is None:
        raise PoolExhausted("brain pool not configured")
    return pool.chat(tier, messages, **kwargs)


def stream(tier: str, messages, **kwargs):
    pool = _pool
    if pool is None:
        raise PoolExhausted("brain pool not configured")
    return pool.stream(tier, messages, **kwargs)


def has_remote_lane(tier: str, est_tokens: int = 4000,
                    reserve: float = 0.0) -> bool:
    """True when ``tier`` still has a non-local lane with budget and no cooldown.

    Unconfigured pool → True, so a caller that asks never routes itself
    *worse* than it would have without asking.
    """
    pool = _pool
    if pool is None:
        return True
    return pool.has_remote_lane(tier, est_tokens, reserve)


def status() -> Dict[str, Any]:
    pool = _pool
    if pool is None:
        return {"configured": False, "providers": []}
    out = pool.status()
    out["configured"] = True
    return out
