"""Connector catalogue + instance registry.

``CATALOGUE`` is the single static list of every connector SYRIX knows about.
Implemented connectors carry an import path; any future unimplemented entries
render as "coming soon" and can never be connected or executed. Adding a new
connector requires an implementation class, one catalogue entry and tests —
the framework gates do not change.

Instances are created lazily (first use) and cached; a connector whose
declared ``schema_version`` does not match the framework's is quarantined
(reported once, surfaced as health ``needs_update``, never executed).
"""
from __future__ import annotations

import importlib
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional

import syrix_obs as obs

from .base import SCHEMA_VERSION, ConnectorBase


@dataclass(frozen=True)
class CatalogueEntry:
    id: str
    name: str
    description: str
    icon: str
    category: str
    implemented: bool = False
    import_path: str = ""  # "package.module:ClassName" when implemented


CATALOGUE: List[CatalogueEntry] = [
    # ── Communication ────────────────────────────────────────────────────────
    CatalogueEntry(
        id="gmail", name="Gmail",
        description="Read, search and summarize emails — draft replies, never sends",
        icon="gmail", category="communication",
        implemented=True,
        import_path="syrix_connectors.connectors.gmail:GmailConnector",
    ),
    CatalogueEntry(
        id="outlook", name="Outlook",
        description="Read and search Outlook mail and calendar; controlled sends",
        icon="outlook", category="communication", implemented=True,
        import_path="syrix_connectors.connectors.token_rest:OutlookConnector"),
    CatalogueEntry(
        id="hostinger_mail", name="Hostinger",
        description="Read, search and send Hostinger mail with confirmation",
        icon="hostinger_mail", category="communication", implemented=True,
        import_path="syrix_connectors.connectors.hostinger_mail:HostingerMailConnector"),
    CatalogueEntry(
        id="slack", name="Slack",
        description="Channels, messages and controlled posting",
        icon="slack", category="communication", implemented=True,
        import_path="syrix_connectors.connectors.token_rest:SlackConnector"),
    CatalogueEntry(
        id="discord", name="Discord",
        description="Servers, channel messages and controlled posting",
        icon="discord", category="communication", implemented=True,
        import_path="syrix_connectors.connectors.token_rest:DiscordConnector"),
    CatalogueEntry(
        id="teams", name="Microsoft Teams",
        description="Teams, channels and messages through Microsoft Graph",
        icon="teams", category="communication", implemented=True,
        import_path="syrix_connectors.connectors.token_rest:TeamsConnector"),

    # ── Work ─────────────────────────────────────────────────────────────────
    CatalogueEntry(
        id="google_calendar", name="Google Calendar",
        description="View schedules and create confirmed events",
        icon="google_calendar", category="work", implemented=True,
        import_path="syrix_connectors.connectors.google_suite:GoogleCalendarConnector"),
    CatalogueEntry(
        id="notion", name="Notion",
        description="Search pages and databases",
        icon="notion", category="work", implemented=True,
        import_path="syrix_connectors.connectors.token_rest:NotionConnector"),
    CatalogueEntry(
        id="todoist", name="Todoist",
        description="Read and create controlled tasks",
        icon="todoist", category="work", implemented=True,
        import_path="syrix_connectors.connectors.token_rest:TodoistConnector"),
    CatalogueEntry(
        id="asana", name="Asana",
        description="Projects, tasks and confirmed task creation",
        icon="asana", category="work", implemented=True,
        import_path="syrix_connectors.connectors.token_rest:AsanaConnector"),
    CatalogueEntry(
        id="trello", name="Trello",
        description="Boards and cards",
        icon="trello", category="work", implemented=True,
        import_path="syrix_connectors.connectors.token_rest:TrelloConnector"),
    CatalogueEntry(
        id="jira", name="Jira",
        description="Search Jira issues and work items",
        icon="jira", category="work", implemented=True,
        import_path="syrix_connectors.connectors.token_rest:JiraConnector"),
    CatalogueEntry(
        id="confluence", name="Confluence",
        description="Search Confluence pages and knowledge",
        icon="confluence", category="work", implemented=True,
        import_path="syrix_connectors.connectors.token_rest:ConfluenceConnector"),
    CatalogueEntry(
        id="linear", name="Linear",
        description="Read Linear issues and project work",
        icon="linear", category="work", implemented=True,
        import_path="syrix_connectors.connectors.token_rest:LinearConnector"),
    CatalogueEntry(
        id="hubspot", name="HubSpot",
        description="CRM contacts and deals",
        icon="hubspot", category="work", implemented=True,
        import_path="syrix_connectors.connectors.token_rest:HubSpotConnector"),

    # ── Storage and local ────────────────────────────────────────────────────
    CatalogueEntry(
        id="google_drive", name="Google Drive",
        description="Search and read Drive files",
        icon="google_drive", category="storage", implemented=True,
        import_path="syrix_connectors.connectors.google_suite:GoogleDriveConnector"),
    CatalogueEntry(
        id="onedrive", name="OneDrive",
        description="List and search files through Microsoft Graph",
        icon="onedrive", category="storage", implemented=True,
        import_path="syrix_connectors.connectors.token_rest:OneDriveConnector"),
    CatalogueEntry(
        id="dropbox", name="Dropbox",
        description="List and search Dropbox files",
        icon="dropbox", category="storage", implemented=True,
        import_path="syrix_connectors.connectors.token_rest:DropboxConnector"),
    CatalogueEntry(
        id="filesystem", name="Local Files",
        description="Read and search explicitly allowed local folders",
        icon="filesystem", category="local", implemented=True,
        import_path="syrix_connectors.connectors.filesystem:FilesystemConnector"),

    # ── Development and hosting ──────────────────────────────────────────────
    CatalogueEntry(
        id="github", name="GitHub",
        description="Repositories, issues and pull requests",
        icon="github", category="development", implemented=True,
        import_path="syrix_connectors.connectors.token_rest:GitHubConnector"),
    CatalogueEntry(
        id="gitlab", name="GitLab",
        description="Projects, issues and merge requests",
        icon="gitlab", category="development", implemented=True,
        import_path="syrix_connectors.connectors.token_rest:GitLabConnector"),
    CatalogueEntry(
        id="hostinger", name="Hostinger",
        description="Domains, VPS and hosting account metadata",
        icon="hostinger", category="hosting", implemented=True,
        import_path="syrix_connectors.connectors.token_rest:HostingerConnector"),

    # ── Finance and commerce ─────────────────────────────────────────────────
    CatalogueEntry(
        id="ibkr", name="Interactive Brokers",
        description="Portfolio/account reads; orders require permission and confirmation",
        icon="ibkr", category="finance", implemented=True,
        import_path="syrix_connectors.connectors.ibkr:IBKRConnector"),
    CatalogueEntry(
        id="stripe", name="Stripe",
        description="Balances, customers and payment metadata",
        icon="stripe", category="finance", implemented=True,
        import_path="syrix_connectors.connectors.token_rest:StripeConnector"),
    CatalogueEntry(
        id="coinbase", name="Coinbase",
        description="Account and transaction metadata; no trading",
        icon="coinbase", category="finance", implemented=True,
        import_path="syrix_connectors.connectors.token_rest:CoinbaseConnector"),
    CatalogueEntry(
        id="shopify", name="Shopify",
        description="Orders, products and customers",
        icon="shopify", category="commerce", implemented=True,
        import_path="syrix_connectors.connectors.token_rest:ShopifyConnector"),

    # ── Media ────────────────────────────────────────────────────────────────
    CatalogueEntry(
        id="spotify", name="Spotify",
        description="Playlists, saved tracks and playback metadata",
        icon="spotify", category="media", implemented=True,
        import_path="syrix_connectors.connectors.token_rest:SpotifyConnector"),
]

