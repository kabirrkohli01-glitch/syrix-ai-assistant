"""Real token-authenticated REST connectors for SYRIX.

The implementations in this module deliberately share one small, audited
transport instead of duplicating HTTP/auth code across every service.

Security invariants:
* credentials are imported from the local ``.env`` only when Connect is
  pressed, then stored with Windows DPAPI;
* request URLs are assembled from class-owned HTTPS origins and fixed action
  paths (the model can never provide a URL);
* only declared arguments are sent;
* only declared response fields are returned to the brain;
* every write action is ``risk=confirm`` and defaults blocked.
"""
from __future__ import annotations

import os
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple
from urllib.parse import quote

import syrix_obs as obs

from .. import secure_store
from ..base import (
    HEALTH_AVAILABLE,
    HEALTH_CONNECTED,
    HEALTH_NEEDS_SETUP,
    PERM_CREATE,
    PERM_MODIFY,
    PERM_READ,
    PERM_SEARCH,
    PERM_SEND,
    RISK_CONFIRM,
    RISK_SAFE,
    SCHEMA_VERSION,
    ActionResult,
    ConnectorAction,
    ConnectorBase,
    NeedsReauth,
    mask_account,
)

_LIMIT_CAP = 20
_TEXT_CAP = 700
_PERSONAL_RE = re.compile(
    r"\b(?:my|our|mine|me|i|we|show|list|find|search|check|read|tell|"
    r"create|add|send|post|update|open|latest|recent|unread|today)\b",
    re.IGNORECASE,
)
_DENY_CONTEXT_RE = re.compile(
    r"\b(?:do\s+not|don['’]?t|never|without\s+(?:my\s+)?permission|"
    r"hypothetical|fictional|for\s+example|a\s+user)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RestActionSpec:
    suffix: str
    label: str
    description: str
    verb: str
    method: str
    path: str
    fields: Tuple[str, ...]
    item_path: str = ""
    arg_properties: Optional[Dict[str, Dict[str, Any]]] = None
    required: Tuple[str, ...] = ()
    query_args: Tuple[str, ...] = ()
    body_args: Tuple[str, ...] = ()
    query_map: Tuple[Tuple[str, str], ...] = ()
    body_map: Tuple[Tuple[str, str], ...] = ()
    risk: str = RISK_SAFE
    success_label: str = ""


def _schema(name: str, description: str, properties: Mapping[str, Any],
            required: Iterable[str] = ()) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": dict(properties),
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


def _dig(value: Any, path: str) -> Any:
    cur = value
    for bit in str(path or "").split("."):
        if not bit:
            continue
        if isinstance(cur, dict):
            cur = cur.get(bit)
        else:
            return None
    return cur


def _clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return ", ".join(_clean(v) for v in value[:8])
    if isinstance(value, dict):
        # Never serialize an arbitrary provider response. A tiny name/value
        # projection is useful for nested identities while remaining bounded.
        for key in ("name", "title", "email", "address", "value", "id"):
            if key in value:
                return _clean(value.get(key))
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()[:_TEXT_CAP]


class TokenRestConnector(ConnectorBase):
    """Declarative fixed-origin REST connector.

    Subclasses provide identity metadata, a token environment variable and
    action specs. Tokens are never read during import or included in errors.
    """

    schema_version = SCHEMA_VERSION
    auth_kind = "token"
    token_env = ""
    setup_url = ""
    base_url = ""
    auth_header = "Authorization"
    auth_prefix = "Bearer "
    extra_headers: Dict[str, str] = {}
    profile_path = ""
    profile_fields: Tuple[str, ...] = ("name", "email", "login", "id")
    claim_terms: Tuple[str, ...] = ()
    action_specs: Tuple[RestActionSpec, ...] = ()

    def __init__(self) -> None:
        self._actions: "OrderedDict[str, ConnectorAction]" = OrderedDict()
        for spec in self.action_specs:
            key = f"{self.id}_{spec.suffix}"
            props = dict(spec.arg_properties or {})
            if "limit" in spec.query_args and "limit" not in props:
                props["limit"] = {
                    "type": "integer", "minimum": 1, "maximum": _LIMIT_CAP,
                    "description": "Maximum number of items to return.",
                }
            self._actions[key] = ConnectorAction(
                key=key,
                label=spec.label,
                description=spec.description,
                verb=spec.verb,
                risk=spec.risk,
                timeout_s=15.0,
                tool_schema=_schema(key, spec.description, props, spec.required),
            )

    def actions(self) -> Dict[str, ConnectorAction]:
        return self._actions

    def setup_hint(self) -> str:
        return f"Set {self.token_env} in the local .env, then press Connect."

    def health(self, *, config: Dict[str, Any]) -> str:
        if not secure_store.available():
            return HEALTH_NEEDS_SETUP
        if secure_store.has_token(self.id):
            return HEALTH_CONNECTED
        return HEALTH_AVAILABLE if os.environ.get(self.token_env, "").strip() else HEALTH_NEEDS_SETUP

    def connect(self, *, config: Dict[str, Any], progress=None) -> Tuple[bool, str]:
        token = os.environ.get(self.token_env, "").strip()
        if not token:
            return False, f"missing_env:{self.token_env}"
        if not secure_store.save_token(self.id, {"access_token": token}):
            return False, "token_store_failed"
        try:
            label = self._profile_label()
        except Exception as exc:
            secure_store.delete_token(self.id)
            obs.report("connectors.token_rest.connect", exc, connector=self.id)
            if isinstance(exc, NeedsReauth):
                return False, "credential_rejected"
            return False, f"verification_failed:{type(exc).__name__}"
        return True, mask_account(label) or self.name

    def disconnect(self, *, config: Dict[str, Any]) -> None:
        secure_store.delete_token(self.id)

    def claims_query(self, text: str) -> bool:
        t = str(text or "")
        if not t or _DENY_CONTEXT_RE.search(t) or not _PERSONAL_RE.search(t):
            return False
        terms = self.claim_terms or (self.name.lower(),)
        return any(re.search(rf"\b{re.escape(term)}\b", t, re.IGNORECASE)
                   for term in terms if term)

    def suggest_action(self, text: str):
        t = str(text or "").lower()
        if not self.claims_query(t):
            return None
        safe_specs = [s for s in self.action_specs if s.risk == RISK_SAFE]
        if not safe_specs:
            return None
        # Prefer search where the user supplies a clear "for/about/from" term.
        search = next((s for s in safe_specs if s.verb == PERM_SEARCH), None)
        m = re.search(r"\b(?:for|about|from|named?)\s+([\w@.+ -]{2,60})", t)
        if search and m:
            props = search.arg_properties or {}
            key = next((k for k in ("query", "search", "q") if k in props), "")
            if key:
                return (f"{self.id}_{search.suffix}", {key: m.group(1).strip()})
        first = next((s for s in safe_specs if not s.required), None)
        return (f"{self.id}_{first.suffix}", {}) if first else None

    def execute(self, action_key: str, args: Dict[str, Any], *,
                config: Dict[str, Any], request_id: str = "") -> ActionResult:
        prefix = f"{self.id}_"
        if not action_key.startswith(prefix):
            return ActionResult(False, error="unknown_action",
                                human_message=f"Unknown {self.name} action.")
        suffix = action_key[len(prefix):]
        spec = next((s for s in self.action_specs if s.suffix == suffix), None)
        if spec is None:
            return ActionResult(False, error="unknown_action",
                                human_message=f"Unknown {self.name} action.")
        for name in spec.required:
            if not str(args.get(name) or "").strip():
                return ActionResult(False, error=f"missing_{name}",
                                    human_message=f"{spec.label} needs {name}.")
        try:
            payload = self._call(spec, args)
        except NeedsReauth:
            raise
        except Exception as exc:
            obs.report("connectors.token_rest.execute", exc,
                       connector=self.id, action=action_key)
            return ActionResult(False, error="provider_error",
                                human_message=f"{self.name} request failed.")
        content, count = self._render(spec, payload)
        if spec.risk == RISK_CONFIRM:
            label = spec.success_label or spec.label
            content = content or f"{label} completed."
        return ActionResult(True, content=content, count=count,
                            human_message=spec.success_label or "")

    def _token(self) -> str:
        doc = secure_store.load_token(self.id) or {}
        token = str(doc.get("access_token") or "")
        if not token:
            raise NeedsReauth()
        return token

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "SYRIX-Connector/1",
            self.auth_header: f"{self.auth_prefix}{self._token()}",
        }
        headers.update(self.extra_headers)
        return headers

    def _profile_label(self) -> str:
        if not self.profile_path:
            return self.name
        data = self._request("GET", self.profile_path)
        for field in self.profile_fields:
            value = _clean(_dig(data, field))
            if value:
                return value
        return self.name

    def _call(self, spec: RestActionSpec, args: Dict[str, Any]) -> Any:
        path = spec.path
        for name in spec.required:
            marker = "{" + name + "}"
            if marker in path:
                path = path.replace(marker, quote(str(args.get(name) or ""), safe=""))
        params: Dict[str, Any] = {}
        query_names = dict(spec.query_map)
        for name in spec.query_args:
            value = args.get(name)
            if value not in (None, ""):
                api_name = query_names.get(name, name)
                params[api_name] = min(_LIMIT_CAP, max(1, int(value))) if name == "limit" else value
        body_names = dict(spec.body_map)
        body = {body_names.get(name, name): args.get(name) for name in spec.body_args
                if args.get(name) not in (None, "")}
        return self._request(spec.method, path, params=params or None, json=body or None)

    def _base_url(self) -> str:
        return self.base_url

    def _request(self, method: str, path: str, **kwargs) -> Any:
        import requests

        origin = self._base_url().rstrip("/")
        if not origin.startswith("https://"):
            raise RuntimeError("connector_origin_must_be_https")
        url = origin + "/" + str(path or "").lstrip("/")
        response = requests.request(
            method.upper(), url, headers=self._headers(), timeout=12, **kwargs
        )
        if response.status_code in (401, 403):
            raise NeedsReauth()
        response.raise_for_status()
        if response.status_code == 204 or not response.content:
            return {}
        data = response.json()
        if isinstance(data, dict) and data.get("ok") is False:
            code = str(data.get("error") or "provider_rejected")
            if code in {"invalid_auth", "not_authed", "account_inactive", "token_revoked"}:
                raise NeedsReauth()
            raise RuntimeError(f"provider_rejected:{code[:60]}")
        return data

    def _render(self, spec: RestActionSpec, payload: Any) -> Tuple[str, int]:
        rows = _dig(payload, spec.item_path) if spec.item_path else payload
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            rows = []
        rows = rows[:_LIMIT_CAP]
        lines = [f"{self.name} — {spec.label} ({len(rows)} item(s))"]
        for index, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                continue
            cells = []
            for field in spec.fields:
                value = _clean(_dig(row, field))
                if value:
                    cells.append(f"{field.rsplit('.', 1)[-1]}: {value}")
            if cells:
                lines.append(f"{index}. " + " | ".join(cells))
        if len(lines) == 1:
            lines.append(spec.success_label or "No matching items.")
        return "\n".join(lines), len(rows)


