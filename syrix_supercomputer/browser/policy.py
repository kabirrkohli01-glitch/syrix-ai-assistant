"""URL and action policy for the Supercomputer browser.

Two independent gates, both enforced before an op leaves Python, and the URL
gate enforced again in the Electron module:

**Transport policy** (this module) — scheme, private-network and credential
rules that hold no matter what the task is. These never widen.

**Task scope** (:mod:`syrix_supercomputer.contract`) — which domains *this* task
may reach. Seeded from the user's request and extended only by an answered
permission card, so a link discovered on a page can never authorise itself.

A URL must satisfy BOTH. That ordering matters: a domain the user approved is
still refused if it resolves to a private address, so "approve example.com" can
never become a route to a device on the home network.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from ..contract import TaskContract, host_matches, normalise_host

# Scheme allow-list. javascript:, data:, file: and blob: are all refused — each
# is a way to run something that is not a web page.
_ALLOWED_SCHEMES = ("http", "https")

_PRIVATE_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]")
_PRIVATE_PATTERNS = (
    re.compile(r"^10\."),
    re.compile(r"^192\.168\."),
    re.compile(r"^172\.(1[6-9]|2\d|3[0-1])\."),
    re.compile(r"^169\.254\."),          # link-local / cloud metadata
    re.compile(r"^127\."),
    # IPv6 loopback in any spelling: ::1, 0:0:0:0:0:0:0:1, and so on.
    re.compile(r"^(?:0*:)+0*1$"),
    # IPv4-mapped IPv6 wrapping a private or loopback address. Kept in sync
    # with the same rule in hud_electron/syrix-supercomputer-browser.js —
    # the two layers must agree on what counts as private.
    re.compile(r"^::ffff:(?:127\.|10\.|192\.168\.|169\.254\.|0\.0\.0\.0"
               r"|172\.(?:1[6-9]|2\d|3[0-1])\.)"),
)
# Router/admin surfaces that are worth refusing even on a public host.
_ADMIN_PATH_RE = re.compile(r"(?:^|[^a-z0-9])(?:wp-admin|phpmyadmin|router|admin)\b", re.I)

# Text that would be a credential or payment instrument. Refused for typing
# regardless of any grant: SYRIX never types these, so there is no approval
# that can make it happen.
_SENSITIVE_TEXT_HINTS = (
    "password", "passwd", "cvv", "cvc", "ssn", "social security",
    "card number", "sort code", "routing number", "seed phrase",
    "private key", "one-time code", "2fa code",
)

# The single exception to the private-host block: a Supercomputer-owned preview
# server. Set by the coding workspace (Phase 7) to one exact host:port.
_preview_origin = ""


def set_preview_origin(origin: str) -> None:
    """Register the one local preview origin the browser may reach.

    Deliberately a single exact ``host:port``, not a range and not a wildcard —
    the point is to make one SYRIX-owned preview reachable, not to open the
    loopback interface.
    """
    global _preview_origin
    _preview_origin = str(origin or "").strip().lower()


def clear_preview_origin() -> None:
    global _preview_origin
    _preview_origin = ""


def preview_origin() -> str:
    return _preview_origin


def _is_preview(parsed) -> bool:
    if not _preview_origin:
        return False
    netloc = (parsed.netloc or "").lower()
    return netloc == _preview_origin


def check_url(url: str) -> Tuple[bool, str]:
    """Transport-level check. Returns ``(ok, reason)``; reason is "" when ok."""
    raw = str(url or "").strip()
    if not raw:
        return False, "empty_url"
    try:
        parsed = urlparse(raw)
    except Exception:
        return False, "invalid_url"

    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        return False, f"scheme_blocked:{scheme or 'none'}"

    # Credentials embedded in the authority are how a blocklist gets pointed at
    # the wrong host, so refuse them outright rather than parsing around them.
    if "@" in (parsed.netloc or ""):
        return False, "credentials_in_url"

    host = normalise_host(raw)
    if not host:
        return False, "no_host"

    if _is_preview(parsed):
        return True, ""

    if host in _PRIVATE_HOSTS or any(p.match(host) for p in _PRIVATE_PATTERNS):
        return False, "private_network"
    if host.endswith(".localhost") or host.endswith(".local") or host.endswith(".internal"):
        return False, "private_network"

    if _ADMIN_PATH_RE.search(parsed.path or ""):
        return False, "admin_path"

    return True, ""


# Hosts where merely *arriving* exposes private data or money. Reaching one of
# these when the user did not name it raises a card; the transport rules above
# still apply on top. Deliberately about the destination, not the verb: opening
# an inbox is worth confirming even though opening a page is "just reading".
_SENSITIVE_DOMAINS = (
    "mail.google.com", "outlook.com", "outlook.live.com", "outlook.office.com",
    "office.com", "mail.yahoo.com", "proton.me", "protonmail.com",
    "paypal.com", "stripe.com", "wise.com", "revolut.com", "coinbase.com",
    "binance.com", "chase.com", "hsbc.co.uk", "barclays.co.uk", "monzo.com",
    "accounts.google.com", "myaccount.google.com", "id.apple.com", "appleid.apple.com",
    "amazon.com", "amazon.co.uk", "amazon.in", "ebay.com",
)


def open_navigation_enabled(config: Optional[Dict[str, Any]] = None) -> bool:
    """Whether the agent may open a public site the user did not name.

    ON by default. A research agent that can only visit domains typed verbatim
    into the request cannot follow a search result, which is most of browsing.
    The protection against a page steering the agent is not this gate — it is
    that page text is fenced as data and can never become an instruction
    (:mod:`syrix_supercomputer.browser.injection_guard`) and that the goal is
    immutable (:mod:`syrix_supercomputer.contract`). Set
    ``supercomputer_open_navigation`` false to fall back to named domains only.
    """
    return bool((config or {}).get("supercomputer_open_navigation", True))


def is_sensitive(url: str) -> bool:
    host = normalise_host(url)
    return bool(host) and any(host_matches(host, d) for d in _SENSITIVE_DOMAINS)


def check_scope(
    url: str,
    contract: Optional[TaskContract],
    *,
    config: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """Task-scope check: may THIS task reach this URL?

    Three outcomes, not two. ``needs_scope_permission`` is returned for a
    sensitive destination the user did not name, so the caller turns it into a
    permission card rather than a flat refusal.
    """
    if contract is None:
        return False, "no_task_contract"
    host = normalise_host(url)
    if not host:
        return False, "no_host"
    if any(host_matches(host, d) for d in contract.allowed_domains):
        return True, ""
    if is_sensitive(url):
        return False, "needs_scope_permission"
    if open_navigation_enabled(config):
        return True, ""
    return False, "domain_not_in_task_scope"


def check_navigation(
    url: str,
    contract: Optional[TaskContract],
    *,
    config: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """Both gates, transport first. This is what the controller calls."""
    ok, reason = check_url(url)
    if not ok:
        return False, reason
    return check_scope(url, contract, config=config)


def check_typed_text(text: str) -> Tuple[bool, str]:
    """Refuse to type credentials or payment instruments. No grant overrides
    this: it is a thing SYRIX does not do, not a thing it asks about."""
    low = str(text or "").lower()
    for hint in _SENSITIVE_TEXT_HINTS:
        if hint in low:
            return False, "refused_sensitive_text"
    return True, ""


def describe_block(reason: str) -> str:
    """Plain-English explanation for the chat, so a refusal is never mysterious."""
    return {
        "empty_url": "no address was given",
        "invalid_url": "that address could not be parsed",
        "no_host": "that address has no host to check",
        "credentials_in_url": "that address embeds credentials, which is a common spoofing trick",
        "private_network": "that address points at this machine or the local network",
        "admin_path": "that looks like a router or admin console",
        "domain_not_in_task_scope": "that site is not part of this task",
        "needs_scope_permission": "that site holds private data or money, so I need your say-so first",
        "no_task_contract": "there is no active task to scope that to",
        "refused_sensitive_text": "that looks like a password or payment detail, which I never type",
    }.get(str(reason or ""), str(reason or "it was blocked by policy"))


def snapshot() -> Dict[str, Any]:
    return {"preview_origin": _preview_origin}


def _reset_for_tests() -> None:
    clear_preview_origin()


__all__ = [
    "check_url",
    "check_scope",
    "check_navigation",
    "check_typed_text",
    "describe_block",
    "is_sensitive",
    "open_navigation_enabled",
    "set_preview_origin",
    "clear_preview_origin",
    "preview_origin",
]
