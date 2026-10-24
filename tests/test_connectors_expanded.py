"""Expanded connector catalogue, routing and safety invariants."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HERE = os.path.dirname(os.path.abspath(__file__))
for _path in (_ROOT, _HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import connectors_testkit as kit  # noqa: E402
from syrix_connectors import manager, permissions, registry, tool_surface  # noqa: E402
from syrix_connectors.base import (  # noqa: E402
    DESTRUCTIVE_VERBS,
    RISK_CONFIRM,
    ConnectorBase,
)
from syrix_connectors.connectors.token_rest import (  # noqa: E402
    AsanaConnector,
    GitHubConnector,
    LinearConnector,
    OutlookConnector,
    ShopifyConnector,
    SlackConnector,
)
from syrix_connectors.connectors.filesystem import FilesystemConnector  # noqa: E402
from syrix_connectors.connectors.ibkr import IBKRConnector  # noqa: E402


class TestExpandedCatalogue(kit.TempStateMixin, unittest.TestCase):
    def test_all_entries_load_and_no_coming_soon_cards_remain(self):
        self.assertGreaterEqual(len(registry.CATALOGUE), 25)
        self.assertTrue(all(entry.implemented for entry in registry.CATALOGUE))
        failures = []
        for entry in registry.CATALOGUE:
            inst = registry.get_connector(entry.id)
            if not isinstance(inst, ConnectorBase):
                failures.append((entry.id, registry.quarantine_reason(entry.id)))
        self.assertEqual(failures, [])

    def test_tool_names_are_globally_unique(self):
        seen = {}
        for entry in registry.CATALOGUE:
            inst = registry.get_connector(entry.id)
            for key, action in inst.actions().items():
                self.assertEqual(key, action.key)
                self.assertNotIn(key, seen, f"{key} duplicated by {entry.id} and {seen.get(key)}")
                seen[key] = entry.id
        self.assertGreaterEqual(len(seen), 50)

    def test_every_destructive_action_requires_confirmation(self):
        unsafe = []
        for entry in registry.CATALOGUE:
            inst = registry.get_connector(entry.id)
            for action in inst.actions().values():
                if action.verb in DESTRUCTIVE_VERBS and action.risk != RISK_CONFIRM:
                    # Gmail intentionally declares send/delete as permanently
                    # not_implemented, which is stricter than confirmation.
                    # Draft creation is also intentionally safe: it never
                    # leaves the account and existing Gmail tests pin that.
                    if (action.risk != "not_implemented"
                            and action.key != "gmail_create_draft"):
                        unsafe.append(f"{entry.id}:{action.key}:{action.risk}")
        self.assertEqual(unsafe, [])

    def test_finance_and_work_categories_exist(self):
        categories = {entry.category for entry in registry.CATALOGUE}
        self.assertIn("finance", categories)
        self.assertIn("work", categories)
        self.assertIn("communication", categories)


class TestExpandedRouting(kit.TempStateMixin, unittest.TestCase):
    def test_explicit_outlook_wins_over_broad_gmail_mail_claim(self):
        gmail = registry.get_connector("gmail")
        outlook = registry.get_connector("outlook")
        live = [
            ("gmail", next(iter(gmail.actions().values()))),
            ("outlook", next(iter(outlook.actions().values()))),
        ]
        with patch.object(tool_surface, "_live_actions", return_value=live):
            claimed = tool_surface.claiming_connector(
                "Can you show me what emails I have in Outlook?", {})
        self.assertEqual(claimed, "outlook")

    def test_generic_mail_question_still_claims_gmail(self):
        gmail = registry.get_connector("gmail")
        live = [("gmail", next(iter(gmail.actions().values())))]
        with patch.object(tool_surface, "_live_actions", return_value=live):
            claimed = tool_surface.claiming_connector(
                "What emails do I have? Check my inbox.", {})
        self.assertEqual(claimed, "gmail")


class TestExpandedSafety(kit.TempStateMixin, unittest.TestCase):
    def test_ibkr_order_is_blocked_then_confirmation_gated(self):
        inst = registry.get_connector("ibkr")
        permissions.set_connected(inst, "U1…")
        action = inst.actions()["ibkr_place_order"]
        args = {
            "account_id": "U123", "conid": 123, "side": "BUY",
            "quantity": 1, "order_type": "MKT",
        }
        blocked = manager.execute("ibkr", action.key, args, config={})
        self.assertEqual(blocked.error, "permission_denied")
        self.assertFalse(blocked.needs_confirmation)

        permissions.set_permission("ibkr", action, True)
        pending = manager.execute("ibkr", action.key, args, config={})
        self.assertEqual(pending.error, "confirmation_required")
        self.assertTrue(pending.needs_confirmation)
        self.assertTrue(pending.confirm_id)

    def test_rest_renderer_never_dumps_undeclared_fields(self):
        connector = GitHubConnector()
        spec = connector.action_specs[0]
        content, count = connector._render(spec, [{
            "full_name": "owner/repo", "visibility": "private",
            "updated_at": "now", "html_url": "https://example.invalid/repo",
            "access_token": "must-never-appear",
        }])
        self.assertEqual(count, 1)
        self.assertIn("owner/repo", content)
        self.assertNotIn("must-never-appear", content)
        self.assertNotIn("access_token", content)

    def test_token_setup_hint_names_variable_not_value(self):
        connector = OutlookConnector()
        self.assertIn("SYRIX_OUTLOOK_TOKEN", connector.setup_hint())
        self.assertNotIn("Bearer", connector.setup_hint())

    def test_token_connectors_expose_fixed_https_setup_pages(self):
        for connector in (GitHubConnector(), OutlookConnector(), ShopifyConnector(), SlackConnector()):
            self.assertTrue(connector.setup_url.startswith("https://"))

    def test_provider_specific_payload_shapes(self):
        github = GitHubConnector()
        with patch.object(github, "_request", return_value={}) as request:
            github._call(github.action_specs[1], {"query": "is:issue bug", "limit": 7})
        self.assertEqual(request.call_args.kwargs["params"],
                         {"q": "is:issue bug", "per_page": 7})

        slack = SlackConnector()
        with patch.object(slack, "_request", return_value={}) as request:
            slack._call(slack.action_specs[2], {"channel_id": "C1", "text": "hello"})
        self.assertEqual(request.call_args.kwargs["json"],
                         {"channel": "C1", "text": "hello"})

        outlook = OutlookConnector()
        with patch.object(outlook, "_request", return_value={}) as request:
            outlook._call(
                outlook.action_specs[3],
                {"to": "a@example.com", "subject": "Hi", "body": "Body"},
            )
        payload = request.call_args.kwargs["json"]
        self.assertEqual(payload["message"]["toRecipients"][0]["emailAddress"]["address"],
                         "a@example.com")
        self.assertEqual(payload["message"]["body"]["content"], "Body")

    def test_provider_defaults_are_real_api_shapes(self):
        linear = LinearConnector()
        with patch.object(linear, "_request", return_value={}) as request:
            linear._call(linear.action_specs[0], {})
        self.assertIn("issues(first: 20", request.call_args.kwargs["json"]["query"])

        asana = AsanaConnector()
        task_spec = next(s for s in asana.action_specs if s.suffix == "tasks")
        with patch.object(asana, "_request", return_value={}) as request:
            asana._call(task_spec, {"workspace": "W1", "limit": 3})
        self.assertEqual(request.call_args.kwargs["params"]["assignee"], "me")
        self.assertEqual(request.call_args.kwargs["params"]["workspace"], "W1")

    def test_dynamic_origins_are_restricted(self):
        shopify = ShopifyConnector()
        with patch.dict(os.environ, {"SYRIX_SHOPIFY_STORE": "bad.example.com"}):
            with self.assertRaises(RuntimeError):
                shopify._base_url()
        with patch.dict(os.environ, {"SYRIX_SHOPIFY_STORE": "my-shop.myshopify.com"}):
            self.assertEqual(
                shopify._base_url(),
                "https://my-shop.myshopify.com/admin/api/2026-07",
            )

        with self.assertRaises(ValueError):
            IBKRConnector._origin({"ibkr_gateway_url": "https://broker.example.com/v1/api"})
        self.assertEqual(
            IBKRConnector._origin({"ibkr_gateway_url": "https://localhost:5000/v1/api"}),
            "https://localhost:5000/v1/api",
        )

    def test_filesystem_rejects_paths_outside_configured_roots(self):
        connector = FilesystemConnector()
        result = connector.execute(
            "filesystem_read", {"path": os.path.dirname(self._tmp)},
            config={"filesystem_connector_roots": [self._tmp]},
        )
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
