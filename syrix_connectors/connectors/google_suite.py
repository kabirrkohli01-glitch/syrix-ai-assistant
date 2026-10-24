"""Google Calendar and Google Drive connectors using the Gmail OAuth pattern."""
from __future__ import annotations

import datetime as dt
import re
from collections import OrderedDict
from typing import Any, Dict, Tuple

import syrix_obs as obs

from ..auth.google_auth import GoogleAuthProvider, client_secrets_path
from ..base import (
    HEALTH_AVAILABLE,
    HEALTH_CONNECTED,
    HEALTH_NEEDS_SETUP,
    PERM_CREATE,
    PERM_READ,
    PERM_SEARCH,
    RISK_CONFIRM,
    SCHEMA_VERSION,
    ActionResult,
    ConnectorAction,
    ConnectorBase,
    NeedsReauth,
    mask_account,
)

_PERSONAL = re.compile(r"\b(?:my|our|me|i|show|list|find|search|check|create|add|today|tomorrow|upcoming)\b", re.I)
_DENY = re.compile(r"\b(?:do\s+not|don['’]?t|never|hypothetical|for\s+example)\b", re.I)


def _tool(name: str, description: str, props=None, required=()):
    return {
        "type": "function",
        "function": {
            "name": name, "description": description,
            "parameters": {
                "type": "object", "properties": dict(props or {}),
                "required": list(required), "additionalProperties": False,
            },
        },
    }


class _GoogleConnector(ConnectorBase):
    schema_version = SCHEMA_VERSION
    auth_kind = "oauth_google"
    scopes = ()
    api_name = ""
    api_version = ""

    def __init__(self):
        self._auth = GoogleAuthProvider(self.id, list(self.scopes))

    def setup_hint(self) -> str:
        return "Add the Google desktop OAuth client file client_secrets.json, then press Connect."

    def health(self, *, config: Dict[str, Any]) -> str:
        ok, _ = self._auth.ready()
        if not ok or not client_secrets_path():
            return HEALTH_NEEDS_SETUP
        return HEALTH_CONNECTED if self._auth.has_stored_token() else HEALTH_AVAILABLE

    def connect(self, *, config: Dict[str, Any], progress=None) -> Tuple[bool, str]:
        ok, creds = self._auth.connect(progress)
        if not ok:
            return False, str(creds)
        label = "Google account"
        try:
            from googleapiclient.discovery import build
            oauth = build("oauth2", "v2", credentials=creds, cache_discovery=False)
            profile = oauth.userinfo().get().execute()
            label = mask_account(str(profile.get("email") or "")) or label
        except Exception as exc:
            obs.report("connectors.google_suite.profile", exc, connector=self.id)
        return True, label

    def disconnect(self, *, config: Dict[str, Any]) -> None:
        self._auth.revoke_and_delete()

    def _service(self):
        creds, status = self._auth.load_credentials()
        if status != "ok" or creds is None:
            raise NeedsReauth()
        from googleapiclient.discovery import build
        return build(self.api_name, self.api_version, credentials=creds, cache_discovery=False)


class GoogleCalendarConnector(_GoogleConnector):
    id = "google_calendar"
    name = "Google Calendar"
    description = "Read schedules and create events only with confirmation"
    icon = "google_calendar"
    category = "work"
    scopes = (
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/calendar.events",
    )
    api_name, api_version = "calendar", "v3"

    def __init__(self):
        super().__init__()
        self._actions = OrderedDict((
            ("google_calendar_list_events", ConnectorAction(
                "google_calendar_list_events", "View events",
                "List upcoming Google Calendar events.", PERM_READ,
                tool_schema=_tool("google_calendar_list_events",
                                  "List upcoming events from the user's connected Google Calendar.",
                                  {"days": {"type": "integer", "minimum": 1, "maximum": 30}}),
            )),
            ("google_calendar_create_event", ConnectorAction(
                "google_calendar_create_event", "Create event",
                "Create a calendar event after explicit confirmation.", PERM_CREATE,
                risk=RISK_CONFIRM,
                tool_schema=_tool(
                    "google_calendar_create_event", "Create a Google Calendar event.",
                    {
                        "title": {"type": "string"},
                        "start": {"type": "string", "description": "ISO-8601 start date/time."},
                        "end": {"type": "string", "description": "ISO-8601 end date/time."},
                        "description": {"type": "string"},
                    }, ("title", "start", "end"),
                ),
            )),
        ))

    def actions(self):
        return self._actions

    def claims_query(self, text: str) -> bool:
        t = str(text or "")
        return bool(_PERSONAL.search(t) and not _DENY.search(t)
                    and re.search(r"\b(?:google\s+calendar|calendar|schedule|events?|meetings?)\b", t, re.I))

    def suggest_action(self, text: str):
        t = str(text or "")
        if self.claims_query(t) and not re.search(r"\b(?:create|add|book|schedule|make)\b", t, re.I):
            days = 1 if re.search(r"\b(?:today|tomorrow)\b", t, re.I) else 7
            return "google_calendar_list_events", {"days": days}
        return None

    def execute(self, action_key: str, args: Dict[str, Any], *,
                config: Dict[str, Any], request_id: str = "") -> ActionResult:
        svc = self._service()
        if action_key == "google_calendar_list_events":
            days = max(1, min(30, int(args.get("days") or 7)))
            now = dt.datetime.now(dt.timezone.utc)
            end = now + dt.timedelta(days=days)
            data = svc.events().list(
                calendarId="primary", timeMin=now.isoformat(),
                timeMax=end.isoformat(), singleEvents=True, orderBy="startTime",
                maxResults=20,
            ).execute()
            rows = list(data.get("items") or [])[:20]
            lines = [f"Google Calendar — {len(rows)} upcoming event(s)"]
            for i, item in enumerate(rows, 1):
                start = (item.get("start") or {}).get("dateTime") or (item.get("start") or {}).get("date") or ""
                lines.append(f"{i}. {item.get('summary') or '(untitled)'} | {start} | {item.get('location') or ''}")
            return ActionResult(True, "\n".join(lines), count=len(rows))
        if action_key == "google_calendar_create_event":
            title = str(args.get("title") or "").strip()
            start, end = str(args.get("start") or "").strip(), str(args.get("end") or "").strip()
            if not title or not start or not end:
                return ActionResult(False, error="missing_event_fields",
                                    human_message="Event title, start and end are required.")
            body = {
                "summary": title,
                "description": str(args.get("description") or "")[:2000],
                "start": {"dateTime": start},
                "end": {"dateTime": end},
            }
            created = svc.events().insert(calendarId="primary", body=body).execute()
            return ActionResult(True, f"Created calendar event: {created.get('summary') or title}",
                                count=1)
        return ActionResult(False, error="unknown_action", human_message="Unknown Calendar action.")


