"""WS transport for Supercomputer browser ops.

Python → ``syrix_host_hud_bridge.ws_send({type: "sc_browser_op"})`` → the renderer
relay in ``syrix-bridge.js`` → ``window.syrixHud.superComputer.*`` →
``sc-browser:*`` handlers in ``hud_electron/syrix-supercomputer-browser.js``.

The message type is what keeps this separate from the older app-dock sandbox:
``sc_browser_op`` reaches a different relay, a different preload namespace and a
different Electron module, so an op issued here cannot resolve to an app-dock
view. Replies come back on the shared ``sandbox_result`` channel, correlated by
``op_id``.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict

import syrix_obs as obs

MSG_TYPE = "sc_browser_op"


def result(ok: bool, reason: str = "", *, data: Any = None) -> Dict[str, Any]:
    return {"ok": bool(ok), "reason": str(reason or ""), "data": data if data is not None else {}}


def send(verb: str, *, timeout_s: float = 12.0, **fields: Any) -> Dict[str, Any]:
    """Dispatch one op to the Electron module and await its reply.

    Never raises: a dead bridge or a slow renderer becomes a normal
    ``{ok: False}`` envelope so the caller reports honestly rather than
    exploding mid-task.
    """
    try:
        import syrix_host_hud_bridge as _bridge
    except Exception as exc:
        obs.report("supercomputer.browser.transport.import", exc, verb=verb)
        return result(False, "bridge_unavailable")

    op: Dict[str, Any] = {"op_id": uuid.uuid4().hex[:16], "verb": str(verb or "")}
    op.update(fields)
    try:
        res = _bridge.send_sandbox_op(op, timeout_s=float(timeout_s), msg_type=MSG_TYPE)
    except TypeError:
        # An older bridge without the msg_type kwarg would silently route this
        # to the app-dock relay, which owns different views. Refuse instead.
        obs.report(
            "supercomputer.browser.transport.msg_type_unsupported",
            RuntimeError("syrix_host_hud_bridge.send_sandbox_op lacks msg_type"),
            verb=verb,
        )
        return result(False, "bridge_too_old")
    except Exception as exc:
        obs.report("supercomputer.browser.transport.send", exc, verb=verb)
        return result(False, f"transport_error:{type(exc).__name__}")

    if res is None:
        return result(False, "no_hud_response")
    return {
        "ok": bool(res.get("ok")),
        "reason": str(res.get("reason") or ""),
        "data": res.get("data") or {},
    }


__all__ = ["MSG_TYPE", "send", "result"]
