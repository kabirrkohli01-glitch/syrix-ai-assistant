"""Bounded public-page HTTP transport for SYRIX web readers.

This is intentionally narrower than a general HTTP client: GET only, public
HTTP(S) URLs only, manual redirect validation, a single wall-clock budget, and
a decompressed response-size ceiling. It returns structured failures and never
executes or stores executable downloads.
"""
from __future__ import annotations

import os
import re
import ssl
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional

import syrix_obs as obs

from . import policy


_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_DEFAULT_MAX_BYTES = 1_000_000
_MAX_BYTES_CEILING = 4_000_000
_READ_CHUNK_BYTES = 64 * 1024

_EXECUTABLE_EXTENSIONS = frozenset({
    ".apk", ".appimage", ".bat", ".cmd", ".com", ".cpl", ".deb", ".dll",
    ".dmg", ".dylib", ".exe", ".jar", ".msi", ".pif", ".pkg", ".ps1",
    ".rpm", ".scr", ".so",
})
_EXECUTABLE_CONTENT_TYPES = frozenset({
    "application/java-archive",
    "application/vnd.android.package-archive",
    "application/vnd.microsoft.portable-executable",
    "application/x-dosexec",
    "application/x-executable",
    "application/x-msdownload",
    "application/x-msi",
    "application/x-sharedlib",
})
_FILENAME_RE = re.compile(
    r"""filename\*?\s*=\s*(?:UTF-8''|["'])?([^;"']+)""",
    re.IGNORECASE,
)


@dataclass
class PublicFetchResult:
    ok: bool = False
    status: int = 0
    body: bytes = b""
    headers: Dict[str, str] = field(default_factory=dict)
    url: str = ""
    error: str = ""
    redirects: int = 0


