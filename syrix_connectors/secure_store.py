"""Encrypted local token vault for connectors — DPAPI, per-user.

Tokens are stored **encrypted or not at all**:

* Primary backend: Windows DPAPI (``win32crypt.CryptProtectData``) with a
  fixed application entropy tag. Per-user encryption, no passphrase, no env
  var — the blob only decrypts for this Windows user on this machine.
* No fallback to plaintext. When ``pywin32`` is unavailable the store reports
  ``unavailable`` and connectors surface health ``needs_setup`` instead of
  writing secrets in the clear.

Blob layout on disk: ``%LOCALAPPDATA%\\SYRIX\\connectors\\tokens\\<id>.dat``
containing the raw DPAPI ciphertext of a UTF-8 JSON document. Token material
is never logged, never redacted-into-logs, never mirrored into hud_state.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, Optional

import syrix_obs as obs

from . import paths

_LOCK = threading.RLock()
_ENTROPY = b"SYRIX.connectors.v1"


def _win32crypt():
    """Import pywin32's crypt module lazily; None when unavailable."""
    try:
        import win32crypt  # type: ignore

        return win32crypt
    except Exception:
        return None


def available() -> bool:
    """True when the DPAPI backend can encrypt/decrypt on this machine."""
    return _win32crypt() is not None


def _blob_path(connector_id: str) -> Optional[str]:
    cid = str(connector_id or "").strip().lower()
    if not cid or not cid.replace("_", "").replace("-", "").isalnum():
        return None
    return os.path.join(paths.tokens_dir(), f"{cid}.dat")


def save_token(connector_id: str, data: Dict[str, Any]) -> bool:
    """Encrypt and persist a token document. False on any failure (logged)."""
    crypt = _win32crypt()
    path = _blob_path(connector_id)
    if crypt is None or path is None or not isinstance(data, dict):
        return False
    with _LOCK:
        try:
            plain = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            blob = crypt.CryptProtectData(plain, "SYRIX connector token", _ENTROPY, None, None, 0)
            tmp = path + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(blob)
            os.replace(tmp, path)
            return True
        except Exception as exc:
            # Log the failure class only — never any token material.
            obs.report("connectors.secure_store.save", exc, connector=connector_id)
            return False


def load_token(connector_id: str) -> Optional[Dict[str, Any]]:
    """Decrypt and return the token document, or None (missing/undecryptable)."""
    crypt = _win32crypt()
    path = _blob_path(connector_id)
    if crypt is None or path is None:
        return None
    with _LOCK:
        try:
            if not os.path.isfile(path):
                return None
            with open(path, "rb") as fh:
                blob = fh.read()
            _desc, plain = crypt.CryptUnprotectData(blob, _ENTROPY, None, None, 0)
            doc = json.loads(plain.decode("utf-8"))
            return doc if isinstance(doc, dict) else None
        except Exception as exc:
            # Wrong user, corrupted blob, tampering — treat all as "no token".
            obs.report("connectors.secure_store.load", exc, connector=connector_id)
            return None


def has_token(connector_id: str) -> bool:
    """True when an encrypted blob exists on disk (does not try to decrypt)."""
    path = _blob_path(connector_id)
    try:
        return bool(path) and os.path.isfile(path)
    except Exception:
        return False


def delete_token(connector_id: str) -> bool:
    """Remove the stored blob. True when no blob remains afterwards."""
    path = _blob_path(connector_id)
    if path is None:
        return False
    with _LOCK:
        try:
            if os.path.isfile(path):
                os.remove(path)
            return not os.path.isfile(path)
        except Exception as exc:
            obs.report("connectors.secure_store.delete", exc, connector=connector_id)
            return False


def status() -> Dict[str, Any]:
    """Non-secret diagnostics for the HUD Security section."""
    n = 0
    try:
        n = sum(1 for f in os.listdir(paths.tokens_dir()) if f.endswith(".dat"))
    except Exception as exc:
        obs.report("connectors.secure_store.status", exc)
    return {
        "backend": "dpapi",
        "available": available(),
        "stored_tokens": n,
    }


__all__ = ["available", "save_token", "load_token", "has_token", "delete_token", "status"]
