"""Non-overridable provider privacy checks shared by brain transports."""
from __future__ import annotations

import ipaddress
import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

try:
    from syrix_redact import redact_text as _redact_text
except Exception:  # Detector unavailable means external dispatch is unsafe.
    _redact_text = None


def _redactor_matches(value: str) -> bool:
    if _redact_text is None:
        return True
    text = str(value or "")
    if not text:
        return False
    try:
        return _redact_text(text) != text
    except Exception:
        return True


def payload_contains_secret(*payloads: Any) -> bool:
    """Return True when existing central redaction detects any nested value.

    Mapping serialization is checked as well as individual leaves so a generic
    value under a secret-named field (for example ``api_key``) cannot evade the
    existing JSON key/value detector. Nothing inspected here is logged.
    """
    stack = list(payloads)
    seen: set[int] = set()
    while stack:
        value = stack.pop()
        if value is None or isinstance(value, (bool, int, float)):
            continue
        if isinstance(value, bytes):
            if _redactor_matches(value.decode("utf-8", errors="replace")):
                return True
            continue
        if isinstance(value, str):
            if _redactor_matches(value):
                return True
            continue

        identity = id(value)
        if identity in seen:
            continue
        seen.add(identity)

        if isinstance(value, Mapping):
            try:
                serialized = json.dumps(
                    value, ensure_ascii=False, sort_keys=True, default=str
                )
            except Exception:
                serialized = ""
            if serialized and _redactor_matches(serialized):
                return True
            for key, item in value.items():
                stack.append(key)
                stack.append(item)
            continue
        if isinstance(value, (list, tuple, set, frozenset)):
            stack.extend(value)
            continue
        if _redactor_matches(str(value)):
            return True
    return False


def provider_is_loopback(base_url: str) -> bool:
    """Whether a provider URL is unambiguously local to this machine."""
    try:
        host = (urlsplit(str(base_url or "")).hostname or "").strip().lower()
        if host == "localhost":
            return True
        return bool(host and ipaddress.ip_address(host).is_loopback)
    except Exception:
        return False
