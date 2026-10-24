"""Google OAuth for connectors — modern loopback flow, DPAPI-stored tokens.

Replaces (does NOT modify) the legacy ``syrix_core_v2/google_oauth.py``,
whose out-of-band ("oob") flow Google shut down in 2023. This provider uses
``InstalledAppFlow.run_local_server`` — the browser consent screen redirects
to a loopback port on 127.0.0.1, which a desktop OAuth client
(``client_secrets.json`` in the project root, redirect_uri ``http://localhost``)
already supports.

Per-connector token isolation: each connector gets its own token blob in the
secure store keyed by ``google:<connector_id>``, so disconnecting Gmail never
touches a future Calendar connection and each connector's scopes stay minimal.

All google-lib imports are lazy — importing this module costs nothing and the
framework degrades to ``needs_setup`` when the libraries are missing.
"""
from __future__ import annotations

import os
import threading
import webbrowser
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

import syrix_obs as obs

from .. import secure_store

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
_CONSENT_TIMEOUT_S = 210  # inside the manager's 240 s connect budget
_SYSTEM_BROWSER_CONSENT_LOCK = threading.RLock()


@contextmanager
def _use_system_browser_for_consent():
    """Temporarily bypass the HUD's global browser shim for OAuth.

    ``InstalledAppFlow`` delegates the consent URL to :mod:`webbrowser`.
    SYRIX normally routes those calls to its isolated HUD browser, which
    breaks a loopback consent flow and hides the user's existing Google
    sessions.  The HUD saves the original system-browser functions for this
    exact kind of external flow.
    """
    with _SYSTEM_BROWSER_CONSENT_LOCK:
        original_open = getattr(webbrowser, "_syrix_hud_orig_open", None)
        if not callable(original_open):
            yield
            return
        current_open = webbrowser.open
        current_new_tab = webbrowser.open_new_tab
        original_new_tab = getattr(webbrowser, "_syrix_hud_orig_new_tab", None)
        try:
            webbrowser.open = original_open
            if callable(original_new_tab):
                webbrowser.open_new_tab = original_new_tab
            yield
        finally:
            webbrowser.open = current_open
            webbrowser.open_new_tab = current_new_tab


def _libs_available() -> bool:
    try:
        import google.oauth2.credentials  # noqa: F401
        import google_auth_oauthlib.flow  # noqa: F401
        import googleapiclient.discovery  # noqa: F401

        return True
    except Exception:
        return False


