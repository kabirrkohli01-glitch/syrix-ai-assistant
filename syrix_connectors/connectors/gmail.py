"""Gmail connector — read/search plus unsent Gmail drafts.

Scopes requested: ``gmail.readonly`` and ``gmail.compose``. Compose is used
only for saving unsent drafts. Send and Delete are DECLARED (so the HUD shows
them as Blocked, matching the product design) but carry
``risk=not_implemented`` — the framework guarantees they can never execute
and are never exposed to the brain as tools.

Privacy contract:
  * list/search return **metadata only** (subject, sender, date, snippet).
  * a full body is fetched only by an explicit ``gmail_read_message`` for one
    message id, lives in memory for that turn, and is never persisted.
  * hud_state / audit receive counts and masked labels — never bodies.

All Google imports are lazy; the module costs nothing until an action runs.
"""
from __future__ import annotations

import base64
import re
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

import syrix_obs as obs

from ..auth.google_auth import GoogleAuthProvider, client_secrets_path
from ..base import (
    HEALTH_AVAILABLE,
    HEALTH_CONNECTED,
    HEALTH_NEEDS_SETUP,
    PERM_CREATE,
    PERM_DELETE,
    PERM_READ,
    PERM_SEARCH,
    PERM_SEND,
    RISK_NOT_IMPLEMENTED,
    SCHEMA_VERSION,
    ActionResult,
    BackgroundTask,
    ConnectorAction,
    ConnectorBase,
    NeedsReauth,
    mask_account,
)

# readonly = read/search/summarize; compose = create/update DRAFTS only as
# used here — the framework declares gmail_send not_implemented, so the
# manager can never execute a send no matter what scope the token carries.
_SCOPE_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
_SCOPE_COMPOSE = "https://www.googleapis.com/auth/gmail.compose"
_SCOPES = [_SCOPE_READONLY, _SCOPE_COMPOSE]
_MAX_RESULTS_CAP = 10
_BODY_CHAR_CAP = 4000

# Personal-mailbox claim (mirrors the intent-planner guard, but owned by the
# connector): a mailbox noun + a personal marker, minus public-topic vetoes.
# Used by ConnectorBase.claims_query so the runtime spine routes these turns
# to the tool-calling brain instead of public web research.
_RE_MAILBOX_NOUN = re.compile(
    r"\b(?:e-?mails?|g-?mails?|inbox(?:es)?|mail(?:box(?:es)?)?|drafts?)\b",
    re.IGNORECASE)
_RE_MAILBOX_PERSONAL = re.compile(
    r"\b(?:my|our|do\s+i\s+have|have\s+i\s+got|i\s+got|did\s+i\s+get|"
    r"any\s+(?:new|unread|recent)|unread|check|show\s+me|read\s+me|"
    r"summar[iy][sz]e|repl(?:y|ies)|respond|draft|write|form|make|create)\b",
    re.IGNORECASE,
)
_RE_MAILBOX_PUBLIC = re.compile(
    r"\b(?:news|leaks?|breach|scandal|outage|down|address\s+of|support|"
    r"price|stocks?|provider|company|founder|ceo|tweet|blog|essay|"
    r"document|report|contract)\b",
    re.IGNORECASE,
)
_RE_SPECIFIC_MESSAGE = re.compile(
    r"\b(?:this|that|the)\s+(?:e-?mail|g-?mail|message)\b",
    re.IGNORECASE,
)
_RE_MESSAGE_TASK = re.compile(
    r"\b(?:say(?:s|ing)?|about|read|tell\s+me|summar[iy][sz]e|"
    r"repl(?:y|ies)|respond|draft)\b",
    re.IGNORECASE,
)
_RE_NON_ACTION_CONTEXT = re.compile(
    r"\b(?:synthetic|hypothetical|fictional|imaginary)\s+(?:example|test|case|scenario)\b|"
    r"\b(?:for\s+example|as\s+an\s+example|suppose|imagine)\b|"
    r"\b(?:a\s+user|the\s+user|someone|somebody)\s+(?:asks?|says?|wants?)\b|"
    r"\b(?:do\s+not|don['’]?t|must\s+not|should\s+not|never)\s+(?:access|open|read|search|check|use)\b|"
    r"\bwithout\s+(?:my\s+|the\s+user['’]?s\s+|explicit\s+)?(?:permission|authorization|authorisation|consent)\b|"
    r"\bnot\s+(?:explicitly\s+)?authori[sz]ed\b|"
    r"\b(?:explain|describe|discuss|state)\s+(?:your\s+)?privacy\s+(?:boundary|boundaries|policy|rules?|limits?)\b",
    re.IGNORECASE,
)