P_QUERY = {"query": {"type": "string", "description": "Search text."}}
P_ID = {"item_id": {"type": "string", "description": "Exact item identifier."}}
P_TEXT = {"text": {"type": "string", "description": "Text content."}}
P_TITLE = {"title": {"type": "string", "description": "Title."}}
P_CHANNEL_TEXT = {
    "channel_id": {"type": "string", "description": "Channel identifier."},
    "text": {"type": "string", "description": "Message text."},
}


class GitHubConnector(TokenRestConnector):
    id, name, icon, category = "github", "GitHub", "github", "development"
    description = "Repositories, issues, pull requests and controlled issue creation"
    token_env = "SYRIX_GITHUB_TOKEN"
    setup_url = "https://github.com/settings/personal-access-tokens/new"
    base_url = "https://api.github.com"
    extra_headers = {"X-GitHub-Api-Version": "2022-11-28"}
    profile_path = "/user"
    claim_terms = ("github", "repo", "repository", "pull request", "issue")
    action_specs = (
        RestActionSpec("list_repos", "List repositories", "List repositories available to you.",
                       PERM_READ, "GET", "/user/repos",
                       ("full_name", "visibility", "updated_at", "html_url"),
                       query_args=("limit",), query_map=(("limit", "per_page"),)),
        RestActionSpec("search_issues", "Search issues", "Search GitHub issues and pull requests.",
                       PERM_SEARCH, "GET", "/search/issues",
                       ("title", "state", "repository_url", "html_url"), item_path="items",
                       arg_properties=P_QUERY, required=("query",),
                       query_args=("query", "limit"),
                       query_map=(("query", "q"), ("limit", "per_page"))),
        RestActionSpec("create_issue", "Create issue", "Create an issue in a named repository.",
                       PERM_CREATE, "POST", "/repos/{repo}/issues",
                       ("title", "state", "html_url"),
                       arg_properties={
                           "repo": {"type": "string", "description": "owner/repository"},
                           "title": P_TITLE["title"],
                           "body": {"type": "string", "description": "Issue body."},
                       }, required=("repo", "title"), body_args=("title", "body"),
                       risk=RISK_CONFIRM, success_label="Issue created."),
    )

    def _call(self, spec: RestActionSpec, args: Dict[str, Any]) -> Any:
        if spec.suffix != "create_issue":
            return super()._call(spec, args)
        repo = str(args.get("repo") or "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise ValueError("invalid_repository")
        owner, name = repo.split("/", 1)
        body = {key: args.get(key) for key in ("title", "body")
                if args.get(key) not in (None, "")}
        return self._request(
            "POST", f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}/issues",
            json=body,
        )


