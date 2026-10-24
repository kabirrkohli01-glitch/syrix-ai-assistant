"""Local preview server for a task's workspace.

Serves ONE workspace directory, on 127.0.0.1, on an ephemeral port, behind a
random path token. That token is the single exception registered with
``browser.policy`` — nothing else on the loopback interface becomes reachable,
and the exception dies with the server.

Deliberately not a general static server:

* binds 127.0.0.1 explicitly, never 0.0.0.0;
* every request must carry the session token or gets 404 (not 403 — an
  unauthenticated probe learns nothing about what is here);
* paths resolve through :func:`workspace.resolve`, so the same containment that
  stops a write escaping stops a read escaping;
* only a small allow-list of content types is served, so the preview cannot be
  turned into a delivery mechanism for something executable.
"""
from __future__ import annotations

import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import unquote, urlparse

import syrix_obs as obs

from . import workspace

_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
    # Documents SYRIX produces, so the user can look at one before deciding what to
    # do with it. Chromium renders PDF and text inline; formats it cannot render
    # are deliberately absent, because serving one would only trigger a download
    # — and downloads are blocked.
    ".pdf": "application/pdf",
    ".csv": "text/plain; charset=utf-8",
}

_LOCK = threading.RLock()
_server: Optional[ThreadingHTTPServer] = None
_thread: Optional[threading.Thread] = None
_state: Dict[str, Any] = {}


class _Handler(BaseHTTPRequestHandler):
    server_version = "SyrixPreview"
    sys_version = ""

    def log_message(self, fmt, *args):  # noqa: A003 - silence stdlib access log
        return

    def _deny(self):
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):  # noqa: N802
        with _LOCK:
            token = _state.get("token") or ""
            task_id = _state.get("task_id") or ""
        if not token:
            self._deny()
            return

        parsed = urlparse(self.path or "")
        parts = [p for p in unquote(parsed.path or "").split("/") if p]
        # /<token>/<relative path...>
        if not parts or parts[0] != token:
            self._deny()
            return
        rel = "/".join(parts[1:]) or "index.html"

        target, reason = workspace.resolve(task_id, rel)
        if target is None:
            self._deny()
            return
        if os.path.isdir(target):
            target = os.path.join(target, "index.html")
        if not os.path.isfile(target):
            self._deny()
            return

        ctype = _TYPES.get(os.path.splitext(target)[1].lower())
        if ctype is None:
            self._deny()
            return

        try:
            with open(target, "rb") as fh:
                body = fh.read()
        except OSError as exc:
            obs.report("supercomputer.preview.read", exc, path=target)
            self._deny()
            return

        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # The preview is the user's own generated page; keep it from reaching out.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; connect-src 'none'; frame-ancestors 'none'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


def start(task_id: str) -> Dict[str, Any]:
    """Serve one task workspace. Restarting switches which task is served."""
    global _server, _thread
    stop()
    token = secrets.token_urlsafe(16)
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    except OSError as exc:
        obs.report("supercomputer.preview.bind", exc)
        return {"ok": False, "reason": f"bind_failed:{type(exc).__name__}"}

    host, port = srv.server_address[0], srv.server_address[1]
    with _LOCK:
        _server = srv
        _state.clear()
        _state.update({"token": token, "task_id": str(task_id or ""),
                       "host": host, "port": int(port)})
    _thread = threading.Thread(target=srv.serve_forever, name="sc-preview", daemon=True)
    _thread.start()

    origin = f"{host}:{port}"
    # The ONE loopback exception, scoped to this exact host:port and revoked on
    # stop(). Nothing else on the interface becomes reachable. Registered in
    # BOTH policy layers — the Electron module refuses loopback independently,
    # so a Python-only exception would still be blocked at the browser.
    from .browser import policy
    from .browser.transport import send as _send

    policy.set_preview_origin(origin)
    with obs.swallow("supercomputer.preview.register_origin", origin=origin):
        _send("preview_origin", origin=origin, timeout_s=6.0)

    url = f"http://{origin}/{token}/"
    return {"ok": True, "data": {"url": url, "origin": origin, "port": int(port),
                                 "task_id": str(task_id or "")}}


def stop() -> bool:
    global _server, _thread
    with _LOCK:
        srv = _server
        _server = None
        _state.clear()
    if srv is None:
        return False
    try:
        srv.shutdown()
        srv.server_close()
    except Exception as exc:
        obs.report("supercomputer.preview.stop", exc)
    _thread = None
    from .browser import policy
    from .browser.transport import send as _send

    policy.clear_preview_origin()
    with obs.swallow("supercomputer.preview.clear_origin"):
        _send("preview_origin", origin="", timeout_s=6.0)
    return True


def snapshot() -> Dict[str, Any]:
    with _LOCK:
        if not _state:
            return {"running": False}
        return {
            "running": True,
            "task_id": _state.get("task_id", ""),
            "url": f"http://{_state.get('host')}:{_state.get('port')}/{_state.get('token')}/",
        }


def _reset_for_tests() -> None:
    stop()


__all__ = ["start", "stop", "snapshot"]
