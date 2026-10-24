"""Connector background-task scheduler — safe, opt-in, individually toggleable.

Modeled on ``syrix_core_v2.proactive_loop``: one daemon thread wakes every
``_TICK_INTERVAL`` seconds and runs each connector background task whose
interval elapsed AND whose gates all pass:

    connectors_enabled AND connectors_background_enabled (config)
    AND connector is connected
    AND the user toggled this specific task on (default OFF)

Task results surface as Dynamic Island notices and are audited with
actor="scheduler". A failing task never kills the loop; it is reported via
obs and retried on the next interval. When nothing is connected the tick is
a handful of dict reads — effectively free.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

import syrix_obs as obs

from . import audit, events, manager, permissions, registry
from .base import HEALTH_CONNECTED

_TICK_INTERVAL = 30.0
_TASK_BUDGET_S = 30.0

_LOCK = threading.RLock()
_running = False
_thread: Optional[threading.Thread] = None
_config: Dict[str, Any] = {}
_last_run: Dict[str, float] = {}  # "cid.task_key" → ts


def _tick() -> None:
    if not manager.background_enabled(_config):
        return
    now = time.time()
    for cid, inst in registry.loaded_connectors().items():
        if not inst.background_tasks or not permissions.is_connected(cid):
            continue
        if manager.connector_health(cid, _config) != HEALTH_CONNECTED:
            continue
        for task in inst.background_tasks:
            if not permissions.task_enabled(cid, task.key):
                continue
            slot = f"{cid}.{task.key}"
            if now - _last_run.get(slot, 0.0) < max(60.0, float(task.interval_s)):
                continue
            _last_run[slot] = now
            _run_task(cid, inst, task)


def _run_task(cid: str, inst, task) -> None:
    t0 = time.perf_counter()
    try:
        notice = manager._run_budgeted(
            lambda: inst.run_task(task.key, config=dict(_config)),
            timeout_s=_TASK_BUDGET_S, tag=f"{cid}.task.{task.key}",
        )
        ms = (time.perf_counter() - t0) * 1000.0
        audit.record(cid, f"task:{task.key}", actor=audit.ACTOR_SCHEDULER,
                     result=audit.RESULT_OK, latency_ms=ms)
        if notice:
            events.island(str(notice)[:250], tone="info",
                          kind=f"connectors:{cid}:{task.key}",
                          dedupe_seconds=max(600.0, float(task.interval_s)))
    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000.0
        obs.report("connectors.scheduler.task", exc, connector=cid, task=task.key)
        audit.record(cid, f"task:{task.key}", actor=audit.ACTOR_SCHEDULER,
                     result=audit.RESULT_ERROR, detail=type(exc).__name__,
                     latency_ms=ms)


def _warm_connected_instances() -> None:
    """Instantiate connectors already marked connected so their tasks can run."""
    try:
        for cid, rec in (permissions.load_state().get("connectors") or {}).items():
            if isinstance(rec, dict) and rec.get("connected"):
                registry.get_connector(cid)
    except Exception as exc:
        obs.report("connectors.scheduler.warm", exc)


def _thread_main() -> None:
    _warm_connected_instances()
    while _running:
        try:
            if manager.is_enabled(_config):
                _tick()
        except Exception as exc:
            obs.report("connectors.scheduler.tick", exc)
        time.sleep(_TICK_INTERVAL)


def start(config: Optional[Dict[str, Any]] = None) -> None:
    """Start the scheduler daemon (idempotent). Zero cost when disabled."""
    global _running, _thread, _config
    _config = dict(config or {})
    if not manager.background_enabled(_config):
        return
    with _LOCK:
        if _running and _thread is not None and _thread.is_alive():
            return
        _running = True
        _thread = threading.Thread(target=_thread_main, daemon=True,
                                   name="syrix-connectors-scheduler")
        _thread.start()


def stop() -> None:
    global _running
    _running = False


__all__ = ["start", "stop"]