class NotionConnector(TokenRestConnector):
    id, name, icon, category = "notion", "Notion", "notion", "work"
    description = "Search pages and databases; create pages under explicit confirmation"
    token_env = "SYRIX_NOTION_TOKEN"
    setup_url = "https://www.notion.so/my-integrations"
    base_url = "https://api.notion.com"
    extra_headers = {"Notion-Version": "2022-06-28", "Content-Type": "application/json"}
    profile_path = "/v1/users/me"
    profile_fields = ("name", "bot.owner.user.name", "id")
    claim_terms = ("notion", "notion page", "notion database")
    action_specs = (
        RestActionSpec("search", "Search workspace", "Search pages and databases in Notion.",
                       PERM_SEARCH, "POST", "/v1/search",
                       ("object", "id", "url", "last_edited_time"), item_path="results",
                       arg_properties=P_QUERY, body_args=("query",)),
    )


class SlackConnector(TokenRestConnector):
    id, name, icon, category = "slack", "Slack", "slack", "communication"
    description = "Read channels and messages; send only with confirmation"
    token_env = "SYRIX_SLACK_TOKEN"
    setup_url = "https://api.slack.com/apps"
    base_url = "https://slack.com/api"
    profile_path = "/auth.test"
    profile_fields = ("user", "team", "user_id")
    claim_terms = ("slack", "slack channel", "slack message")
    action_specs = (
        RestActionSpec("list_channels", "List channels", "List Slack channels.",
                       PERM_READ, "GET", "/conversations.list",
                       ("id", "name", "topic.value"), item_path="channels",
                       query_args=("limit",)),
        RestActionSpec("history", "Read channel messages", "Read recent messages in one Slack channel.",
                       PERM_READ, "GET", "/conversations.history",
                       ("ts", "user", "text"), item_path="messages",
                       arg_properties={"channel": {"type": "string", "description": "Channel ID."}},
                       required=("channel",), query_args=("channel", "limit")),
        RestActionSpec("send_message", "Send message", "Send a Slack channel message.",
                       PERM_SEND, "POST", "/chat.postMessage",
                       ("ok", "channel", "ts"),
                       arg_properties=P_CHANNEL_TEXT, required=("channel_id", "text"),
                       body_args=("channel_id", "text"),
                       body_map=(("channel_id", "channel"),), risk=RISK_CONFIRM,
                       success_label="Slack message sent."),
    )