class GoogleDriveConnector(_GoogleConnector):
    id = "google_drive"
    name = "Google Drive"
    description = "Search and read Drive file metadata and text content"
    icon = "google_drive"
    category = "storage"
    scopes = ("https://www.googleapis.com/auth/drive.readonly",)
    api_name, api_version = "drive", "v3"

    def __init__(self):
        super().__init__()
        self._actions = OrderedDict((
            ("google_drive_list_files", ConnectorAction(
                "google_drive_list_files", "List files", "List recent Google Drive files.",
                PERM_READ, tool_schema=_tool("google_drive_list_files",
                                            "List recent files in connected Google Drive."),
            )),
            ("google_drive_search", ConnectorAction(
                "google_drive_search", "Search files", "Search Google Drive filenames.",
                PERM_SEARCH, tool_schema=_tool(
                    "google_drive_search", "Search connected Google Drive filenames.",
                    {"query": {"type": "string"}}, ("query",)),
            )),
            ("google_drive_read_file", ConnectorAction(
                "google_drive_read_file", "Read file", "Read one explicitly selected text file.",
                PERM_READ, tool_schema=_tool(
                    "google_drive_read_file", "Read one selected Google Drive text document.",
                    {"file_id": {"type": "string"}}, ("file_id",)),
            )),
        ))

    def actions(self):
        return self._actions

    def claims_query(self, text: str) -> bool:
        t = str(text or "")
        return bool(_PERSONAL.search(t) and not _DENY.search(t)
                    and re.search(r"\b(?:google\s+drive|drive\s+files?|my\s+drive)\b", t, re.I))

    def suggest_action(self, text: str):
        t = str(text or "")
        if not self.claims_query(t):
            return None
        m = re.search(r"\b(?:find|search|for|named?)\s+([\w .&-]{2,60})", t, re.I)
        return ("google_drive_search", {"query": m.group(1).strip()}) if m else ("google_drive_list_files", {})

    def execute(self, action_key: str, args: Dict[str, Any], *,
                config: Dict[str, Any], request_id: str = "") -> ActionResult:
        svc = self._service()
        if action_key in ("google_drive_list_files", "google_drive_search"):
            kwargs = {
                "pageSize": 20,
                "fields": "files(id,name,mimeType,modifiedTime,size,webViewLink)",
                "orderBy": "modifiedTime desc",
            }
            if action_key == "google_drive_search":
                query = str(args.get("query") or "").replace("'", "\\'").strip()
                if not query:
                    return ActionResult(False, error="missing_query", human_message="Search text is required.")
                kwargs["q"] = f"name contains '{query}' and trashed = false"
            data = svc.files().list(**kwargs).execute()
            rows = list(data.get("files") or [])[:20]
            lines = [f"Google Drive — {len(rows)} file(s)"]
            for i, item in enumerate(rows, 1):
                lines.append(
                    f"{i}. {item.get('name') or '(untitled)'} | {item.get('mimeType') or ''} | "
                    f"{item.get('modifiedTime') or ''} | id={item.get('id') or ''}"
                )
            return ActionResult(True, "\n".join(lines), count=len(rows))
        if action_key == "google_drive_read_file":
            file_id = str(args.get("file_id") or "").strip()
            if not file_id:
                return ActionResult(False, error="missing_file_id", human_message="A file ID is required.")
            meta = svc.files().get(fileId=file_id, fields="id,name,mimeType").execute()
            mime = str(meta.get("mimeType") or "")
            if mime.startswith("application/vnd.google-apps."):
                raw = svc.files().export(fileId=file_id, mimeType="text/plain").execute()
            else:
                raw = svc.files().get_media(fileId=file_id).execute()
            text = bytes(raw).decode("utf-8", "replace")[:6000]
            return ActionResult(True, f"{meta.get('name') or 'Drive file'}:\n{text}", count=1)
        return ActionResult(False, error="unknown_action", human_message="Unknown Drive action.")


__all__ = ["GoogleCalendarConnector", "GoogleDriveConnector"]
