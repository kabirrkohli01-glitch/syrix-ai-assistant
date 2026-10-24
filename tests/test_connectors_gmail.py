"""Gmail pilot connector — action declarations, metadata-only privacy,
body extraction, reauth conversion. googleapiclient fully mocked."""
from __future__ import annotations

import base64
import os
import sys
import types
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import connectors_testkit as kit  # noqa: E402
from syrix_connectors.base import (  # noqa: E402
    RISK_NOT_IMPLEMENTED,
    SCHEMA_VERSION,
    NeedsReauth,
)
from syrix_connectors.connectors.gmail import (  # noqa: E402
    GmailConnector,
    _SCOPE_COMPOSE,
    _SCOPE_READONLY,
)


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


class FakeMessages:
    def __init__(self, listing, details):
        self._listing = listing
        self._details = details

    def list(self, **kw):
        return types.SimpleNamespace(execute=lambda: self._listing)

    def get(self, userId="me", id="", **kw):  # noqa: A002, N803
        detail = self._details.get(id, {})
        return types.SimpleNamespace(execute=lambda: detail)


class FakeDrafts:
    def __init__(self, listing, details, create_response=None):
        self._listing = listing
        self._details = details
        self._create_response = ({"id": "draft-xyz"}
                                 if create_response is None else create_response)
        self.created = []  # payloads passed to create()

    def list(self, **kw):
        return types.SimpleNamespace(execute=lambda: self._listing)

    def get(self, userId="me", id="", **kw):  # noqa: A002, N803
        detail = self._details.get(id, {})
        return types.SimpleNamespace(execute=lambda: detail)

    def create(self, userId="me", body=None):  # noqa: N803
        self.created.append(body or {})
        return types.SimpleNamespace(execute=lambda: self._create_response)


class FakeService:
    def __init__(self, listing, details, drafts_listing=None, drafts_details=None,
                 draft_create_response=None):
        self._messages = FakeMessages(listing, details)
        self.drafts_api = FakeDrafts(
            drafts_listing or {}, drafts_details or {}, draft_create_response)

    def users(self):
        svc = self

        class _U:
            def messages(self):
                return svc._messages

            def drafts(self):
                return svc.drafts_api

            def getProfile(self, userId="me"):  # noqa: N802, N803
                return types.SimpleNamespace(
                    execute=lambda: {"emailAddress": "owner@example.com"})

        return _U()


def _meta_detail(subject, sender, snippet="snip"):
    return {
        "snippet": snippet,
        "payload": {"headers": [
            {"name": "Subject", "value": subject},
            {"name": "From", "value": sender},
            {"name": "Date", "value": "Sat, 12 Jul 2026 10:00:00"},
        ]},
    }