class DiscordConnector(TokenRestConnector):
    id, name, icon, category = "discord", "Discord", "discord", "communication"
    description = "Read servers and channel messages; post only with confirmation"
    token_env = "SYRIX_DISCORD_BOT_TOKEN"
    setup_url = "https://discord.com/developers/applications"
    base_url = "https://discord.com/api/v10"
    auth_prefix = "Bot "
    profile_path = "/users/@me"
    profile_fields = ("global_name", "username", "id")
    claim_terms = ("discord", "discord server", "discord channel")
    action_specs = (
        RestActionSpec("list_servers", "List servers", "List Discord servers the bot can access.",
                       PERM_READ, "GET", "/users/@me/guilds",
                       ("id", "name", "owner")),
        RestActionSpec("channel_messages", "Read channel messages", "Read recent Discord messages.",
                       PERM_READ, "GET", "/channels/{channel_id}/messages",
                       ("id", "author.username", "timestamp", "content"),
                       arg_properties={"channel_id": {"type": "string", "description": "Channel ID."}},
                       required=("channel_id",), query_args=("limit",)),
        RestActionSpec("send_message", "Send message", "Post a Discord channel message.",
                       PERM_SEND, "POST", "/channels/{channel_id}/messages",
                       ("id", "timestamp", "content"),
                       arg_properties=P_CHANNEL_TEXT, required=("channel_id", "text"),
                       body_args=("text",), risk=RISK_CONFIRM,
                       success_label="Discord message sent."),
    )


class SpotifyConnector(TokenRestConnector):
    id, name, icon, category = "spotify", "Spotify", "spotify", "media"
    description = "Read playlists, saved music and current playback"
    token_env = "SYRIX_SPOTIFY_TOKEN"
    setup_url = "https://developer.spotify.com/dashboard"
    base_url = "https://api.spotify.com/v1"
    profile_path = "/me"
    profile_fields = ("display_name", "email", "id")
    claim_terms = ("spotify", "playlist", "saved songs")
    action_specs = (
        RestActionSpec("playlists", "List playlists", "List your Spotify playlists.",
                       PERM_READ, "GET", "/me/playlists",
                       ("id", "name", "tracks.total", "external_urls.spotify"), item_path="items",
                       query_args=("limit",)),
        RestActionSpec("saved_tracks", "List saved tracks", "List your saved Spotify tracks.",
                       PERM_READ, "GET", "/me/tracks",
                       ("track.id", "track.name", "track.artists", "added_at"), item_path="items",
                       query_args=("limit",)),
    )


class DropboxConnector(TokenRestConnector):
    id, name, icon, category = "dropbox", "Dropbox", "dropbox", "storage"
    description = "List, search and inspect Dropbox file metadata"
    token_env = "SYRIX_DROPBOX_TOKEN"
    setup_url = "https://www.dropbox.com/developers/apps"
    base_url = "https://api.dropboxapi.com/2"
    profile_path = "/users/get_current_account"
    profile_fields = ("name.display_name", "email", "account_id")
    claim_terms = ("dropbox", "dropbox file", "dropbox folder")
    action_specs = (
        RestActionSpec("list_files", "List files", "List Dropbox files and folders.",
                       PERM_READ, "POST", "/files/list_folder",
                       ("name", "path_display", "client_modified", "size"), item_path="entries",
                       arg_properties={"path": {"type": "string", "description": "Folder path or blank for root."}},
                       body_args=("path",)),
        RestActionSpec("search", "Search files", "Search Dropbox filenames.",
                       PERM_SEARCH, "POST", "/files/search_v2",
                       ("metadata.metadata.name", "metadata.metadata.path_display"),
                       item_path="matches", arg_properties=P_QUERY, required=("query",),
                       body_args=("query",)),
    )

    def _profile_label(self) -> str:
        data = self._request("POST", "/users/get_current_account", json={})
        for field in self.profile_fields:
            value = _clean(_dig(data, field))
            if value:
                return value
        return self.name

    def _call(self, spec: RestActionSpec, args: Dict[str, Any]) -> Any:
        if spec.suffix == "list_files":
            return self._request(
                "POST", spec.path,
                json={"path": str(args.get("path") or ""), "limit": _LIMIT_CAP},
            )
        return super()._call(spec, args)


class MicrosoftGraphConnector(TokenRestConnector):
    base_url = "https://graph.microsoft.com/v1.0"
    profile_path = "/me"
    profile_fields = ("displayName", "mail", "userPrincipalName", "id")
    setup_url = "https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade"


