"""Hostinger Email connector using standard IMAP/SMTP.

Credentials originate only from local ``.env`` and are immediately moved
into the DPAPI vault on Connect. Mail bodies are fetched only for an explicit
message id and never persisted.
"""
from __future__ import annotations

import email
import imaplib
import os
import re
import smtplib
from collections import OrderedDict
from email.header import decode_header
from email.message import EmailMessage
from typing import Any, Dict, List, Tuple

from .. import secure_store
from ..base import (
    HEALTH_AVAILABLE,
    HEALTH_CONNECTED,
    HEALTH_NEEDS_SETUP,
    PERM_READ,
    PERM_SEARCH,
    PERM_SEND,
    RISK_CONFIRM,
    SCHEMA_VERSION,
    ActionResult,
    ConnectorAction,
    ConnectorBase,
    NeedsReauth,
    mask_account,
)

_MAX_MESSAGES = 15
_BODY_CAP = 5000


def _tool(name, description, props=None, required=()):
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


def _decode(value: str) -> str:
    out = []
    for chunk, charset in decode_header(str(value or "")):
        if isinstance(chunk, bytes):
            out.append(chunk.decode(charset or "utf-8", "replace"))
        else:
            out.append(str(chunk))
    return re.sub(r"\s+", " ", "".join(out)).strip()


