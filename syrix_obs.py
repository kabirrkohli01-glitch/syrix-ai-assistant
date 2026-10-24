"""SYRIX observability sink — make swallowed errors visible.

Top-level module (zero heavy deps) so *any* layer can import it cheaply —
including low-level voice/audio modules where pulling in the ``syrix_core_v2``
package would be circular (its adapters import ``syrix_host``, which imports
the audio layer). ``syrix_core_v2/obs.py`` re-exports from here for callers
already inside that package.

Background (2026-06-19 observability sweep): an audit found ~3,200 silent
``except Exception: pass`` blocks across the codebase. Each one hides a real
failure until it cascades into something a user notices, which is the root
cause of SYRIX's perpetual firefighting loop (voice crash loops, NoneType
``close``, swallowed ``TypeError`` killing audio, ...). The fix is *not* to
remove the swallows — degrading gracefully is correct — but to stop them being
*silent*. Every caught-and-degraded exception in a hot path should leave one
searchable line behind.

This module is the single sink for that. It routes through the existing
``syrix_host_log.log_line`` so the line is (a) redacted for secrets, (b) teed into
the in-memory ring the HUD per-message inspector reads, and (c) written to the
rotating ``syrix.log``. It degrades to ``print`` if ``syrix_host_log`` is
unavailable (e.g. in a bare unit test) and **never raises** — observability
must not become a new failure mode.

Usage::

    from . import obs
    try:
        risky()
    except Exception as exc:
        obs.report("spine.dispatch", exc, rid=request_id, route=route)
        return fallback

Prefer :func:`report` over a bare ``pass``. Reserve a true silent swallow for
best-effort *telemetry* (e.g. a trace event whose own failure is irrelevant);
even there, ``swallow`` documents the intent.
"""
from __future__ import annotations

import contextlib
import threading
from typing import Any, Iterator

_MARKER = "CORE_V2_SWALLOW"

# Current-request-id thread-local (2026-07-08). Low-level modules like
# syrix_host_brain.py can't import syrix_host.py's own _SYRIX_REQUEST_TLS
# (circular — syrix_host imports syrix_host_brain), so this is the shared,
# import-safe home. syrix_host.py sets it alongside its own TLS at the top
# of each typed-chat dispatch; syrix_host_brain.py reads it when logging which
# provider/model actually answered a turn, so the per-message Inspect panel's
# fallback chain is scoped to the REAL request instead of falling back to a
# global "last brain used" label that's wrong under concurrent turns.
_REQUEST_ID_TLS = threading.local()


def set_current_request_id(request_id: str) -> None:
    _REQUEST_ID_TLS.value = str(request_id or "")


def current_request_id() -> str:
    return str(getattr(_REQUEST_ID_TLS, "value", "") or "")


def _emit(line: str, level: str) -> None:
    """Best-effort write to the canonical sink; never raises."""
    try:
        from syrix_host_log import log_line  # type: ignore

        log_line({}, line, level)
        return
    except Exception:
        pass
    try:
        print(line, flush=True)
    except Exception:
        pass


def _fmt(where: str, exc: BaseException, ctx: dict[str, Any]) -> str:
    err = f"{type(exc).__name__}: {str(exc)[:200]}"
    if ctx:
        kv = " ".join(f"{k}={str(v)[:80]}" for k, v in ctx.items())
        return f"[{_MARKER}] where={where} err={err} {kv}"
    return f"[{_MARKER}] where={where} err={err}"


def _otel_record(where: str, exc: BaseException, ctx: dict[str, Any]) -> None:
    """Best-effort: attach this swallow to the active OTel span (if any).

    Lazily imported and fully guarded — when tracing is off or unavailable this
    is a couple of no-op function calls. Keeps the searchable log line (above)
    as the primary record; the span event is the correlated, trace-scoped copy.
    """
    with contextlib.suppress(Exception):
        import syrix_otel
        syrix_otel.record_exception(where, exc, **ctx)


def report(where: str, exc: BaseException, level: str = "warning", **ctx: Any) -> None:
    """Record a caught-and-degraded exception, then let the caller continue.

    ``where`` is a stable dotted location (``"module.operation"``) so the line
    is greppable. ``ctx`` carries request-scoped breadcrumbs (rid, route, ...).
    Use ``level="error"`` when the degradation drops user-visible function.
    """
    try:
        _emit(_fmt(where, exc, ctx), level)
        _otel_record(where, exc, ctx)
    except Exception:
        # The sink itself must never become a failure mode. _emit already
        # guards internally; this is the belt-and-braces boundary guarantee.
        pass


@contextlib.contextmanager
def swallow(where: str, level: str = "warning", **ctx: Any) -> Iterator[None]:
    """Context-manager form: log any exception from the block, then continue.

    ``with obs.swallow("spine.trace", rid=rid): trace.event(...)`` replaces an
    ``except Exception: pass`` while keeping the failure searchable.
    """
    try:
        yield
    except Exception as exc:  # noqa: BLE001 — deliberate degrade-and-log
        _emit(_fmt(where, exc, ctx), level)
        _otel_record(where, exc, ctx)