class OutlookConnector(MicrosoftGraphConnector):
    id, name, icon, category = "outlook", "Outlook", "outlook", "communication"
    description = "Read and search Outlook mail and calendar; sends require confirmation"
    token_env = "SYRIX_OUTLOOK_TOKEN"
    claim_terms = ("outlook", "outlook email", "microsoft email", "office 365 mail")
    extra_headers = {"ConsistencyLevel": "eventual", "Content-Type": "application/json"}
    action_specs = (
        RestActionSpec("list_messages", "List email", "List recent Outlook email.",
                       PERM_READ, "GET", "/me/messages",
                       ("id", "subject", "from.emailAddress.name", "from.emailAddress.address",
                        "receivedDateTime", "isRead", "bodyPreview"), item_path="value",
                       query_args=("limit",), query_map=(("limit", "$top"),)),
        RestActionSpec("search_messages", "Search email", "Search Outlook email.",
                       PERM_SEARCH, "GET", "/me/messages",
                       ("id", "subject", "from.emailAddress.address", "receivedDateTime",
                        "bodyPreview"), item_path="value",
                       arg_properties=P_QUERY, required=("query",), query_args=("query", "limit"),
                       query_map=(("query", "$search"), ("limit", "$top"))),
        RestActionSpec("calendar", "View calendar", "List upcoming Outlook calendar events.",
                       PERM_READ, "GET", "/me/events",
                       ("id", "subject", "start.dateTime", "end.dateTime", "location.displayName"),
                       item_path="value", query_args=("limit",),
                       query_map=(("limit", "$top"),)),
        RestActionSpec("send_mail", "Send email", "Send an Outlook email.",
                       PERM_SEND, "POST", "/me/sendMail", (),
                       arg_properties={
                           "to": {"type": "string", "description": "Recipient email."},
                           "subject": P_TITLE["title"],
                           "body": {"type": "string", "description": "Email body."},
                       }, required=("to", "subject", "body"),
                       body_args=("to", "subject", "body"), risk=RISK_CONFIRM,
                       success_label="Outlook email sent."),
    )

    def _call(self, spec: RestActionSpec, args: Dict[str, Any]) -> Any:
        if spec.suffix == "send_mail":
            body = {
                "message": {
                    "subject": str(args.get("subject") or ""),
                    "body": {
                        "contentType": "Text",
                        "content": str(args.get("body") or ""),
                    },
                    "toRecipients": [{
                        "emailAddress": {"address": str(args.get("to") or "")}
                    }],
                },
                "saveToSentItems": True,
            }
            return self._request("POST", spec.path, json=body)
        if spec.suffix == "search_messages":
            copied = dict(args)
            copied["query"] = f'"{str(args.get("query") or "").replace(chr(34), "")}"'
            return super()._call(spec, copied)
        return super()._call(spec, args)


class OneDriveConnector(MicrosoftGraphConnector):
    id, name, icon, category = "onedrive", "OneDrive", "onedrive", "storage"
    description = "List and search OneDrive file metadata"
    token_env = "SYRIX_ONEDRIVE_TOKEN"
    claim_terms = ("onedrive", "one drive", "microsoft files")
    action_specs = (
        RestActionSpec("list_files", "List files", "List files in your OneDrive root.",
                       PERM_READ, "GET", "/me/drive/root/children",
                       ("id", "name", "size", "lastModifiedDateTime", "webUrl"), item_path="value",
                       query_args=("limit",), query_map=(("limit", "$top"),)),
        RestActionSpec("search", "Search files", "Search OneDrive filenames.",
                       PERM_SEARCH, "GET", "/me/drive/root/search(q='{query}')",
                       ("id", "name", "size", "lastModifiedDateTime", "webUrl"), item_path="value",
                       arg_properties=P_QUERY, required=("query",)),
    )


class TodoistConnector(TokenRestConnector):
    id, name, icon, category = "todoist", "Todoist", "todoist", "work"
    description = "Read and create tasks with controlled permissions"
    token_env = "SYRIX_TODOIST_TOKEN"
    setup_url = "https://app.todoist.com/app/settings/integrations/developer"
    base_url = "https://api.todoist.com/api/v1"
    profile_path = "/projects"
    profile_fields = ("0.name",)
    claim_terms = ("todoist", "todo list", "task list")
    action_specs = (
        RestActionSpec("list_tasks", "List tasks", "List active Todoist tasks.",
                       PERM_READ, "GET", "/tasks",
                       ("id", "content", "description", "due.date", "priority"),
                       item_path="results", query_args=("limit",)),
        RestActionSpec("create_task", "Create task", "Create a Todoist task.",
                       PERM_CREATE, "POST", "/tasks",
                       ("id", "content", "due.date"),
                       arg_properties={
                           "content": {"type": "string", "description": "Task text."},
                           "due_string": {"type": "string", "description": "Natural-language due date."},
                       }, required=("content",), body_args=("content", "due_string"),
                       risk=RISK_CONFIRM, success_label="Todoist task created."),
    )


class TrelloConnector(TokenRestConnector):
    id, name, icon, category = "trello", "Trello", "trello", "work"
    description = "Read boards and cards"
    token_env = "SYRIX_TRELLO_TOKEN"
    setup_url = "https://trello.com/power-ups/admin"
    base_url = "https://api.trello.com/1"
    profile_path = "/members/me"
    profile_fields = ("fullName", "username", "id")
    claim_terms = ("trello", "trello board", "trello card")
    action_specs = (
        RestActionSpec("boards", "List boards", "List your Trello boards.",
                       PERM_READ, "GET", "/members/me/boards",
                       ("id", "name", "url", "dateLastActivity")),
        RestActionSpec("cards", "List board cards", "List cards on a Trello board.",
                       PERM_READ, "GET", "/boards/{board_id}/cards",
                       ("id", "name", "due", "url"),
                       arg_properties={"board_id": {"type": "string", "description": "Board ID."}},
                       required=("board_id",)),
    )

    def setup_hint(self) -> str:
        return "Set SYRIX_TRELLO_TOKEN as <api_key>:<api_token> in the local .env, then press Connect."

    def _headers(self) -> Dict[str, str]:
        return {"Accept": "application/json", "User-Agent": "SYRIX-Connector/1"}

    def _request(self, method: str, path: str, **kwargs) -> Any:
        import requests

        combined = self._token()
        api_key, sep, api_token = combined.partition(":")
        if not sep or not api_key or not api_token:
            raise NeedsReauth()
        params = dict(kwargs.pop("params", None) or {})
        params.update({"key": api_key, "token": api_token})
        response = requests.request(
            method.upper(), self.base_url.rstrip("/") + "/" + path.lstrip("/"),
            headers=self._headers(), params=params, timeout=12, **kwargs
        )
        if response.status_code in (401, 403):
            raise NeedsReauth()
        response.raise_for_status()
        return response.json() if response.content else {}