class TestDeclarations(kit.TempStateMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.g = GmailConnector()

    def test_schema_version_matches_framework(self):
        self.assertEqual(self.g.schema_version, SCHEMA_VERSION)

    def test_oauth_requests_read_and_compose_scopes(self):
        self.assertEqual(self.g._auth.scopes, [_SCOPE_READONLY, _SCOPE_COMPOSE])

    def test_action_set(self):
        keys = list(self.g.actions().keys())
        self.assertEqual(keys, ["gmail_list_unread", "gmail_search",
                                "gmail_read_message", "gmail_list_drafts",
                                "gmail_create_draft", "gmail_send", "gmail_delete"])

    def test_create_draft_is_default_allowed_create_verb(self):
        act = self.g.actions()["gmail_create_draft"]
        self.assertEqual(act.verb, "create")
        self.assertTrue(act.default_allowed)
        from syrix_connectors import permissions

        self.assertEqual(permissions.default_permission_state(act),
                         permissions.ALLOWED)

    def test_claims_drafts_phrases(self):
        self.assertTrue(self.g.claims_query("form a draft reply for all of them"))
        self.assertTrue(self.g.claims_query("make the drafts in my inbox"))
        self.assertFalse(self.g.claims_query("draft a blog post about AI"))

    def test_claims_specific_email_understanding_requests(self):
        self.assertTrue(self.g.claims_query("what does this email say"))
        self.assertTrue(self.g.claims_query("tell me about that message"))
        self.assertFalse(self.g.claims_query("what does email mean"))

    def test_does_not_claim_meta_hypothetical_or_denied_access(self):
        prompts = (
            "Explain your privacy boundary using a synthetic example: a user asks you to summarize a private email inbox but has not explicitly authorized access.",
            "Hypothetical scenario: summarize someone's Gmail without permission.",
            "Do not access my inbox; explain what email privacy means.",
            "Suppose a user asks to check their mailbox.",
        )
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertFalse(self.g.claims_query(prompt))

    def test_explicit_personal_mailbox_requests_still_claim(self):
        for prompt in ("summarize my emails", "check my unread inbox",
                       "search my Gmail for the invoice"):
            with self.subTest(prompt=prompt):
                self.assertTrue(self.g.claims_query(prompt))

    def test_suggest_action_skips_llm_for_reads(self):
        self.assertEqual(self.g.suggest_action("Do I have any unread emails?"),
                         ("gmail_list_unread", {}))
        self.assertEqual(self.g.suggest_action("summarize my emails"),
                         ("gmail_list_unread", {}))
        self.assertEqual(self.g.suggest_action("show me my drafts"),
                         ("gmail_list_drafts", {}))
        self.assertEqual(self.g.suggest_action("search my email for invoices"),
                         ("gmail_search", {"query": "invoices"}))
        self.assertEqual(self.g.suggest_action("emails from google"),
                         ("gmail_search", {"query": "google"}))

    def test_suggest_action_defers_writing_and_specific_reads_to_llm(self):
        # Writing (needs the model to compose) → None.
        self.assertIsNone(self.g.suggest_action("draft a reply to the tradingview email"))
        self.assertIsNone(self.g.suggest_action("reply to them"))
        self.assertIsNone(self.g.suggest_action("write an email to my boss"))
        # Reading ONE specific message / "what does it say" → None.
        self.assertIsNone(self.g.suggest_action("what are the tradingview ones saying"))
        self.assertIsNone(self.g.suggest_action("read that email to me"))

    def test_send_delete_are_not_implemented_and_toolless(self):
        acts = self.g.actions()
        for key in ("gmail_send", "gmail_delete"):
            self.assertEqual(acts[key].risk, RISK_NOT_IMPLEMENTED)
            self.assertIsNone(acts[key].tool_schema)

    def test_send_delete_hard_blocked_even_if_called_directly(self):
        """Belt-and-suspenders: even a direct connector call cannot send/delete."""
        for key in ("gmail_send", "gmail_delete"):
            r = self.g.execute(key, {"to": "x@y.com", "body": "hi"}, config={})
            self.assertFalse(r.ok)
            self.assertEqual(r.error, "blocked")

    def test_read_actions_carry_tool_schemas(self):
        acts = self.g.actions()
        for key in ("gmail_list_unread", "gmail_search", "gmail_read_message",
                    "gmail_list_drafts", "gmail_create_draft"):
            self.assertEqual(acts[key].tool_schema["function"]["name"], key)

    def test_background_task_defaults_off(self):
        self.assertEqual(self.g.background_tasks[0].key, "inbox_digest")
        self.assertFalse(self.g.background_tasks[0].default_enabled)


class TestActions(kit.TempStateMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.g = GmailConnector()

    def _wire(self, listing, details):
        self.g._service = lambda: FakeService(listing, details)

    def test_list_unread_metadata_only(self):
        self._wire({"messages": [{"id": "m1"}, {"id": "m2"}]},
                   {"m1": _meta_detail("Hello", "Alice <a@x.com>"),
                    "m2": _meta_detail("World", "Bob <b@x.com>")})
        r = self.g.execute("gmail_list_unread", {"max_results": 5}, config={})
        self.assertTrue(r.ok)
        self.assertEqual(r.count, 2)
        self.assertIn("Hello", r.content)
        self.assertIn("id: m1", r.content)  # ids exposed for read_message

    def test_list_unread_empty(self):
        self._wire({}, {})
        r = self.g.execute("gmail_list_unread", {}, config={})
        self.assertTrue(r.ok)
        self.assertEqual(r.count, 0)
        self.assertIn("none found", r.content)

    def test_search_requires_query(self):
        self._wire({}, {})
        r = self.g.execute("gmail_search", {}, config={})
        self.assertFalse(r.ok)
        self.assertEqual(r.error, "missing_query")

    def test_max_results_clamped(self):
        self.assertEqual(GmailConnector._clamp(999), 10)
        self.assertEqual(GmailConnector._clamp(0), 1)
        self.assertEqual(GmailConnector._clamp("junk"), 5)

    def test_read_message_extracts_plain_body(self):
        detail = _meta_detail("Subj", "Alice <a@x.com>")
        detail["payload"]["mimeType"] = "multipart/alternative"
        detail["payload"]["parts"] = [
            {"mimeType": "text/plain",
             "body": {"data": _b64("The plain body text.")}},
            {"mimeType": "text/html",
             "body": {"data": _b64("<b>html body</b>")}},
        ]
        self._wire({}, {"m9": detail})
        r = self.g.execute("gmail_read_message", {"message_id": "m9"}, config={})
        self.assertTrue(r.ok)
        self.assertIn("The plain body text.", r.content)
        self.assertNotIn("<b>", r.content)

    def test_read_message_html_fallback_strips_tags(self):
        payload = {"mimeType": "text/html",
                   "body": {"data": _b64("<p>Only html here</p>")}}
        text = GmailConnector._extract_text(payload)
        self.assertIn("Only html here", text)
        self.assertNotIn("<p>", text)

    def test_read_message_requires_id(self):
        self._wire({}, {})
        r = self.g.execute("gmail_read_message", {}, config={})
        self.assertEqual(r.error, "missing_message_id")

    def test_service_raises_needs_reauth(self):
        self.g._auth.load_credentials = lambda: (None, "needs_reauth")
        with self.assertRaises(NeedsReauth):
            self.g._service()

    def _wire_drafts(self, svc):
        self.g._service = lambda: svc
        # Simulate a token granted the compose scope.
        self.g._auth.granted_scopes = lambda: {
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.compose",
        }
        return svc

    def test_create_draft_saves_never_sends(self):
        import email as _email

        svc = self._wire_drafts(FakeService({}, {}))
        r = self.g.execute("gmail_create_draft",
                           {"to": "alice@x.com", "subject": "Hi", "body": "Hello Alice"},
                           config={})
        self.assertTrue(r.ok)
        self.assertIn("NOT sent", r.content)
        self.assertEqual(len(svc.drafts_api.created), 1)
        # The draft rides drafts().create() — the send() API is never touched.
        raw = svc.drafts_api.created[0]["message"]["raw"]
        parsed = _email.message_from_bytes(base64.urlsafe_b64decode(raw + "==="))
        self.assertEqual(parsed["To"], "alice@x.com")
        self.assertEqual(parsed["Subject"], "Hi")
        # MIMEText base64-encodes the body inside the message — decode to check.
        body = parsed.get_payload(decode=True).decode("utf-8", "replace")
        self.assertIn("Hello Alice", body)

    def test_create_draft_reply_threads_to_original(self):
        detail = _meta_detail("Original subject", "Bob <b@x.com>")
        detail["payload"]["headers"].append(
            {"name": "Message-ID", "value": "<orig-123@mail>"})
        detail["threadId"] = "thread-9"
        svc = self._wire_drafts(FakeService({}, {"m7": detail}))
        r = self.g.execute("gmail_create_draft",
                           {"body": "Thanks Bob!", "reply_to_message_id": "m7"},
                           config={})
        self.assertTrue(r.ok)
        payload = svc.drafts_api.created[0]["message"]
        self.assertEqual(payload["threadId"], "thread-9")
        decoded = base64.urlsafe_b64decode(payload["raw"] + "===").decode("utf-8", "replace")
        self.assertIn("Subject: Re: Original subject", decoded)
        self.assertIn("In-Reply-To: <orig-123@mail>", decoded)
        self.assertIn("Bob", decoded)

    def test_create_draft_without_compose_scope_asks_reconnect(self):
        self.g._service = lambda: FakeService({}, {})
        self.g._auth.granted_scopes = lambda: {
            "https://www.googleapis.com/auth/gmail.readonly"}
        r = self.g.execute("gmail_create_draft",
                           {"to": "a@x.com", "body": "hi"}, config={})
        self.assertFalse(r.ok)
        self.assertEqual(r.error, "reconnect_required")
        self.assertIn("Disconnect", r.human_message)
        self.assertIn("permission", r.human_message)

    def test_create_draft_requires_gmail_confirmation(self):
        svc = self._wire_drafts(FakeService({}, {}, draft_create_response={}))
        r = self.g.execute("gmail_create_draft",
                           {"to": "a@x.com", "body": "hi"}, config={})
        self.assertFalse(r.ok)
        self.assertEqual(r.error, "draft_not_confirmed")
        self.assertIn("did not confirm", r.content)

    def test_reply_requires_thread_and_reply_headers(self):
        detail = _meta_detail("Original subject", "Bob <b@x.com>")
        svc = self._wire_drafts(FakeService({}, {"m7": detail}))
        r = self.g.execute("gmail_create_draft",
                           {"body": "Thanks", "reply_to_message_id": "m7"},
                           config={})
        self.assertFalse(r.ok)
        self.assertEqual(r.error, "reply_thread_not_found")
        self.assertEqual(svc.drafts_api.created, [])

    def test_create_draft_requires_body_and_recipient(self):
        svc = self._wire_drafts(FakeService({}, {}))
        self.assertEqual(self.g.execute("gmail_create_draft", {}, config={}).error,
                         "missing_body")
        r = self.g.execute("gmail_create_draft", {"body": "x"}, config={})
        self.assertEqual(r.error, "missing_recipient")
        self.assertEqual(svc.drafts_api.created, [])

    def test_list_drafts(self):
        drafts_listing = {"drafts": [{"id": "d1"}]}
        drafts_details = {"d1": {"message": {
            "snippet": "draft snippet",
            "payload": {"headers": [
                {"name": "Subject", "value": "Draft subject"},
                {"name": "To", "value": "carol@x.com"},
            ]},
        }}}
        svc = FakeService({}, {}, drafts_listing, drafts_details)
        self.g._service = lambda: svc
        r = self.g.execute("gmail_list_drafts", {}, config={})
        self.assertTrue(r.ok)
        self.assertEqual(r.count, 1)
        self.assertIn("Draft subject", r.content)
        self.assertIn("carol@x.com", r.content)

    def test_run_task_inbox_digest(self):
        self._wire({"messages": [{"id": "m1"}]},
                   {"m1": _meta_detail("Hi", "Alice <a@x.com>")})
        notice = self.g.run_task("inbox_digest", config={})
        self.assertIn("1 unread", notice)
        self._wire({}, {})
        self.assertIsNone(self.g.run_task("inbox_digest", config={}))
        self.assertIsNone(self.g.run_task("unknown_task", config={}))


if __name__ == "__main__":
    unittest.main()
