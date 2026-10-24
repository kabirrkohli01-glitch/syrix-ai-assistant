"""Loopback HTTP CONNECT proxy with a public-network-only transport.

Chromium's origin blocklist is useful defence in depth, but Playwright's own
documentation is explicit that it is not a security boundary and does not
cover redirects.  This proxy is the browser boundary: every HTTP request and
HTTPS tunnel resolves at the proxy, rejects the whole DNS answer if any address
is non-public, and connects a validated numeric address directly.  Redirects,
XHR/fetch, WebSockets, scripts, frames and other subresources all traverse it.
"""
from __future__ import annotations

import ipaddress
import select
import socket
import socketserver
import threading
import time
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

import syrix_obs as obs

from . import policy


_MAX_HEADER_BYTES = 64 * 1024
_MAX_TUNNEL_BYTES = 64 * 1024 * 1024
_HEADER_TIMEOUT_S = 10.0
_CONNECT_TIMEOUT_S = 8.0
_TUNNEL_IDLE_S = 60.0
_MAX_CONNECTIONS = 24
_HOP_HEADERS = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "proxy-connection", "te", "trailer", "upgrade",
})


class ProxyRefusal(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = str(reason or "private_network")


def _url_for(host: str, port: int, scheme: str) -> str:
    shown = f"[{host}]" if ":" in host else host
    return f"{scheme}://{shown}:{int(port)}/"


def _open_public_socket(
    host: str,
    port: int,
    *,
    scheme: str,
    timeout_s: float = _CONNECT_TIMEOUT_S,
) -> socket.socket:
    """Resolve, validate and connect without asking DNS a second time."""
    allowed, reason = policy.check_url(_url_for(host, port, scheme))
    if not allowed:
        raise ProxyRefusal(reason)
    addresses = policy.resolve_public_addresses(host)
    if not addresses:
        raise ProxyRefusal("private_network")

    last_error: Optional[BaseException] = None
    for address in addresses:
        peer = ipaddress.ip_address(address)
        family = socket.AF_INET6 if peer.version == 6 else socket.AF_INET
        upstream = socket.socket(family, socket.SOCK_STREAM)
        try:
            upstream.settimeout(max(0.25, float(timeout_s)))
            destination = (
                (str(peer), int(port), 0, 0)
                if family == socket.AF_INET6
                else (str(peer), int(port))
            )
            upstream.connect(destination)
            return upstream
        except Exception as exc:
            last_error = exc
            upstream.close()
    if last_error is not None:
        raise last_error
    raise ProxyRefusal("private_network")


def _receive_head(client: socket.socket) -> Tuple[bytes, bytes]:
    client.settimeout(_HEADER_TIMEOUT_S)
    data = bytearray()
    marker = b"\r\n\r\n"
    while marker not in data:
        chunk = client.recv(min(8192, _MAX_HEADER_BYTES + 1 - len(data)))
        if not chunk:
            raise ProxyRefusal("empty_request")
        data.extend(chunk)
        if len(data) > _MAX_HEADER_BYTES:
            raise ProxyRefusal("headers_too_large")
    head, remainder = bytes(data).split(marker, 1)
    return head, remainder


def _parse_head(head: bytes) -> Tuple[str, str, str, List[Tuple[str, str]]]:
    lines = head.split(b"\r\n")
    if not lines:
        raise ProxyRefusal("invalid_request")
    try:
        first = lines[0].decode("ascii")
        method, target, version = first.split(" ")
    except Exception as exc:
        raise ProxyRefusal("invalid_request") from exc
    if not method.isalpha() or version not in {"HTTP/1.0", "HTTP/1.1"}:
        raise ProxyRefusal("invalid_request")

    headers: List[Tuple[str, str]] = []
    for raw in lines[1:]:
        if not raw or raw[:1] in b" \t" or b":" not in raw:
            raise ProxyRefusal("invalid_headers")
        name_raw, value_raw = raw.split(b":", 1)
        try:
            name = name_raw.decode("ascii")
            value = value_raw.decode("latin-1").strip()
        except Exception as exc:
            raise ProxyRefusal("invalid_headers") from exc
        if not name or any(not (c.isalnum() or c in "!#$%&'*+-.^_`|~") for c in name):
            raise ProxyRefusal("invalid_headers")
        if "\r" in value or "\n" in value or "\x00" in value:
            raise ProxyRefusal("invalid_headers")
        headers.append((name, value))
    return method.upper(), target, version, headers


def _connect_target(target: str) -> Tuple[str, int]:
    if "/" in target or "?" in target or "#" in target:
        raise ProxyRefusal("invalid_connect_target")
    try:
        parsed = urlsplit("//" + target)
        host = parsed.hostname or ""
        port = parsed.port
    except Exception as exc:
        raise ProxyRefusal("invalid_connect_target") from exc
    if not host or port is None:
        raise ProxyRefusal("invalid_connect_target")
    try:
        host = host.encode("idna").decode("ascii")
    except Exception as exc:
        raise ProxyRefusal("invalid_connect_target") from exc
    return host.lower().rstrip("."), int(port)


def _http_target(target: str) -> Tuple[str, int, str, str]:
    try:
        parsed = urlsplit(target)
        host = parsed.hostname or ""
        port = parsed.port or 80
    except Exception as exc:
        raise ProxyRefusal("invalid_url") from exc
    if parsed.scheme.lower() != "http" or not host:
        raise ProxyRefusal("scheme_blocked")
    origin = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    try:
        host = host.encode("idna").decode("ascii")
    except Exception as exc:
        raise ProxyRefusal("invalid_url") from exc
    return host.lower().rstrip("."), int(port), origin, target


def _host_header(host: str, port: int) -> str:
    shown = f"[{host}]" if ":" in host else host
    return shown if port == 80 else f"{shown}:{port}"


def _forward_http_head(
    upstream: socket.socket,
    method: str,
    origin: str,
    version: str,
    host: str,
    port: int,
    headers: Iterable[Tuple[str, str]],
) -> None:
    lines = [f"{method} {origin} {version}", f"Host: {_host_header(host, port)}"]
    upgrade = ""
    for name, value in headers:
        lowered = name.lower()
        if lowered == "upgrade":
            upgrade = value
            continue
        if lowered == "host" or lowered in _HOP_HEADERS:
            continue
        lines.append(f"{name}: {value}")
    if upgrade:
        lines.extend((f"Upgrade: {upgrade}", "Connection: Upgrade"))
    else:
        lines.append("Connection: close")
    lines.extend(("", ""))
    upstream.sendall("\r\n".join(lines).encode("latin-1"))


def _tunnel(client: socket.socket, upstream: socket.socket, initial: bytes = b"") -> None:
    if initial:
        upstream.sendall(initial)
    client.settimeout(10.0)
    upstream.settimeout(10.0)
    total = len(initial)
    last_activity = time.monotonic()
    sockets = (client, upstream)
    while total <= _MAX_TUNNEL_BYTES:
        readable, _, failed = select.select(sockets, (), sockets, 1.0)
        if failed:
            return
        if not readable:
            if time.monotonic() - last_activity >= _TUNNEL_IDLE_S:
                return
            continue
        for source in readable:
            destination = upstream if source is client else client
            try:
                chunk = source.recv(64 * 1024)
                if not chunk:
                    return
                destination.sendall(chunk)
            except (BlockingIOError, InterruptedError):
                continue
            total += len(chunk)
            last_activity = time.monotonic()


def _send_error(client: socket.socket, status: int, label: str) -> None:
    body = f"SYRIX browser network guard: {label}\n".encode("ascii", "replace")
    payload = (
        f"HTTP/1.1 {status} {label}\r\n"
        "Content-Type: text/plain\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii") + body
    try:
        client.sendall(payload)
    except Exception as exc:
        obs.report("web.proxy.send_error", exc, level="debug")


class _ProxyHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        guard = self.server.guard  # type: ignore[attr-defined]
        if not guard._slots.acquire(blocking=False):
            _send_error(self.request, 503, "Guard busy")
            return
        upstream: Optional[socket.socket] = None
        try:
            head, remainder = _receive_head(self.request)
            method, target, version, headers = _parse_head(head)
            if method == "CONNECT":
                host, port = _connect_target(target)
                upstream = _open_public_socket(host, port, scheme="https")
                self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                _tunnel(self.request, upstream, remainder)
            else:
                host, port, origin, _url = _http_target(target)
                upstream = _open_public_socket(host, port, scheme="http")
                _forward_http_head(
                    upstream, method, origin, version, host, port, headers
                )
                _tunnel(self.request, upstream, remainder)
            guard._record("allowed")
        except ProxyRefusal:
            guard._record("blocked")
            _send_error(self.request, 403, "Forbidden")
        except Exception as exc:
            guard._record("failed")
            obs.report("web.proxy.connection", exc, level="debug")
            _send_error(self.request, 502, "Bad Gateway")
        finally:
            if upstream is not None:
                try:
                    upstream.close()
                except Exception as exc:
                    obs.report("web.proxy.upstream_close", exc, level="debug")
            guard._slots.release()


class _ProxyServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True
    request_queue_size = _MAX_CONNECTIONS


class PublicOnlyProxy:
    """Lifecycle wrapper for one loopback-only guarded proxy."""

    def __init__(self) -> None:
        self._server: Optional[_ProxyServer] = None
        self._thread: Optional[threading.Thread] = None
        self._slots = threading.BoundedSemaphore(_MAX_CONNECTIONS)
        self._metrics_lock = threading.Lock()
        self._metrics: Dict[str, int] = {"allowed": 0, "blocked": 0, "failed": 0}

    def _record(self, key: str) -> None:
        with self._metrics_lock:
            self._metrics[key] = self._metrics.get(key, 0) + 1

    def start(self) -> str:
        if self.alive():
            return self.url
        server = _ProxyServer(("127.0.0.1", 0), _ProxyHandler)
        server.guard = self  # type: ignore[attr-defined]
        thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.2},
            daemon=True,
            name="syrix-public-web-proxy",
        )
        self._server, self._thread = server, thread
        thread.start()
        if not thread.is_alive():
            self.stop()
            raise RuntimeError("proxy_start_failed")
        return self.url

    @property
    def url(self) -> str:
        server = self._server
        if server is None:
            return ""
        host, port = server.server_address[:2]
        if host != "127.0.0.1" or not port:
            return ""
        return f"http://127.0.0.1:{int(port)}"

    def alive(self) -> bool:
        return bool(
            self._server is not None
            and self._thread is not None
            and self._thread.is_alive()
            and self.url
        )

    def stop(self) -> None:
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        if server is not None:
            try:
                server.shutdown()
            finally:
                server.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def status(self) -> Dict[str, object]:
        with self._metrics_lock:
            metrics = dict(self._metrics)
        return {"alive": self.alive(), **metrics}


__all__ = ["PublicOnlyProxy", "ProxyRefusal"]