class JiraConnector(TokenRestConnector):
    id, name, icon, category = "jira", "Jira", "jira", "work"
    description = "Search Jira issues and create controlled work items"
    token_env = "SYRIX_JIRA_TOKEN"
    setup_url = "https://id.atlassian.com/manage-profile/security/api-tokens"
    base_url = "https://api.atlassian.com"
    profile_path = "/me"
    claim_terms = ("jira", "jira issue", "jira ticket")
    action_specs = (
        RestActionSpec("search_issues", "Search issues", "Search Jira issues with JQL.",
                       PERM_SEARCH, "GET", "/ex/jira/{cloud_id}/rest/api/3/search/jql",
                       ("key", "fields.summary", "fields.status.name", "fields.assignee.displayName"),
                       item_path="issues",
                       arg_properties={
                           "cloud_id": {"type": "string", "description": "Atlassian cloud ID."},
                           "jql": {"type": "string", "description": "Jira query language expression."},
                       }, required=("cloud_id", "jql"), query_args=("jql", "limit"),
                       query_map=(("limit", "maxResults"),)),
    )


class ConfluenceConnector(TokenRestConnector):
    id, name, icon, category = "confluence", "Confluence", "confluence", "work"
    description = "Search and read Confluence page metadata"
    token_env = "SYRIX_CONFLUENCE_TOKEN"
    setup_url = "https://id.atlassian.com/manage-profile/security/api-tokens"
    base_url = "https://api.atlassian.com"
    profile_path = "/me"
    claim_terms = ("confluence", "confluence page", "wiki")
    action_specs = (
        RestActionSpec("search", "Search pages", "Search Confluence content with CQL.",
                       PERM_SEARCH, "GET", "/ex/confluence/{cloud_id}/wiki/rest/api/search",
                       ("content.id", "content.title", "content.type", "url"), item_path="results",
                       arg_properties={
                           "cloud_id": {"type": "string", "description": "Atlassian cloud ID."},
                           "cql": {"type": "string", "description": "Confluence query."},
                       }, required=("cloud_id", "cql"), query_args=("cql", "limit")),
    )


class LinearConnector(TokenRestConnector):
    id, name, icon, category = "linear", "Linear", "linear", "work"
    description = "Search Linear issues through its GraphQL API"
    token_env = "SYRIX_LINEAR_TOKEN"
    setup_url = "https://linear.app/settings/account/security"
    base_url = "https://api.linear.app"
    profile_path = "/graphql"
    profile_fields = ("data.viewer.name", "data.viewer.email", "data.viewer.id")
    claim_terms = ("linear", "linear issue", "linear task")
    action_specs = (
        RestActionSpec("issues", "List issues", "List recent Linear issues.",
                       PERM_READ, "POST", "/graphql",
                       ("node.id", "node.identifier", "node.title", "node.state.name"),
                       item_path="data.issues.edges",
                       arg_properties={"query": {"type": "string", "description": "GraphQL query; defaults to recent issues."}},
                       body_args=("query",)),
    )

    def _profile_label(self) -> str:
        data = self._request("POST", "/graphql",
                             json={"query": "{ viewer { id name email } }"})
        for field in self.profile_fields:
            value = _clean(_dig(data, field))
            if value:
                return value
        return self.name

    def setup_hint(self) -> str:
        return (
            "Set SYRIX_LINEAR_TOKEN in the local .env. Personal keys use "
            "SYRIX_LINEAR_AUTH_KIND=personal; OAuth tokens use oauth."
        )

    def _headers(self) -> Dict[str, str]:
        token = self._token()
        kind = os.environ.get("SYRIX_LINEAR_AUTH_KIND", "personal").strip().lower()
        auth = f"Bearer {token}" if kind == "oauth" else token
        return {
            "Accept": "application/json", "Content-Type": "application/json",
            "User-Agent": "SYRIX-Connector/1", "Authorization": auth,
        }

    def _request(self, method: str, path: str, **kwargs) -> Any:
        data = super()._request(method, path, **kwargs)
        if isinstance(data, dict) and data.get("errors"):
            raise RuntimeError("linear_graphql_error")
        return data

    def _call(self, spec: RestActionSpec, args: Dict[str, Any]) -> Any:
        query = str(args.get("query") or "").strip()
        if not query:
            query = (
                "{ issues(first: 20, orderBy: updatedAt) { edges { node { "
                "id identifier title state { name } } } } }"
            )
        return self._request("POST", "/graphql", json={"query": query})


class GitLabConnector(TokenRestConnector):
    id, name, icon, category = "gitlab", "GitLab", "gitlab", "development"
    description = "Read projects, issues and merge requests"
    token_env = "SYRIX_GITLAB_TOKEN"
    setup_url = "https://gitlab.com/-/user_settings/personal_access_tokens"
    base_url = "https://gitlab.com/api/v4"
    auth_header = "PRIVATE-TOKEN"
    auth_prefix = ""
    profile_path = "/user"
    profile_fields = ("name", "username", "id")
    claim_terms = ("gitlab", "merge request", "gitlab issue")
    action_specs = (
        RestActionSpec("projects", "List projects", "List GitLab projects available to you.",
                       PERM_READ, "GET", "/projects",
                       ("id", "path_with_namespace", "visibility", "web_url"),
                       query_args=("limit",), query_map=(("limit", "per_page"),)),
        RestActionSpec("issues", "List issues", "List GitLab issues assigned to you.",
                       PERM_READ, "GET", "/issues",
                       ("id", "title", "state", "web_url", "due_date"),
                       query_args=("limit",), query_map=(("limit", "per_page"),)),
    )


