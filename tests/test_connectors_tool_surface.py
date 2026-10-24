"""Tool surface — THE guarantee: an action the user cannot run right now is
never visible to the brain. Plus the tool_executor fallthrough path."""
from __future__ import annotations

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import connectors_testkit as kit  # noqa: E402
from syrix_connectors import permissions, tool_surface  # noqa: E402


def _names(config=None):
    return [t["function"]["name"] for t in tool_surface.list_tool_schemas(config or {})]


class TestExposureGates(kit.TempStateMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.inst = kit.register_fake()

    def test_nothing_exposed_when_disconnected(self):
        self.assertEqual(_names(), [])

    def test_connected_exposes_allowed_actions_only(self):
        kit.mark_connected(self.inst)
        names = _names()
        self.assertIn("fk_read", names)
        # fk_send is destructive → blocked by default → not exposed.
        self.assertNotIn("fk_send", names)
        # fk_never is not_implemented → NEVER exposed, no matter what.
        self.assertNotIn("fk_never", names)

    def test_blocking_permission_removes_tool(self):
        kit.mark_connected(self.inst)
        self.assertIn("fk_read", _names())
        permissions.set_permission("fakeconn", self.inst.actions()["fk_read"], False)
        self.assertNotIn("fk_read", _names())

    def test_allowing_confirm_action_exposes_it(self):
        kit.mark_connected(self.inst)
        permissions.set_permission("fakeconn", self.inst.actions()["fk_send"], True)
        self.assertIn("fk_send", _names())

    def test_not_implemented_never_exposed_even_if_state_tampered(self):
        kit.mark_connected(self.inst)
        import json

        from syrix_connectors import paths

        with open(paths.state_path(), "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["connectors"]["fakeconn"]["permissions"]["fk_never"] = "allowed"
        with open(paths.state_path(), "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        self.assertNotIn("fk_never", _names())

    def test_tools_kill_switch(self):
        kit.mark_connected(self.inst)
        self.assertEqual(_names({"connectors_tools_enabled": False}), [])
        self.assertEqual(_names({"connectors_enabled": False}), [])

    def test_lookup(self):
        kit.mark_connected(self.inst)
        self.assertEqual(tool_surface.lookup("fk_read"), ("fakeconn", "fk_read"))
        self.assertTrue(tool_surface.is_connector_tool("fk_read"))
        self.assertIsNone(tool_surface.lookup("open_chart"))
        self.assertFalse(tool_surface.is_connector_tool("nonexistent_tool"))

    def test_connector_claim_requires_affirmative_real_request(self):
        self.assertFalse(tool_surface._is_affirmative_connector_request(
            "A user asks to summarize a private inbox without explicit authorization."))
        self.assertFalse(tool_surface._is_affirmative_connector_request(
            "Do not access my email; explain your privacy boundary."))
        self.assertTrue(tool_surface._is_affirmative_connector_request(
            "Please summarize my unread email inbox."))


class TestExecutorFallthrough(kit.TempStateMixin, unittest.TestCase):
    """tool_executor.execute() must route connector tools through the manager."""

    def setUp(self):
        super().setUp()
        self.inst = kit.register_fake()


    def test_execute_tool_unknown_name(self):
        res = tool_surface.execute_tool("nope", {}, config={})
        self.assertFalse(res["success"])
        self.assertEqual(res["error"], "unknown_tool")


if __name__ == "__main__":
    unittest.main()