def client_secrets_path() -> str:
    """First existing OAuth client file (desktop-app type)."""
    candidates = [
        os.path.join(_REPO_ROOT, "client_secrets.json"),
        os.path.join(_REPO_ROOT, "data", "google_oauth", "credentials.json"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return ""


class GoogleAuthProvider:
    """OAuth token lifecycle for one connector: consent, refresh, revoke."""

    def __init__(self, connector_id: str, scopes: List[str]) -> None:
        self.connector_id = str(connector_id or "").strip().lower()
        self.scopes = list(scopes or [])
        self._store_key = f"google_{self.connector_id}"
        self._lock = threading.RLock()

    # ── Preconditions ────────────────────────────────────────────────────
    def ready(self) -> Tuple[bool, str]:
        """(True, "") when a consent flow could run right now."""
        if not _libs_available():
            return False, "google_libs_missing"
        if not client_secrets_path():
            return False, "client_secrets_missing"
        if not secure_store.available():
            return False, "secure_store_unavailable"
        return True, ""

    def has_stored_token(self) -> bool:
        return secure_store.has_token(self._store_key)

    # ── Consent flow (blocking; manager runs it on a worker thread) ─────
    def connect(self, progress=None) -> Tuple[bool, Any]:
        """Run the loopback consent flow. Returns ``(True, credentials)`` or
        ``(False, error_code)``. Never raises."""
        ok, why = self.ready()
        if not ok:
            return False, why
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow

            flow = InstalledAppFlow.from_client_secrets_file(
                client_secrets_path(), self.scopes
            )
            if callable(progress):
                progress("waiting_consent", "Waiting for you to approve in the browser")
            with _use_system_browser_for_consent():
                creds = flow.run_local_server(
                    host="127.0.0.1",
                    port=0,
                    open_browser=True,
                    timeout_seconds=_CONSENT_TIMEOUT_S,
                    authorization_prompt_message="",
                    success_message=(
                        "SYRIX is connected. You can close this tab and return to SYRIX."
                    ),
                )
        except Exception as exc:
            obs.report("connectors.google_auth.consent", exc, connector=self.connector_id)
            name = type(exc).__name__
            if "AccessDenied" in name or "access_denied" in str(exc):
                return False, "consent_denied"
            return False, f"consent_failed:{name}"
        if creds is None or not creds.valid:
            return False, "consent_incomplete"
        if not self._save(creds):
            return False, "token_store_failed"
        return True, creds

    # ── Stored-credential lifecycle ──────────────────────────────────────
    def _save(self, creds: Any) -> bool:
        try:
            import json as _json

            doc = _json.loads(creds.to_json())
        except Exception as exc:
            obs.report("connectors.google_auth.serialize", exc, connector=self.connector_id)
            return False
        return secure_store.save_token(self._store_key, doc)

    def granted_scopes(self) -> set:
        """Scope set the STORED token was actually granted (may lag
        ``self.scopes`` when the connector gained scopes after the user
        connected — callers surface 'reconnect to enable X' in that case)."""
        try:
            doc = secure_store.load_token(self._store_key) or {}
            return {str(s) for s in (doc.get("scopes") or [])}
        except Exception as exc:
            obs.report("connectors.google_auth.granted_scopes", exc,
                       connector=self.connector_id)
            return set()

    def load_credentials(self) -> Tuple[Optional[Any], str]:
        """(credentials, status) — status ∈ ok | no_token | needs_reauth | needs_setup."""
        if not _libs_available():
            return None, "needs_setup"
        with self._lock:
            doc = secure_store.load_token(self._store_key)
            if not doc:
                return None, "no_token"
            try:
                from google.oauth2.credentials import Credentials

                # Build against the scopes the token was GRANTED, not the
                # connector's current wish-list — otherwise a scope added in
                # an update poisons refresh for an older token.
                _granted = [str(s) for s in (doc.get("scopes") or [])] or self.scopes
                creds = Credentials.from_authorized_user_info(doc, _granted)
            except Exception as exc:
                obs.report("connectors.google_auth.parse", exc, connector=self.connector_id)
                return None, "needs_reauth"
            if creds.valid:
                return creds, "ok"
            if creds.expired and creds.refresh_token:
                try:
                    from google.auth.transport.requests import Request

                    creds.refresh(Request())
                    self._save(creds)
                    return creds, "ok"
                except Exception as exc:
                    # invalid_grant → user revoked access / token rotated out.
                    obs.report("connectors.google_auth.refresh", exc,
                               connector=self.connector_id)
                    return None, "needs_reauth"
            return None, "needs_reauth"

    def revoke_and_delete(self) -> None:
        """Best-effort server-side revoke, then unconditionally wipe the blob."""
        doc: Optional[Dict[str, Any]] = None
        try:
            doc = secure_store.load_token(self._store_key)
        except Exception as exc:
            obs.report("connectors.google_auth.revoke_load", exc, connector=self.connector_id)
        token = (doc or {}).get("token") or (doc or {}).get("refresh_token")
        if token:
            try:
                import requests

                requests.post(_REVOKE_URL, params={"token": token},
                              headers={"content-type": "application/x-www-form-urlencoded"},
                              timeout=6)
            except Exception as exc:
                obs.report("connectors.google_auth.revoke", exc, connector=self.connector_id)
        secure_store.delete_token(self._store_key)


__all__ = ["GoogleAuthProvider", "client_secrets_path"]