class HubSpotConnector(TokenRestConnector):
    id, name, icon, category = "hubspot", "HubSpot", "hubspot", "work"
    description = "Read CRM contacts and deals"
    token_env = "SYRIX_HUBSPOT_TOKEN"
    # The old /private-apps shortcut now returns 404.  Start at HubSpot's
    # hosted account login; signed-in admins can continue to Development >
    # Keys / Legacy apps according to their account's current platform.
    setup_url = "https://app.hubspot.com/login"
    base_url = "https://api.hubapi.com"
    profile_path = "/oauth/v1/access-tokens/current"
    profile_fields = ("user", "hub_domain", "hub_id")
    claim_terms = ("hubspot", "crm", "hubspot contact", "hubspot deal")
    action_specs = (
        RestActionSpec("contacts", "List contacts", "List HubSpot CRM contacts.",
                       PERM_READ, "GET", "/crm/v3/objects/contacts",
                       ("id", "properties.firstname", "properties.lastname",
                        "properties.email", "updatedAt"), item_path="results",
                       query_args=("limit",)),
        RestActionSpec("deals", "List deals", "List HubSpot CRM deals.",
                       PERM_READ, "GET", "/crm/v3/objects/deals",
                       ("id", "properties.dealname", "properties.amount",
                        "properties.dealstage", "updatedAt"), item_path="results",
                       query_args=("limit",)),
    )


class StripeConnector(TokenRestConnector):
    id, name, icon, category = "stripe", "Stripe", "stripe", "finance"
    description = "Read balances, customers and payment metadata"
    token_env = "SYRIX_STRIPE_TOKEN"
    setup_url = "https://dashboard.stripe.com/apikeys"
    base_url = "https://api.stripe.com/v1"
    profile_path = "/account"
    profile_fields = ("business_profile.name", "email", "id")
    claim_terms = ("stripe", "stripe payment", "stripe balance", "stripe customer")
    action_specs = (
        RestActionSpec("balance", "View balance", "View Stripe balance metadata.",
                       PERM_READ, "GET", "/balance",
                       ("available", "pending", "livemode")),
        RestActionSpec("customers", "List customers", "List Stripe customer metadata.",
                       PERM_READ, "GET", "/customers",
                       ("id", "name", "email", "created"), item_path="data",
                       query_args=("limit",)),
        RestActionSpec("payments", "List payments", "List recent Stripe payments.",
                       PERM_READ, "GET", "/payment_intents",
                       ("id", "amount", "currency", "status", "created"), item_path="data",
                       query_args=("limit",)),
    )


class ShopifyConnector(TokenRestConnector):
    id, name, icon, category = "shopify", "Shopify", "shopify", "commerce"
    description = "Read orders, products and customers"
    token_env = "SYRIX_SHOPIFY_TOKEN"
    setup_url = "https://admin.shopify.com/store"
    base_url = "https://placeholder.myshopify.com/admin/api/2026-07"
    auth_header = "X-Shopify-Access-Token"
    auth_prefix = ""
    profile_path = "/shop.json"
    profile_fields = ("shop.name", "shop.email", "shop.id")
    claim_terms = ("shopify", "shopify order", "shopify product")
    action_specs = (
        RestActionSpec("orders", "List orders", "List recent Shopify order metadata.",
                       PERM_READ, "GET", "/orders.json",
                       ("id", "name", "email", "financial_status", "total_price", "created_at"),
                       item_path="orders", query_args=("limit",)),
        RestActionSpec("products", "List products", "List Shopify product metadata.",
                       PERM_READ, "GET", "/products.json",
                       ("id", "title", "status", "updated_at"), item_path="products",
                       query_args=("limit",)),
    )

    def setup_hint(self) -> str:
        return (
            "Set SYRIX_SHOPIFY_TOKEN and SYRIX_SHOPIFY_STORE "
            "(your-store.myshopify.com) in the local .env, then press Connect."
        )

    def health(self, *, config: Dict[str, Any]) -> str:
        if not os.environ.get("SYRIX_SHOPIFY_STORE", "").strip():
            return HEALTH_NEEDS_SETUP
        return super().health(config=config)

    def _base_url(self) -> str:
        store = os.environ.get("SYRIX_SHOPIFY_STORE", "").strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", store):
            raise RuntimeError("invalid_shopify_store")
        return f"https://{store}/admin/api/2026-07"


class CoinbaseConnector(TokenRestConnector):
    id, name, icon, category = "coinbase", "Coinbase", "coinbase", "finance"
    description = "Read account and transaction metadata; no trading"
    token_env = "SYRIX_COINBASE_TOKEN"
    setup_url = "https://portal.cdp.coinbase.com/projects/api-keys"
    base_url = "https://api.coinbase.com/v2"
    profile_path = "/user"
    profile_fields = ("data.name", "data.email", "data.id")
    claim_terms = ("coinbase", "crypto account", "crypto balance")
    action_specs = (
        RestActionSpec("accounts", "List accounts", "List Coinbase account balances.",
                       PERM_READ, "GET", "/accounts",
                       ("id", "name", "type", "balance.amount", "balance.currency"),
                       item_path="data", query_args=("limit",)),
        RestActionSpec("transactions", "List transactions", "List Coinbase transaction metadata.",
                       PERM_READ, "GET", "/accounts/{account_id}/transactions",
                       ("id", "type", "status", "amount.amount", "amount.currency", "created_at"),
                       item_path="data",
                       arg_properties={"account_id": {"type": "string", "description": "Coinbase account ID."}},
                       required=("account_id",), query_args=("limit",)),
    )