class HostingerMailConnector(ConnectorBase):
    id = "hostinger_mail"
    name = "Hostinger"
    description = "Read, search and send Hostinger mail with confirmation"
    icon = "hostinger_mail"
    category = "communication"
    auth_kind = "password"
    setup_url = "https://hpanel.hostinger.com/email"
    schema_version = SCHEMA_VERSION

    def __init__(self):
        self._actions = OrderedDict((
            ("hostinger_mail_list_unread", ConnectorAction(
                "hostinger_mail_list_unread", "List unread email",
                "List unread Hostinger email metadata.", PERM_READ,
                tool_schema=_tool("hostinger_mail_list_unread",
                                  "List unread messages in connected Hostinger email."),
            )),
            ("hostinger_mail_search", ConnectorAction(
                "hostinger_mail_search", "Search email", "Search Hostinger email metadata.",
                PERM_SEARCH, tool_schema=_tool(
                    "hostinger_mail_search", "Search connected Hostinger email.",
                    {"query": {"type": "string"}}, ("query",)),
            )),
            ("hostinger_mail_read_message", ConnectorAction(
                "hostinger_mail_read_message", "Read one email",
                "Read one explicitly selected Hostinger message body.", PERM_READ,
                tool_schema=_tool(
                    "hostinger_mail_read_message", "Read one Hostinger email by IMAP message id.",
                    {"message_id": {"type": "string"}}, ("message_id",)),
            )),
            ("hostinger_mail_send", ConnectorAction(
                "hostinger_mail_send", "Send email",
                "Send Hostinger email only after explicit confirmation.", PERM_SEND,
                risk=RISK_CONFIRM,
                tool_schema=_tool(
                    "hostinger_mail_send", "Send an email through connected Hostinger mail.",
                    {
                        "to": {"type": "string"}, "subject": {"type": "string"},
                        "body": {"type": "string"},
                    }, ("to", "subject", "body")),
            )),
        ))

    def actions(self):
        return self._actions

    def setup_hint(self) -> str:
        return (
            "Set SYRIX_HOSTINGER_EMAIL_ADDRESS and SYRIX_HOSTINGER_EMAIL_PASSWORD "
            "in the local .env, then press Connect."
        )

    def health(self, *, config: Dict[str, Any]) -> str:
        if not secure_store.available():
            return HEALTH_NEEDS_SETUP
        if secure_store.has_token(self.id):
            return HEALTH_CONNECTED
        required = ("SYRIX_HOSTINGER_EMAIL_ADDRESS", "SYRIX_HOSTINGER_EMAIL_PASSWORD")
        return HEALTH_AVAILABLE if all(os.environ.get(k, "").strip() for k in required) else HEALTH_NEEDS_SETUP

    def connect(self, *, config: Dict[str, Any], progress=None) -> Tuple[bool, str]:
        address = os.environ.get("SYRIX_HOSTINGER_EMAIL_ADDRESS", "").strip()
        password = os.environ.get("SYRIX_HOSTINGER_EMAIL_PASSWORD", "").strip()
        imap_host = os.environ.get("SYRIX_HOSTINGER_IMAP_HOST", "imap.hostinger.com").strip()
        smtp_host = os.environ.get("SYRIX_HOSTINGER_SMTP_HOST", "smtp.hostinger.com").strip()
        if not address or not password:
            return False, "missing_env:SYRIX_HOSTINGER_EMAIL_ADDRESS/PASSWORD"
        doc = {
            "address": address, "password": password,
            "imap_host": imap_host, "smtp_host": smtp_host,
        }
        if not secure_store.save_token(self.id, doc):
            return False, "token_store_failed"
        try:
            with imaplib.IMAP4_SSL(imap_host, 993, timeout=12) as client:
                status, _ = client.login(address, password)
                if status != "OK":
                    raise NeedsReauth()
        except Exception:
            secure_store.delete_token(self.id)
            return False, "credential_rejected"
        return True, mask_account(address)

    def disconnect(self, *, config: Dict[str, Any]) -> None:
        secure_store.delete_token(self.id)

    def claims_query(self, text: str) -> bool:
        t = str(text or "")
        return bool(
            re.search(r"\b(?:hostinger|my\s+(?:email|mail|inbox)|unread\s+(?:email|mail))\b", t, re.I)
            and re.search(r"\b(?:show|list|find|search|check|read|tell|send|email|mail|inbox|unread)\b", t, re.I)
            and not re.search(r"\b(?:do\s+not|don['’]?t|never|hypothetical)\b", t, re.I)
        )

    def suggest_action(self, text: str):
        t = str(text or "")
        if not self.claims_query(t) or re.search(r"\b(?:send|write|reply|respond)\b", t, re.I):
            return None
        m = re.search(r"\b(?:from|about|for)\s+([\w@.+ -]{2,60})", t, re.I)
        return ("hostinger_mail_search", {"query": m.group(1).strip()}) if m else (
            "hostinger_mail_list_unread", {})

    def execute(self, action_key: str, args: Dict[str, Any], *,
                config: Dict[str, Any], request_id: str = "") -> ActionResult:
        if action_key == "hostinger_mail_send":
            return self._send(args)
        doc = self._credentials()
        with imaplib.IMAP4_SSL(doc["imap_host"], 993, timeout=12) as client:
            status, _ = client.login(doc["address"], doc["password"])
            if status != "OK":
                raise NeedsReauth()
            client.select("INBOX", readonly=True)
            if action_key == "hostinger_mail_list_unread":
                return self._search(client, "UNSEEN")
            if action_key == "hostinger_mail_search":
                query = str(args.get("query") or "").strip()
                if not query:
                    return ActionResult(False, error="missing_query", human_message="Search text is required.")
                safe = query.replace('"', "")
                return self._search(client, f'(OR SUBJECT "{safe}" FROM "{safe}")')
            if action_key == "hostinger_mail_read_message":
                message_id = str(args.get("message_id") or "").strip()
                if not message_id.isdigit():
                    return ActionResult(False, error="invalid_message_id",
                                        human_message="Choose a numeric message id from a mail listing.")
                status, data = client.fetch(message_id, "(RFC822)")
                if status != "OK" or not data or not isinstance(data[0], tuple):
                    return ActionResult(False, error="message_not_found",
                                        human_message="That message could not be read.")
                msg = email.message_from_bytes(data[0][1])
                body = self._body(msg)
                content = (
                    f"Subject: {_decode(msg.get('Subject', ''))}\n"
                    f"From: {_decode(msg.get('From', ''))}\n"
                    f"Date: {_decode(msg.get('Date', ''))}\n\n{body}"
                )
                return ActionResult(True, content[:_BODY_CAP + 1000], count=1)
        return ActionResult(False, error="unknown_action", human_message="Unknown Hostinger mail action.")

    def _credentials(self) -> Dict[str, str]:
        doc = secure_store.load_token(self.id) or {}
        if not all(doc.get(k) for k in ("address", "password", "imap_host", "smtp_host")):
            raise NeedsReauth()
        return {k: str(doc[k]) for k in ("address", "password", "imap_host", "smtp_host")}

    def _search(self, client, criterion: str) -> ActionResult:
        status, data = client.search(None, criterion)
        ids = (data[0].split() if status == "OK" and data else [])[-_MAX_MESSAGES:]
        ids.reverse()
        lines: List[str] = [f"Hostinger Email — {len(ids)} message(s)"]
        for raw_id in ids:
            mid = raw_id.decode("ascii", "ignore")
            status, item = client.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])")
            if status != "OK" or not item or not isinstance(item[0], tuple):
                continue
            msg = email.message_from_bytes(item[0][1])
            lines.append(
                f"id={mid} | Subject: {_decode(msg.get('Subject', ''))} | "
                f"From: {_decode(msg.get('From', ''))} | Date: {_decode(msg.get('Date', ''))}"
            )
        return ActionResult(True, "\n".join(lines), count=len(ids))

    def _send(self, args: Dict[str, Any]) -> ActionResult:
        to = str(args.get("to") or "").strip()
        subject = str(args.get("subject") or "").strip()
        body = str(args.get("body") or "").strip()
        if not to or not subject or not body:
            return ActionResult(False, error="missing_mail_fields",
                                human_message="Recipient, subject and body are required.")
        doc = self._credentials()
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = doc["address"], to, subject
        msg.set_content(body)
        with smtplib.SMTP_SSL(doc["smtp_host"], 465, timeout=12) as smtp:
            smtp.login(doc["address"], doc["password"])
            smtp.send_message(msg)
        return ActionResult(True, f"Email sent to {to}.", count=1)

    @staticmethod
    def _body(msg) -> str:
        chunks = []
        parts = msg.walk() if msg.is_multipart() else [msg]
        for part in parts:
            if part.get_content_type() != "text/plain":
                continue
            if "attachment" in str(part.get("Content-Disposition") or "").lower():
                continue
            raw = part.get_payload(decode=True) or b""
            chunks.append(raw.decode(part.get_content_charset() or "utf-8", "replace"))
            if sum(len(x) for x in chunks) >= _BODY_CAP:
                break
        return "\n".join(chunks).strip()[:_BODY_CAP] or "(No readable plain-text body.)"


__all__ = ["HostingerMailConnector"]
