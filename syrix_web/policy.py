"""URL policy for the web gateway — the single validator, deny-list shaped.

Two layers, and both matter:

**String layer** (:func:`check_url`) — scheme allow-list, embedded credentials,
literal private addresses, reserved suffixes. Fast, no network, safe to call on
every candidate URL in a search result list.

**Resolution layer** (:func:`resolves_public`) — actually resolves the host and
refuses if *any* returned record is private. A string check cannot catch DNS
rebinding: ``evil.example`` is a perfectly ordinary-looking name that can
resolve to ``192.168.1.1``. This is the layer that stops it, and it is the
reason a fetch is validated again on every redirect rather than once at entry.

Reads are deny-listed, not allow-listed. The 16-domain allow-list that used to
gate this (``sandbox_browser._DEFAULT_ALLOWLIST``) is the direct cause of most
"I could not verify that" answers — a research assistant that may only read
sixteen websites cannot research. Interactive verbs (click, type, submit,
upload) keep their allow-list and their permission gate; those live in
:mod:`syrix_supercomputer.gate` and are untouched by this module.

Nothing here consults config: a policy with a switch is a policy that will be
switched off. The only tunable is the resolution cache TTL.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import threading
import time
from typing import Dict, Optional, Tuple
from urllib.parse import unquote, urlsplit

import syrix_obs as obs

# Only two schemes describe "a web page". javascript:, data:, blob: and
# view-source: are ways to run something; file: and chrome: reach the machine.
_ALLOWED_SCHEMES = frozenset({"http", "https"})

# Non-web service ports that must never be reached by a page-reading feature.
# Public hosts can still expose sensitive databases, remote shells, container
# daemons, message brokers, and file-sharing protocols; sending HTTP-shaped
# bytes to those services is unnecessary for research and enables cross-
# protocol SSRF. Ordinary HTTP(S), including uncommon high web ports, remains
# available unless the port is explicitly dangerous.
_BLOCKED_PORTS = frozenset({
    20, 21, 22, 23, 25, 53, 69, 110, 111, 135, 137, 138, 139, 143, 161,
    389, 445, 465, 512, 513, 514, 587, 623, 631, 873, 993, 995, 1080,
    1433, 1521, 1883, 2049, 2375, 2376, 3306, 3389, 5432, 5672, 5900,
    5984, 6379, 6443, 9200, 9300, 11211, 27017,
})

# A page-reading surface has no reason to navigate to an executable download.
# This also protects browser paths, where an attachment response may otherwise
# be written by the automation runtime before Python sees the landing page.
_EXECUTABLE_PATH_EXTENSIONS = frozenset({
    ".apk", ".appimage", ".bat", ".cmd", ".com", ".cpl", ".deb", ".dll",
    ".dmg", ".dylib", ".exe", ".jar", ".msi", ".pif", ".pkg", ".ps1",
    ".rpm", ".scr", ".so",
})

# Suffixes that mean "somewhere on this network" regardless of what DNS says.
_PRIVATE_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")

# Names that are private by convention rather than by address.
_PRIVATE_NAMES = frozenset({
    "localhost",
    "metadata.google.internal",
    "metadata",
    "instance-data",
})

# Cloud instance-metadata endpoints. 169.254.0.0/16 is already link-local and
# refused by the address rules, but naming them makes the intent legible and
# covers the IPv6 spelling AWS also serves.
_METADATA_ADDRS = frozenset({"169.254.169.254", "fd00:ec2::254", "100.100.100.200"})

# Carrier-grade NAT — not covered by ipaddress.is_private, but never a public
# site either.
_CGNAT = ipaddress.ip_network("100.64.0.0/10")

_RESOLVE_TTL_S = 300.0
_RESOLVE_CACHE: Dict[str, Tuple[float, bool]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 512


def _reason(ok: bool, reason: str = "") -> Tuple[bool, str]:
    return ok, reason


def extract_host(url: str) -> str:
    """Registrable host, credentials and port stripped, lowercased.

    ``https://github.com@evil.example/x`` yields ``evil.example`` — the userinfo
    trick is the classic way to point a blocklist at the wrong name, so the host
    always comes from the parser, never from a substring of the raw URL.
    """
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        host = urlsplit(raw).hostname or ""
    except Exception:
        return ""
    if not host and "//" not in raw:
        try:
            host = urlsplit("//" + raw).hostname or ""
        except Exception:
            return ""
    return host.lower().rstrip(".")


def _is_ip_literal(value: str) -> bool:
    """Is this a bare IP address rather than a hostname?"""
    try:
        ipaddress.ip_address(str(value or ""))
    except ValueError:
        return False
    return True


def _has_executable_path(url: str) -> bool:
    try:
        path = unquote(urlsplit(str(url or "")).path).lower()
    except Exception:
        return False
    return any(path.endswith(extension) for extension in _EXECUTABLE_PATH_EXTENSIONS)


def _address_is_public(addr: str) -> bool:
    """True only for an address that is routable on the public internet."""
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
    except ValueError:
        return False
    if str(ip) in _METADATA_ADDRS:
        return False
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified):
        return False
    if ip.version == 4 and ip in _CGNAT:
        return False
    if ip.version == 6:
        # An IPv4-mapped or 6to4-wrapped private address is still private.
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None and not _address_is_public(str(mapped)):
            return False
        sixtofour = getattr(ip, "sixtofour", None)
        if sixtofour is not None and not _address_is_public(str(sixtofour)):
            return False
    return True


def check_url(url: str) -> Tuple[bool, str]:
    """String-level check. ``(ok, reason)``; reason is "" when ok.

    Cheap enough to run over every URL in a result list. It does not prove the
    host is public — only :func:`resolves_public` can — so callers that are
    about to *fetch* must use :func:`check_fetch`.
    """
    raw = str(url or "").strip()
    if not raw:
        return _reason(False, "empty_url")
    if len(raw) > 2048:
        return _reason(False, "url_too_long")
    try:
        parsed = urlsplit(raw)
    except Exception:
        return _reason(False, "invalid_url")

    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        return _reason(False, f"scheme_blocked:{scheme or 'none'}")
    if "@" in (parsed.netloc or ""):
        return _reason(False, "credentials_in_url")
    try:
        port = parsed.port
    except ValueError:
        return _reason(False, "invalid_port")
    if port in _BLOCKED_PORTS:
        return _reason(False, f"port_blocked:{port}")

    host = extract_host(raw)
    if not host:
        return _reason(False, "no_host")
    if host in _PRIVATE_NAMES:
        return _reason(False, "private_network")
    if any(host.endswith(sfx) for sfx in _PRIVATE_SUFFIXES):
        return _reason(False, "private_network")

    # A literal address in the URL is checked here so we never even resolve it.
    # A name that is not an address is the ordinary case, not an error, so this
    # asks rather than catching.
    stripped = host.strip("[]")
    if _is_ip_literal(stripped) and not _address_is_public(stripped):
        return _reason(False, "private_network")
    if _has_executable_path(raw):
        return _reason(False, "executable_content_blocked")

    return _reason(True, "")


def resolves_public(host: str, *, ttl_s: float = _RESOLVE_TTL_S) -> bool:
    """Resolve ``host`` and return True only if EVERY record is public.

    Fails closed: a host that cannot be resolved is treated as not public, so a
    DNS outage degrades into "we did not fetch it" rather than "we fetched
    something we could not check".
    """
    name = str(host or "").strip().lower().rstrip(".")
    if not name:
        return False
    now = time.time()
    with _CACHE_LOCK:
        hit = _RESOLVE_CACHE.get(name)
        if hit is not None and (now - hit[0]) < ttl_s:
            return hit[1]

    verdict = False
    try:
        infos = socket.getaddrinfo(name, None)
        verdict = bool(infos) and all(
            _address_is_public(info[4][0]) for info in infos
        )
    except Exception as exc:
        obs.report("web.policy.resolve", exc, level="debug", host=name)
        verdict = False

    with _CACHE_LOCK:
        if len(_RESOLVE_CACHE) >= _CACHE_MAX:
            _RESOLVE_CACHE.clear()
        _RESOLVE_CACHE[name] = (now, verdict)
    return verdict


def resolve_public_addresses(host: str) -> Tuple[str, ...]:
    """Resolve once and return only an all-public address set.

    This is the transport-facing resolver.  Unlike :func:`resolves_public`, it
    never caches because its result is intended to be connected to directly;
    callers must not approve these addresses and then ask DNS to choose a
    different peer.  If resolution fails or *any* answer is non-public the
    entire set is rejected.
    """
    name = str(host or "").strip().lower().rstrip(".")
    if not name:
        return ()
    try:
        infos = socket.getaddrinfo(name, None, type=socket.SOCK_STREAM)
    except Exception as exc:
        obs.report("web.policy.resolve_transport", exc, level="debug", host=name)
        return ()

    addresses = tuple(dict.fromkeys(
        str(info[4][0]).split("%", 1)[0] for info in infos if info[4]
    ))
    if not addresses or any(not _address_is_public(ip) for ip in addresses):
        return ()
    return addresses


def check_fetch(url: str, *, resolve: bool = True) -> Tuple[bool, str]:
    """Full check for a URL we are about to open. Both layers.

    ``resolve=False`` skips the DNS layer — only for callers that have already
    resolved this exact host in the same operation (the browser tier re-checks
    on every navigation, so it would otherwise resolve the same name twice).
    """
    ok, reason = check_url(url)
    if not ok:
        return ok, reason
    if not resolve:
        return _reason(True, "")
    host = extract_host(url)
    # Fetch boundaries resolve afresh. The general resolver retains its small
    # cache for ranking/list operations, but reusing a five-minute verdict here
    # lets an attacker flip DNS to loopback after the first approved request.
    if not resolves_public(host, ttl_s=0.0):
        return _reason(False, "private_network")
    return _reason(True, "")


def check_redirect(from_url: str, to_url: str) -> Tuple[bool, str]:
    """A redirect is a fresh navigation and gets the full check again.

    Validating only the URL the user typed is how an open redirect on a
    permitted host becomes a route to the local network.
    """
    ok, reason = check_fetch(to_url)
    if not ok:
        obs.report(
            "web.policy.redirect_blocked",
            RuntimeError(reason),
            level="debug",
            frm=extract_host(from_url),
            to=extract_host(to_url),
        )
    return ok, reason


def describe_block(reason: str) -> str:
    """Plain English, so a refusal is never mysterious in the chat."""
    key = str(reason or "")
    if key.startswith("scheme_blocked:"):
        return "that address is not a web page I can read"
    if key.startswith("port_blocked:"):
        return "that address uses a non-web service port"
    return {
        "empty_url": "no address was given",
        "invalid_url": "that address could not be parsed",
        "invalid_port": "that address has an invalid port",
        "url_too_long": "that address is implausibly long",
        "no_host": "that address has no host to check",
        "credentials_in_url": "that address embeds credentials, which is a common spoofing trick",
        "executable_content_blocked": "that address points to an executable download",
        "private_network": "that address points at this machine or the local network",
    }.get(key, "it was blocked by policy")


# Origins the sidecar itself is told to refuse, belt-and-braces beside the
# Python check. Passed to @playwright/mcp as --blocked-origins.
BLOCKED_ORIGINS = (
    "localhost", "127.0.0.1", "0.0.0.0", "[::1]",
    "169.254.169.254", "metadata.google.internal",
    "*.localhost", "*.local", "*.internal",
)


def _reset_for_tests() -> None:
    with _CACHE_LOCK:
        _RESOLVE_CACHE.clear()


__all__ = [
    "BLOCKED_ORIGINS",
    "check_fetch",
    "check_redirect",
    "check_url",
    "describe_block",
    "extract_host",
    "resolve_public_addresses",
    "resolves_public",
]