class _FetchPolicyError(RuntimeError):
    """A transport refusal that should remain a structured policy result."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = str(reason or "url_blocked")


class _PinnedResponse:
    """Requests-shaped wrapper around one DNS-pinned urllib3 response."""

    def __init__(self, response: Any, pool: Any, url: str) -> None:
        self._response = response
        self._pool = pool
        self.status_code = int(getattr(response, "status", 0) or 0)
        self.headers = dict(getattr(response, "headers", {}) or {})
        self.url = str(url or "")

    def iter_content(self, chunk_size: int):
        yield from self._response.stream(
            amt=max(1, int(chunk_size)), decode_content=True
        )

    def close(self) -> None:
        try:
            self._response.close()
        finally:
            try:
                self._response.release_conn()
            finally:
                self._pool.close()


def _ascii_host(host: str) -> str:
    try:
        return str(host or "").encode("idna").decode("ascii")
    except Exception as exc:
        raise _FetchPolicyError("invalid_url") from exc


def _host_header(host: str, port: int, scheme: str) -> str:
    shown = f"[{host}]" if ":" in host else host
    default_port = 443 if scheme == "https" else 80
    return shown if port == default_port else f"{shown}:{port}"


def _pinned_get(
    url: str,
    *,
    timeout: float,
    headers: Mapping[str, str],
    allow_redirects: bool,
    stream: bool,
) -> _PinnedResponse:
    """Connect to an address from the exact public DNS answer we validated.

    Checking DNS and then handing the hostname to ``requests`` leaves a
    time-of-check/time-of-use window: the HTTP stack performs a second lookup
    and can be rebound to loopback or a LAN address.  This transport resolves
    once, rejects the whole answer set if any record is non-public, and gives
    urllib3 a numeric peer while retaining the original Host header, SNI and
    certificate hostname.
    """
    if allow_redirects:
        raise ValueError("redirects_must_be_disabled")
    if not stream:
        raise ValueError("streaming_required")

    allowed, reason = policy.check_url(url)
    if not allowed:
        raise _FetchPolicyError(reason)
    parsed = urllib.parse.urlsplit(str(url or ""))
    scheme = parsed.scheme.lower()
    host = policy.extract_host(url)
    tls_host = _ascii_host(host)
    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise _FetchPolicyError("invalid_port") from exc

    addresses = policy.resolve_public_addresses(host)
    if not addresses:
        raise _FetchPolicyError("private_network")

    try:
        import urllib3
    except Exception as exc:
        # No fallback may hand the hostname to another resolver.  Losing the
        # secure transport degrades to no fetch, not an unpinned fetch.
        raise _FetchPolicyError("secure_transport_unavailable") from exc

    request_headers = {
        str(key): str(value)
        for key, value in (headers or {}).items()
        if str(key).lower() not in {"host", "proxy-authorization"}
    }
    request_headers["Host"] = _host_header(tls_host, port, scheme)
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    timeout_cfg = urllib3.util.Timeout(
        connect=max(0.25, float(timeout)), read=max(0.25, float(timeout))
    )

    last_error: Optional[BaseException] = None
    for address in addresses:
        pool = None
        try:
            if scheme == "https":
                pool = urllib3.HTTPSConnectionPool(
                    address,
                    port=port,
                    maxsize=1,
                    block=True,
                    retries=False,
                    assert_hostname=tls_host,
                    server_hostname=tls_host,
                    ssl_context=ssl.create_default_context(),
                )
            else:
                pool = urllib3.HTTPConnectionPool(
                    address, port=port, maxsize=1, block=True, retries=False
                )
            response = pool.urlopen(
                "GET",
                target,
                headers=request_headers,
                redirect=False,
                retries=False,
                preload_content=False,
                timeout=timeout_cfg,
            )
            return _PinnedResponse(response, pool, url)
        except Exception as exc:
            last_error = exc
            if pool is not None:
                pool.close()

    if last_error is not None:
        raise last_error
    raise _FetchPolicyError("private_network")


def _urllib_get(
    url: str,
    *,
    timeout: float,
    headers: Mapping[str, str],
    allow_redirects: bool,
    stream: bool,
) -> _PinnedResponse:
    """Compatibility alias for the old fallback; now equally DNS-pinned."""
    return _pinned_get(
        url,
        timeout=timeout,
        headers=headers,
        allow_redirects=allow_redirects,
        stream=stream,
    )


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = int(default)
    return max(int(minimum), min(int(maximum), parsed))


def _extension_from(value: str) -> str:
    try:
        path = urllib.parse.urlsplit(str(value or "")).path
    except Exception:
        path = str(value or "")
    return os.path.splitext(urllib.parse.unquote(path))[1].lower()


def _looks_executable(url: str, headers: Mapping[str, str]) -> bool:
    if _extension_from(url) in _EXECUTABLE_EXTENSIONS:
        return True
    lowered = {str(k).lower(): str(v) for k, v in headers.items()}
    content_type = lowered.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type in _EXECUTABLE_CONTENT_TYPES:
        return True
    disposition = lowered.get("content-disposition", "")
    match = _FILENAME_RE.search(disposition)
    if match and _extension_from(match.group(1).strip()) in _EXECUTABLE_EXTENSIONS:
        return True
    return False


def _read_limited(
    response: Any,
    max_bytes: int,
    *,
    deadline: Optional[float] = None,
) -> tuple[bytes, str]:
    headers = getattr(response, "headers", {}) or {}
    try:
        declared = int(str(headers.get("Content-Length") or headers.get(
            "content-length") or "0").strip())
    except Exception:
        declared = 0
    if declared > max_bytes:
        return b"", "response_too_large"

    out = bytearray()
    iterator = getattr(response, "iter_content", None)
    if callable(iterator):
        chunks = iterator(chunk_size=_READ_CHUNK_BYTES)
    else:
        chunks = (bytes(getattr(response, "content", b"") or b""),)
    for chunk in chunks:
        if deadline is not None and time.monotonic() >= deadline:
            return b"", "timeout"
        if not chunk:
            continue
        out.extend(bytes(chunk))
        if len(out) > max_bytes:
            return b"", "response_too_large"
    return bytes(out), ""


def fetch_public(
    url: str,
    *,
    timeout_s: float = 8.0,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    headers: Optional[Mapping[str, str]] = None,
    max_redirects: int = 5,
    request_get: Optional[Callable[..., Any]] = None,
) -> PublicFetchResult:
    """Fetch one public page with policy checks on the initial URL and each hop.

    ``request_get`` is dependency injection for offline tests. Production uses
    the DNS-pinned transport above with redirects disabled and streaming.
    """
    current = str(url or "").strip()
    result = PublicFetchResult(url=current)
    byte_cap = _bounded_int(max_bytes, _DEFAULT_MAX_BYTES, 1024, _MAX_BYTES_CEILING)
    redirect_cap = _bounded_int(max_redirects, 5, 0, 10)
    try:
        wall_s = max(0.5, min(30.0, float(timeout_s)))
    except Exception:
        wall_s = 8.0
    deadline = time.monotonic() + wall_s
    req_headers = {str(k): str(v) for k, v in (headers or {}).items()}

    if request_get is None:
        request_get = _pinned_get

    previous = ""
    for hop in range(redirect_cap + 1):
        if previous:
            allowed, reason = policy.check_redirect(previous, current)
        else:
            allowed, reason = policy.check_fetch(current)
        if not allowed:
            result.error = reason or "url_blocked"
            result.url = current
            return result
        if _looks_executable(current, {}):
            result.error = "executable_content_blocked"
            result.url = current
            return result

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            result.error = "timeout"
            result.url = current
            return result

        response = None
        try:
            requested_url = current
            response = request_get(
                requested_url,
                timeout=max(0.25, remaining),
                headers=req_headers,
                allow_redirects=False,
                stream=True,
            )
            status = int(getattr(response, "status_code", 0) or 0)
            response_headers = {
                str(k): str(v)
                for k, v in dict(getattr(response, "headers", {}) or {}).items()
            }
            response_url = str(getattr(response, "url", requested_url) or requested_url)
            result.status = status
            result.headers = response_headers
            result.url = response_url

            # ``allow_redirects=False`` should keep this identical, but custom
            # transports and adapters are permitted via ``request_get``. Treat
            # an unexpected effective URL as another redirect boundary rather
            # than trusting it implicitly.
            if response_url != requested_url:
                allowed, reason = policy.check_redirect(requested_url, response_url)
                if not allowed:
                    result.error = reason or "url_blocked"
                    return result
            current = response_url

            if status in _REDIRECT_STATUSES:
                location = response_headers.get("Location") or response_headers.get(
                    "location") or ""
                if not location:
                    result.error = "redirect_without_location"
                    return result
                if hop >= redirect_cap:
                    result.error = "too_many_redirects"
                    return result
                previous, current = current, urllib.parse.urljoin(current, location)
                result.redirects += 1
                continue

            if _looks_executable(response_url, response_headers):
                result.error = "executable_content_blocked"
                return result
            body, read_error = _read_limited(
                response, byte_cap, deadline=deadline
            )
            if read_error:
                result.error = read_error
                return result
            result.body = body
            result.ok = True
            return result
        except _FetchPolicyError as exc:
            result.error = exc.reason
            result.url = current
            return result
        except Exception as exc:
            result.error = f"request_failed:{type(exc).__name__}"
            result.url = current
            return result
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:
                    # Closing a failed response is best-effort and cannot turn a
                    # safely rejected fetch into an allowed one.
                    obs.report("web.http_fetch.close", exc, level="debug")

    result.error = "too_many_redirects"
    return result


__all__ = ["PublicFetchResult", "fetch_public"]