_BY_ID: Dict[str, CatalogueEntry] = {e.id: e for e in CATALOGUE}

_LOCK = threading.RLock()
_INSTANCES: Dict[str, ConnectorBase] = {}
_QUARANTINED: Dict[str, str] = {}  # id → reason (import failed / version mismatch)


def entry(connector_id: str) -> Optional[CatalogueEntry]:
    return _BY_ID.get(str(connector_id or "").strip().lower())


def is_implemented(connector_id: str) -> bool:
    e = entry(connector_id)
    return bool(e and e.implemented)


def quarantine_reason(connector_id: str) -> str:
    """Non-empty when the connector failed to load or is version-mismatched."""
    return _QUARANTINED.get(str(connector_id or "").strip().lower(), "")


def get_connector(connector_id: str) -> Optional[ConnectorBase]:
    """Instantiate (cached) an implemented connector; None otherwise.

    Quarantines on import failure or schema-version mismatch — the caller
    surfaces that as health instead of crashing mid-request.
    """
    cid = str(connector_id or "").strip().lower()
    e = _BY_ID.get(cid)
    if e is None or not e.implemented or not e.import_path:
        return None
    with _LOCK:
        if cid in _INSTANCES:
            return _INSTANCES[cid]
        if cid in _QUARANTINED:
            return None
        try:
            module_name, _, class_name = e.import_path.partition(":")
            mod = importlib.import_module(module_name)
            cls = getattr(mod, class_name)
            inst = cls()
            if not isinstance(inst, ConnectorBase):
                raise TypeError(f"{e.import_path} is not a ConnectorBase")
            if int(getattr(inst, "schema_version", 0)) != SCHEMA_VERSION:
                _QUARANTINED[cid] = (
                    f"schema_version {getattr(inst, 'schema_version', 0)} "
                    f"!= framework {SCHEMA_VERSION}"
                )
                obs.report(
                    "connectors.registry.version_mismatch",
                    RuntimeError(_QUARANTINED[cid]),
                    connector=cid, level="error",
                )
                return None
            _INSTANCES[cid] = inst
            return inst
        except Exception as exc:
            _QUARANTINED[cid] = f"load_failed:{type(exc).__name__}"
            obs.report("connectors.registry.load", exc, connector=cid, level="error")
            return None


def loaded_connectors() -> Dict[str, ConnectorBase]:
    """Snapshot of currently loaded instances (no new loads triggered)."""
    with _LOCK:
        return dict(_INSTANCES)


def _reset_for_tests() -> None:
    """Test hook: drop instance cache + quarantine list."""
    with _LOCK:
        _INSTANCES.clear()
        _QUARANTINED.clear()


__all__ = [
    "CatalogueEntry", "CATALOGUE",
    "entry", "is_implemented", "get_connector",
    "quarantine_reason", "loaded_connectors",
]
