"""Minimal environments for security-sensitive child processes.

Child processes inherit the full parent environment when ``env`` is omitted.
That is unsafe for SYRIX because the parent owns provider credentials and other
private runtime settings that a browser sidecar does not need.  This module
builds an allow-listed environment containing only operating-system, locale,
temporary-directory, certificate, and Playwright runtime variables.

The helper is deliberately opt-in.  Callers must pass its result explicitly to
their process launcher; importing this module changes no global state.
"""
from __future__ import annotations

import os
from typing import Dict, Mapping, Optional


# Keep this list small.  In particular, do not add provider credentials, proxy
# variables (which may embed credentials), NODE_OPTIONS, or general SYRIX
# configuration.  PLAYWRIGHT_BROWSERS_PATH is a local installation path, not a
# credential, and is needed by deployments with a shared browser cache.
_ALLOWED_KEYS = frozenset({
    "APPDATA",
    "COMSPEC",
    "HOME",
    "HOMEDRIVE",
    "HOMEPATH",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "LOCALAPPDATA",
    "PATH",
    "PATHEXT",
    "PLAYWRIGHT_BROWSERS_PATH",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "TZ",
    "USERPROFILE",
    "WINDIR",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_RUNTIME_DIR",
})


def minimal_child_env(
    source: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """Return an allow-listed copy of ``source`` (or :data:`os.environ`).

    Matching is case-insensitive so Windows' conventional ``Path`` and
    ``SystemRoot`` spellings are retained.  Original key casing is preserved,
    and values are copied as strings without mutating the source mapping.
    """
    current = os.environ if source is None else source
    allowed_upper = {key.upper() for key in _ALLOWED_KEYS}
    result: Dict[str, str] = {}
    for raw_key, raw_value in current.items():
        key = str(raw_key)
        if key.upper() not in allowed_upper:
            continue
        result[key] = str(raw_value)
    return result


__all__ = ["minimal_child_env"]