class HostingerConnector(TokenRestConnector):
    id, name, icon, category = "hostinger", "Hostinger", "hostinger", "hosting"
    description = "Read domains, VPS instances and hosting account metadata"
    token_env = "SYRIX_HOSTINGER_API_TOKEN"
    setup_url = "https://hpanel.hostinger.com/api"
    base_url = "https://developers.hostinger.com/api"
    profile_path = "/domains/v1/portfolio"
    profile_fields = ("0.domain",)
    claim_terms = ("hostinger", "hostinger domain", "hostinger vps", "hosting account")
    action_specs = (
        RestActionSpec("domains", "List domains", "List Hostinger domain portfolio metadata.",
                       PERM_READ, "GET", "/domains/v1/portfolio",
                       ("domain", "status", "created_at", "expires_at")),
        RestActionSpec("vps", "List VPS instances", "List Hostinger VPS metadata.",
                       PERM_READ, "GET", "/vps/v1/virtual-machines",
                       ("id", "hostname", "state", "plan", "created_at")),
    )


class TeamsConnector(MicrosoftGraphConnector):
    id, name, icon, category = "teams", "Microsoft Teams", "teams", "communication"
    description = "Read teams, channels and channel messages"
    token_env = "SYRIX_TEAMS_TOKEN"
    claim_terms = ("teams", "microsoft teams", "teams channel", "teams message")
    action_specs = (
        RestActionSpec("list_teams", "List teams", "List Microsoft Teams you joined.",
                       PERM_READ, "GET", "/me/joinedTeams",
                       ("id", "displayName", "description"), item_path="value"),
        RestActionSpec("channels", "List channels", "List channels in a Microsoft Team.",
                       PERM_READ, "GET", "/teams/{team_id}/channels",
                       ("id", "displayName", "description"), item_path="value",
                       arg_properties={"team_id": {"type": "string", "description": "Team ID."}},
                       required=("team_id",)),
    )


class AsanaConnector(TokenRestConnector):
    id, name, icon, category = "asana", "Asana", "asana", "work"
    description = "Read projects and tasks; create tasks with confirmation"
    token_env = "SYRIX_ASANA_TOKEN"
    setup_url = "https://app.asana.com/0/my-apps"
    base_url = "https://app.asana.com/api/1.0"
    profile_path = "/users/me"
    profile_fields = ("data.name", "data.email", "data.gid")
    claim_terms = ("asana", "asana project", "asana task")
    action_specs = (
        RestActionSpec("workspaces", "List workspaces", "List Asana workspaces.",
                       PERM_READ, "GET", "/workspaces",
                       ("gid", "name"), item_path="data"),
        RestActionSpec("projects", "List projects", "List Asana projects.",
                       PERM_READ, "GET", "/workspaces/{workspace}/projects",
                       ("gid", "name", "archived"), item_path="data",
                       arg_properties={
                           "workspace": {"type": "string", "description": "Workspace ID."},
                       }, required=("workspace",), query_args=("limit",)),
        RestActionSpec("tasks", "List tasks", "List tasks assigned to you.",
                       PERM_READ, "GET", "/tasks",
                       ("gid", "name", "completed", "due_on"), item_path="data",
                       arg_properties={
                           "workspace": {"type": "string", "description": "Workspace ID."},
                       }, required=("workspace",), query_args=("workspace", "limit")),
        RestActionSpec("create_task", "Create task", "Create an Asana task.",
                       PERM_CREATE, "POST", "/tasks",
                       ("data.gid", "data.name"),
                       arg_properties={
                           "name": {"type": "string", "description": "Task name."},
                           "notes": {"type": "string", "description": "Task notes."},
                           "workspace": {"type": "string", "description": "Workspace ID."},
                       }, required=("name", "workspace"), body_args=("name", "notes", "workspace"),
                       risk=RISK_CONFIRM, success_label="Asana task created."),
    )

    def _call(self, spec: RestActionSpec, args: Dict[str, Any]) -> Any:
        if spec.suffix == "create_task":
            data = {key: args.get(key) for key in ("name", "notes", "workspace")
                    if args.get(key) not in (None, "")}
            return self._request("POST", "/tasks", json={"data": data})
        if spec.suffix == "tasks":
            return self._request(
                "GET", "/tasks",
                params={
                    "workspace": str(args.get("workspace") or ""),
                    "assignee": "me",
                    "limit": min(_LIMIT_CAP, max(1, int(args.get("limit") or _LIMIT_CAP))),
                },
            )
        return super()._call(spec, args)


__all__ = [
    "TokenRestConnector", "RestActionSpec", "GitHubConnector", "NotionConnector",
    "SlackConnector", "DiscordConnector", "SpotifyConnector", "DropboxConnector",
    "OutlookConnector", "OneDriveConnector", "TodoistConnector", "TrelloConnector",
    "JiraConnector", "ConfluenceConnector", "LinearConnector", "GitLabConnector",
    "HubSpotConnector", "StripeConnector", "ShopifyConnector", "CoinbaseConnector",
    "HostingerConnector", "TeamsConnector", "AsanaConnector",
]
