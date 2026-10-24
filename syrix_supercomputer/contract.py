"""TaskContract — the immutable statement of what a task is allowed to be.

This is the backbone of the prompt-injection defence. Webpage content is data,
never authority, and the contract is what makes that structural rather than a
plea in a system prompt:

1. **Goal immutability** — ``goal`` is captured from the user's message when the
   task is created and can never be rewritten. Page text cannot restate it.
2. **User-derived destinations** — ``allowed_domains`` is seeded from the user's
   request. A page can never add a domain; only :func:`grant_domain`, driven by
   an answered permission card, can, and it records who asked.
3. **Scope monotonicity** — :meth:`narrow` returns a tighter contract; there is
   no widening operation at all. A sub-step can only ever be more restricted
   than its parent.
4. **No authority from content** — the contract carries no field that page text
   or model output could set to authorise anything. Approval is not expressible
   here; it lives only in :mod:`syrix_supercomputer.permissions`, reachable only
   from the HUD card channel.

The dataclass is frozen, so an accidental mutation is a TypeError rather than a
silent scope expansion.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import FrozenSet, Optional, Tuple
from urllib.parse import urlparse


def normalise_host(url_or_host: str) -> str:
    """Registrable host from a URL or bare host — credentials and port stripped.

    ``https://github.com@evil.example/x`` resolves to ``evil.example``, not
    ``github.com``: userinfo is the classic way to make a blocklist read the
    wrong authority.
    """
    raw = str(url_or_host or "").strip()
    if not raw:
        return ""
    host = urlparse(raw).hostname or ""
    if not host and "//" not in raw:
        host = urlparse("//" + raw).hostname or ""
    return host.lower().rstrip(".")


def host_matches(host: str, domain: str) -> bool:
    """Exact host or a true sub-domain. ``github.com`` matches ``api.github.com``
    but NOT ``github.com.evil.example`` (the suffix-substring bug) and NOT
    ``notgithub.com``."""
    if not host or not domain:
        return False
    return host == domain or host.endswith("." + domain)


@dataclass(frozen=True)
class TaskContract:
    """What this task is for, and the outer limit of what it may touch."""

    task_id: str
    # Captured verbatim from the user. Never regenerated from page content.
    goal: str
    # Seeded from the user's request; extended only via an answered permission
    # card, never from a link found on a page.
    allowed_domains: FrozenSet[str] = field(default_factory=frozenset)
    # Capability verbs this task may use at all (already filtered by settings).
    allowed_actions: FrozenSet[str] = field(default_factory=frozenset)
    # Provenance for every domain that was added after creation, so the audit
    # can show exactly which card widened the reachable surface.
    granted_domains: Tuple[Tuple[str, str], ...] = ()

    # ── destinations ───────────────────────────────────────────────────────
    def allows_url(self, url: str) -> bool:
        host = normalise_host(url)
        if not host:
            # Unparseable authority cannot be authorised — deny by default.
            return False
        return any(host_matches(host, d) for d in self.allowed_domains)

    def grant_domain(self, url_or_host: str, *, permission_request_id: str) -> "TaskContract":
        """Return a contract with one more domain, attributed to a user decision.

        ``permission_request_id`` is required and must be non-empty: it is the
        proof that a human answered a card. There is no code path that adds a
        domain without one.
        """
        rid = str(permission_request_id or "").strip()
        if not rid:
            raise ValueError("grant_domain requires an answered permission request id")
        host = normalise_host(url_or_host)
        if not host:
            raise ValueError(f"grant_domain got no parseable host from {url_or_host!r}")
        return replace(
            self,
            allowed_domains=frozenset(self.allowed_domains | {host}),
            granted_domains=self.granted_domains + ((host, rid),),
        )

    # ── scope ──────────────────────────────────────────────────────────────
    def narrow(
        self,
        *,
        domains: Optional[FrozenSet[str]] = None,
        actions: Optional[FrozenSet[str]] = None,
    ) -> "TaskContract":
        """Return a strictly tighter contract for a sub-step.

        Intersection only. Passing something outside the current scope silently
        drops it rather than adding it — there is no widening path, so a
        "while you're here, also check X" injected into page text cannot reach
        anything new.
        """
        d = self.allowed_domains if domains is None else frozenset(domains) & self.allowed_domains
        a = self.allowed_actions if actions is None else frozenset(actions) & self.allowed_actions
        return replace(self, allowed_domains=d, allowed_actions=a)

    def allows_action(self, action: str) -> bool:
        return str(action or "").strip().lower() in self.allowed_actions

    def is_within(self, other: "TaskContract") -> bool:
        """True when this contract is no broader than ``other`` — the invariant
        the gate asserts before every action."""
        return (
            self.allowed_domains <= other.allowed_domains
            and self.allowed_actions <= other.allowed_actions
        )


__all__ = ["TaskContract", "normalise_host", "host_matches"]