def _tool(name: str, description: str, properties: Dict[str, Any],
          required: List[str]) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


class GmailConnector(ConnectorBase):
    id = "gmail"
    name = "Gmail"
    description = "Read, search, understand and save unsent email drafts"
    icon = "gmail"
    category = "communication"
    auth_kind = "oauth_google"
    schema_version = SCHEMA_VERSION
    background_tasks = (
        BackgroundTask(
            key="inbox_digest",
            label="Inbox Digest",
            description="Quiet island notice with your unread count",
            interval_s=1800.0,
            default_enabled=False,
        ),
    )

    def __init__(self) -> None:
        self._auth = GoogleAuthProvider(self.id, _SCOPES)
        self._actions: "OrderedDict[str, ConnectorAction]" = OrderedDict()
        self._actions["gmail_list_unread"] = ConnectorAction(
            key="gmail_list_unread", label="Read Emails",
            description="Read your unread emails and metadata",
            verb=PERM_READ, timeout_s=15.0,
            tool_schema=_tool(
                "gmail_list_unread",
                "List the user's unread Gmail messages (subject, sender, date, "
                "snippet — metadata only). Use when the user asks about new, "
                "unread or recent emails, or wants an inbox summary.",
                {"max_results": {
                    "type": "integer",
                    "description": "How many unread messages to return (1-10). Default 5.",
                }},
                [],
            ),
        )
        self._actions["gmail_search"] = ConnectorAction(
            key="gmail_search", label="Search Emails",
            description="Search and find emails",
            verb=PERM_SEARCH, timeout_s=15.0,
            tool_schema=_tool(
                "gmail_search",
                "Search the user's Gmail with a query (Gmail search syntax "
                "works, e.g. 'from:amazon invoice newer_than:30d'). Returns "
                "metadata only. Use when the user asks to find specific emails.",
                {
                    "query": {"type": "string",
                              "description": "The search query."},
                    "max_results": {"type": "integer",
                                    "description": "Max messages to return (1-10). Default 5."},
                },
                ["query"],
            ),
        )
        self._actions["gmail_read_message"] = ConnectorAction(
            key="gmail_read_message", label="Read Full Email",
            description="Open one email's full text for summarizing",
            verb=PERM_READ, timeout_s=15.0,
            tool_schema=_tool(
                "gmail_read_message",
                "Fetch the full plain-text body of ONE email by its message id "
                "(ids come from gmail_list_unread / gmail_search results). Use "
                "when the user asks what a specific email says or wants it "
                "summarized.",
                {"message_id": {"type": "string",
                                "description": "The Gmail message id to read."}},
                ["message_id"],
            ),
        )
        self._actions["gmail_list_drafts"] = ConnectorAction(
            key="gmail_list_drafts", label="Read Drafts",
            description="View your email drafts",
            verb=PERM_READ, timeout_s=15.0,
            tool_schema=_tool(
                "gmail_list_drafts",
                "List the user's Gmail DRAFTS (subject, recipient, snippet). "
                "Use when the user asks about their drafts or unsent emails.",
                {"max_results": {
                    "type": "integer",
                    "description": "How many drafts to return (1-10). Default 5.",
                }},
                [],
            ),
        )
        self._actions["gmail_create_draft"] = ConnectorAction(
            key="gmail_create_draft", label="Create Drafts",
            description="Write draft replies — saved to Drafts, NEVER sent",
            verb=PERM_CREATE, timeout_s=15.0, default_allowed=True,
            tool_schema=_tool(
                "gmail_create_draft",
                "Create a DRAFT email in the user's Gmail Drafts folder. The "
                "draft is ONLY saved — it is never sent (SYRIX cannot send "
                "email). Use when the user asks you to draft, write or prepare "
                "a reply/email. To draft a REPLY to a specific message, pass "
                "its reply_to_message_id (from gmail_list_unread/gmail_search) "
                "and the recipient/subject/threading are handled automatically.",
                {
                    "body": {"type": "string",
                             "description": "The full draft body text."},
                    "to": {"type": "string",
                           "description": "Recipient address. Optional when replying."},
                    "subject": {"type": "string",
                                "description": "Subject line. Optional when replying."},
                    "reply_to_message_id": {
                        "type": "string",
                        "description": "Gmail message id to reply to (sets recipient, 'Re:' subject and thread)."},
                },
                ["body"],
            ),
        )
        self._actions["gmail_send"] = ConnectorAction(
            key="gmail_send", label="Send Emails",
            description="Send emails on your behalf",
            verb=PERM_SEND, risk=RISK_NOT_IMPLEMENTED,
        )
        self._actions["gmail_delete"] = ConnectorAction(
            key="gmail_delete", label="Delete Emails",
            description="Delete or archive emails",
            verb=PERM_DELETE, risk=RISK_NOT_IMPLEMENTED,
        )

    # ── ConnectorBase interface ──────────────────────────────────────────
    def actions(self) -> Dict[str, ConnectorAction]:
        return self._actions

    def setup_hint(self) -> str:
        return "Add the Google desktop OAuth client file client_secrets.json, then press Connect."

    def claims_query(self, text: str) -> bool:
        t = str(text or "")
        return bool(
            (
                (
                    _RE_MAILBOX_NOUN.search(t)
                    and _RE_MAILBOX_PERSONAL.search(t)
                )
                or (
                    _RE_SPECIFIC_MESSAGE.search(t)
                    and _RE_MESSAGE_TASK.search(t)
                )
            )
            and not _RE_MAILBOX_PUBLIC.search(t)
            and not _RE_NON_ACTION_CONTEXT.search(t)
        )

    def suggest_action(self, text: str):
        """Skip the LLM tool-selection round for unambiguous read asks.

        Returns None for anything needing the model to write (drafts) or to
        target a specific message — those go through the normal tool round.
        """
        t = str(text or "").strip().lower()
        if not t:
            return None
        # Listing DRAFTS (plural noun) — a read. Checked before the writing
        # guard so "show me my drafts" isn't caught by the "draft" verb.
        if re.search(r"\bdrafts\b", t) and re.search(r"\b(my|list|show|any|see|view|what)\b", t):
            return ("gmail_list_drafts", {})
        # Writing / replying / reading ONE specific message → let the model
        # drive (it must compose text or pick a message id). "draft" here is the
        # singular verb ("draft a reply"), distinct from "drafts" above.
        if re.search(r"\b(draft\b|repl(?:y|ies)|respond|write|compose|"
                     r"answer\s+(?:it|them|this)|"
                     r"what\s+does\s+(?:it|this|that)\s+say|"
                     r"read\s+(?:the|that|this)\b)", t):
            return None
        # Explicit search / "from X" / "about X" / "the X ones".
        m = re.search(r"(?:from|about|regarding|to)\s+([a-z0-9][\w .&-]{1,40})", t)
        if re.search(r"\b(search|find|look\s+for)\b", t) or m:
            if m:
                return ("gmail_search", {"query": m.group(1).strip()})
            q = re.sub(r"\b(search|find|look\s+for|my|e-?mails?|for|in|the|inbox)\b",
                       " ", t).strip()
            if q:
                return ("gmail_search", {"query": q})
        # Unread / new / recent / "do I have any emails" / inbox check / summarise all.
        if re.search(r"\b(unread|new|recent|latest|any\s+e-?mail|inbox|"
                     r"do\s+i\s+have|check\s+my|summar[iy][sz]e)\b", t):
            return ("gmail_list_unread", {})
        return None

    def health(self, *, config: Dict[str, Any]) -> str:
        ok, _why = self._auth.ready()
        if not ok and not client_secrets_path():
            return HEALTH_NEEDS_SETUP
        if not ok:
            return HEALTH_NEEDS_SETUP
        return HEALTH_CONNECTED if self._auth.has_stored_token() else HEALTH_AVAILABLE

    def connect(self, *, config: Dict[str, Any], progress=None) -> Tuple[bool, str]:
        ok, creds_or_err = self._auth.connect(progress)
        if not ok:
            return False, str(creds_or_err)
        label = ""
        try:
            service = self._build_service(creds_or_err)
            profile = service.users().getProfile(userId="me").execute()
            label = mask_account(str(profile.get("emailAddress") or ""))
        except Exception as exc:
            obs.report("connectors.gmail.profile", exc)
            label = "Google account"
        return True, label

    def disconnect(self, *, config: Dict[str, Any]) -> None:
        self._auth.revoke_and_delete()

    def execute(self, action_key: str, args: Dict[str, Any], *,
                config: Dict[str, Any], request_id: str = "") -> ActionResult:
        # Hard safety wall: SYRIX NEVER sends or deletes email, by any path.
        # These are declared not_implemented (so the manager blocks them and
        # they are never exposed to the brain as tools); this second check at
        # the execution boundary guarantees it even if the connector is ever
        # invoked directly, bypassing the manager.
        if action_key in ("gmail_send", "gmail_delete"):
            return ActionResult(
                ok=False, error="blocked",
                content="SYRIX cannot send or delete emails — this is disabled.",
                human_message="I can't send or delete emails — only read them and save drafts.",
            )
        if action_key == "gmail_list_unread":
            return self._list_unread(args)
        if action_key == "gmail_search":
            return self._search(args)
        if action_key == "gmail_read_message":
            return self._read_message(args)
        if action_key == "gmail_list_drafts":
            return self._list_drafts(args)
        if action_key == "gmail_create_draft":
            return self._create_draft(args)
        return ActionResult(ok=False, error="unknown_action",
                            content=f"Gmail has no action {action_key!r}.")

    def run_task(self, task_key: str, *, config: Dict[str, Any]) -> Optional[str]:
        if task_key != "inbox_digest":
            return None
        result = self._list_unread({"max_results": 3})
        if not result.ok or not result.count:
            return None
        senders = result.human_message or ""
        return (f"Inbox: {result.count} unread"
                + (f" — latest from {senders}." if senders else "."))

    # ── Gmail API helpers (all lazy imports) ─────────────────────────────
    def _service(self):
        creds, status = self._auth.load_credentials()
        if status in ("needs_reauth",):
            raise NeedsReauth("gmail credentials expired or revoked")
        if creds is None:
            raise NeedsReauth(f"gmail credentials unavailable ({status})")
        return self._build_service(creds)

    @staticmethod
    def _build_service(creds):
        from googleapiclient.discovery import build

        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    @staticmethod
    def _clamp(n: Any, default: int = 5) -> int:
        try:
            return max(1, min(int(n), _MAX_RESULTS_CAP))
        except (TypeError, ValueError):
            return default

    def _fetch_metadata(self, service, message_ids: List[str]) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        for mid in message_ids:
            detail = (
                service.users().messages()
                .get(userId="me", id=mid, format="metadata",
                     metadataHeaders=["Subject", "From", "Date"])
                .execute()
            )
            headers = {h["name"]: h["value"]
                       for h in (detail.get("payload", {}) or {}).get("headers", [])}
            out.append({
                "id": mid,
                "subject": headers.get("Subject", "(no subject)")[:160],
                "from": headers.get("From", "")[:120],
                "date": headers.get("Date", "")[:48],
                "snippet": (detail.get("snippet") or "")[:180],
            })
        return out

    @staticmethod
    def _format_list(items: List[Dict[str, str]], heading: str) -> str:
        if not items:
            return f"{heading}: none found."
        lines = [f"{heading} ({len(items)}):"]
        for i, m in enumerate(items, 1):
            lines.append(
                f"{i}. From: {m['from']} | Date: {m['date']} | "
                f"Subject: {m['subject']} | id: {m['id']}\n   {m['snippet']}"
            )
        return "\n".join(lines)

    def _list_unread(self, args: Dict[str, Any]) -> ActionResult:
        n = self._clamp(args.get("max_results"))
        service = self._service()
        resp = (service.users().messages()
                .list(userId="me", labelIds=["UNREAD"], maxResults=n)
                .execute())
        ids = [m["id"] for m in resp.get("messages", []) if isinstance(m, dict) and m.get("id")]
        items = self._fetch_metadata(service, ids)
        senders = ", ".join(
            m["from"].split("<")[0].strip().strip('"') for m in items[:2] if m.get("from")
        )
        return ActionResult(
            ok=True,
            content=self._format_list(items, "Unread emails"),
            count=len(items),
            human_message=senders,
        )

    def _search(self, args: Dict[str, Any]) -> ActionResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return ActionResult(ok=False, error="missing_query",
                                content="No search query provided.")
        n = self._clamp(args.get("max_results"))
        service = self._service()
        resp = (service.users().messages()
                .list(userId="me", q=query, maxResults=n)
                .execute())
        ids = [m["id"] for m in resp.get("messages", []) if isinstance(m, dict) and m.get("id")]
        items = self._fetch_metadata(service, ids)
        return ActionResult(
            ok=True,
            content=self._format_list(items, f"Emails matching {query!r}"),
            count=len(items),
        )

    def _has_compose_scope(self) -> bool:
        granted = self._auth.granted_scopes()
        # Older tokens (pre-drafts) carry only gmail.readonly; broader Gmail
        # scopes (gmail.modify / mail.google.com) also cover drafts.
        return any(s in granted for s in (
            _SCOPE_COMPOSE,
            "https://www.googleapis.com/auth/gmail.modify",
            "https://mail.google.com/",
        ))

    _RECONNECT_FOR_DRAFTS = (
        "Drafts need one extra Gmail permission that your current sign-in "
        "doesn't include yet. Open the Connectors page, Disconnect Gmail, "
        "then Connect again and approve the draft permission."
    )

    def _list_drafts(self, args: Dict[str, Any]) -> ActionResult:
        n = self._clamp(args.get("max_results"))
        service = self._service()
        resp = service.users().drafts().list(userId="me", maxResults=n).execute()
        drafts = [d for d in resp.get("drafts", []) if isinstance(d, dict) and d.get("id")]
        items: List[Dict[str, str]] = []
        for d in drafts:
            detail = (service.users().drafts()
                      .get(userId="me", id=d["id"], format="metadata")
                      .execute())
            msg = detail.get("message") or {}
            headers = {h["name"]: h["value"]
                       for h in (msg.get("payload", {}) or {}).get("headers", [])}
            items.append({
                "id": str(d["id"]),
                "subject": headers.get("Subject", "(no subject)")[:160],
                "from": headers.get("To", "(no recipient)")[:120],
                "date": headers.get("Date", "")[:48],
                "snippet": (msg.get("snippet") or "")[:180],
            })
        return ActionResult(
            ok=True,
            content=self._format_list(items, "Drafts").replace("From:", "To:"),
            count=len(items),
        )

    @staticmethod
    def _header_value(value: Any) -> str:
        """Return a safe single-line RFC header value, or an empty string."""
        text = str(value or "").strip()
        if "\r" in text or "\n" in text:
            return ""
        return text

    def _create_draft(self, args: Dict[str, Any]) -> ActionResult:
        body = str(args.get("body") or "").strip()
        if not body:
            return ActionResult(ok=False, error="missing_body",
                                content="No draft body provided.")
        if not self._has_compose_scope():
            return ActionResult(ok=False, error="reconnect_required",
                                content=self._RECONNECT_FOR_DRAFTS,
                                human_message=self._RECONNECT_FOR_DRAFTS)
        to = self._header_value(args.get("to"))
        subject = self._header_value(args.get("subject"))
        if args.get("to") and not to:
            return ActionResult(ok=False, error="invalid_recipient",
                                content="The recipient contains invalid header characters.")
        if args.get("subject") and not subject:
            return ActionResult(ok=False, error="invalid_subject",
                                content="The subject contains invalid header characters.")
        reply_id = str(args.get("reply_to_message_id") or "").strip()
        service = self._service()

        thread_id = ""
        reply_headers: Dict[str, str] = {}
        if reply_id:
            original = (service.users().messages()
                        .get(userId="me", id=reply_id, format="metadata",
                             metadataHeaders=["Subject", "From", "Message-ID",
                                              "References"])
                        .execute())
            headers = {h["name"]: h["value"]
                       for h in (original.get("payload", {}) or {}).get("headers", [])}
            thread_id = str(original.get("threadId") or "")
            # A reply must not silently become a new standalone message.
            # Gmail needs the thread id plus RFC reply headers for correct
            # threading, so fail closed before calling drafts.create().
            if not thread_id:
                return ActionResult(
                    ok=False, error="reply_thread_not_found",
                    content="Gmail did not provide a thread id for that email; no draft was created.",
                )
            # Always reply to the source message's sender. Do not let a
            # conflicting caller-supplied recipient break reply semantics.
            to = self._header_value(headers.get("From"))
            msg_id = self._header_value(headers.get("Message-ID"))
            if not to or not msg_id:
                return ActionResult(
                    ok=False, error="reply_headers_missing",
                    content="Gmail did not provide enough reply metadata; no draft was created.",
                )
            orig_subject = self._header_value(headers.get("Subject"))
            if not subject:
                subject = orig_subject if orig_subject.lower().startswith("re:") \
                    else f"Re: {orig_subject}"
            references = self._header_value(headers.get("References"))
            if references and msg_id not in references.split():
                references = f"{references} {msg_id}".strip()
            reply_headers = {"In-Reply-To": msg_id,
                             "References": references or msg_id}
        if not to:
            return ActionResult(ok=False, error="missing_recipient",
                                content="No recipient — pass 'to' or a reply_to_message_id.")

        from email.mime.text import MIMEText

        mime = MIMEText(body, "plain", "utf-8")
        mime["To"] = to
        mime["Subject"] = subject or "(no subject)"
        for k, v in reply_headers.items():
            mime[k] = v
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")
        payload: Dict[str, Any] = {"message": {"raw": raw}}
        if thread_id:
            payload["message"]["threadId"] = thread_id
        draft = service.users().drafts().create(userId="me", body=payload).execute()
        draft_id = (str((draft or {}).get("id") or "").strip()
                    if isinstance(draft, dict) else "")
        # Gmail is the source of truth. Never tell the user a draft was saved
        # unless drafts.create() returned the created draft resource id.
        if not draft_id:
            return ActionResult(
                ok=False, error="draft_not_confirmed",
                content="Gmail did not confirm the draft was saved; no success is reported.",
                human_message="Gmail did not confirm the draft was saved. Please try again.",
            )
        return ActionResult(
            ok=True, count=1,
            content=(f"Draft saved to your Gmail Drafts folder (NOT sent): "
                     f"to {to}, subject {subject or '(no subject)'!r}, "
                     f"draft id {draft_id}."),
            human_message=f"Draft to {to} saved — it will not be sent.",
        )

    def _read_message(self, args: Dict[str, Any]) -> ActionResult:
        mid = str(args.get("message_id") or "").strip()
        if not mid:
            return ActionResult(ok=False, error="missing_message_id",
                                content="No message id provided.")
        service = self._service()
        detail = (service.users().messages()
                  .get(userId="me", id=mid, format="full")
                  .execute())
        headers = {h["name"]: h["value"]
                   for h in (detail.get("payload", {}) or {}).get("headers", [])}
        body = self._extract_text(detail.get("payload") or {})
        if not body:
            body = detail.get("snippet") or "(no readable text body)"
        content = (
            f"From: {headers.get('From', '')}\n"
            f"Date: {headers.get('Date', '')}\n"
            f"Subject: {headers.get('Subject', '(no subject)')}\n\n"
            f"{body[:_BODY_CHAR_CAP]}"
        )
        return ActionResult(ok=True, content=content, count=1)

    @classmethod
    def _extract_text(cls, payload: Dict[str, Any]) -> str:
        """Depth-first walk for the first text/plain part (base64url-decoded)."""
        if not isinstance(payload, dict):
            return ""
        mime = str(payload.get("mimeType") or "")
        data = ((payload.get("body") or {}).get("data") or "")
        if mime.startswith("text/plain") and data:
            try:
                return base64.urlsafe_b64decode(data + "===").decode(
                    "utf-8", errors="replace")
            except Exception as exc:
                obs.report("connectors.gmail.decode", exc)
                return ""
        for part in payload.get("parts") or []:
            text = cls._extract_text(part)
            if text:
                return text
        # Fallback: accept text/html stripped of tags when no plain part exists.
        if mime.startswith("text/html") and data:
            try:
                import re

                html = base64.urlsafe_b64decode(data + "===").decode(
                    "utf-8", errors="replace")
                return re.sub(r"<[^>]+>", " ", html)
            except Exception as exc:
                obs.report("connectors.gmail.decode_html", exc)
        return ""


__all__ = ["GmailConnector"]
