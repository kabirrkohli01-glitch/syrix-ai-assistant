"""HUD command surface — every connector_* command, ack contract, state push."""
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
from syrix_connectors import hud_api, manager, permissions  # noqa: E402


class TestHudCommands(kit.TempStateMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.inst = kit.register_fake()
        self.jc = sys.modules["syrix_host"]
        self.jc._hud_updates.clear()

    def _last_state(self):
        self.assertTrue(self.jc._hud_updates, "no state push happened")
        fields = self.jc._hud_updates[-1]
        self.assertIn(hud_api.STATE_KEY, fields)
        return fields[hud_api.STATE_KEY]

    def test_refresh_pushes_snapshot(self):
        ok, detail = hud_api.handle_command("connectors_refresh", {}, {})
        self.assertTrue(ok)
        snap = self._last_state()
        self.assertIn("connectors", snap)
        self.assertTrue(any(c["id"] == "gmail" for c in snap["connectors"]))

    def test_set_permission(self):
        kit.mark_connected(self.inst)
        ok, detail = hud_api.handle_command(
            "connector_set_permission",
            {"connector": "fakeconn", "action": "fk_read", "allowed": False}, {})
        self.assertTrue(ok)
        self.assertFalse(permissions.is_allowed("fakeconn",
                                                self.inst.actions()["fk_read"]))
        fk = next(c for c in self._last_state()["connectors"] if c["id"] == "fakeconn")
        row = next(p for p in fk["permissions"] if p["key"] == "fk_read")
        self.assertEqual(row["state"], "blocked")

    def test_set_permission_unknown_action(self):
        kit.mark_connected(self.inst)
        ok, detail = hud_api.handle_command(
            "connector_set_permission",
            {"connector": "fakeconn", "action": "ghost", "allowed": True}, {})
        self.assertFalse(ok)
        self.assertEqual(detail, "unknown_action")

    def test_locked_permission_rejected(self):
        kit.mark_connected(self.inst)
        ok, detail = hud_api.handle_command(
            "connector_set_permission",
            {"connector": "fakeconn", "action": "fk_never", "allowed": True}, {})
        self.assertFalse(ok)

    def test_set_task(self):
        kit.mark_connected(self.inst)
        ok, _ = hud_api.handle_command(
            "connector_set_task",
            {"connector": "fakeconn", "task": "tick", "enabled": True}, {})
        self.assertTrue(ok)
        self.assertTrue(permissions.task_enabled("fakeconn", "tick"))

    def test_disconnect(self):
        kit.mark_connected(self.inst)
        ok, detail = hud_api.handle_command(
            "connector_disconnect", {"connector": "fakeconn"}, {})
        self.assertTrue(ok)
        self.assertFalse(permissions.is_connected("fakeconn"))

    def test_confirm_and_deny_roundtrip(self):
        kit.mark_connected(self.inst)
        permissions.set_permission("fakeconn", self.inst.actions()["fk_send"], True)
        r = manager.execute("fakeconn", "fk_send", {"x": 1}, config={})
        ok, _ = hud_api.handle_command(
            "connector_confirm_action", {"confirm_id": r.confirm_id}, {})
        self.assertTrue(ok)
        self.assertEqual(self.inst.executed, [("fk_send", {"x": 1})])
        # Deny on a consumed id → not found.
        ok, _ = hud_api.handle_command(
            "connector_deny_action", {"confirm_id": r.confirm_id}, {})
        self.assertFalse(ok)

    def test_unknown_command(self):
        ok, detail = hud_api.handle_command("connector_frobnicate", {}, {})
        self.assertFalse(ok)
        self.assertIn("unknown_command", detail)

    def test_connect_unknown_connector(self):
        ok, detail = hud_api.handle_command(
            "connector_connect", {"connector": "nosuch"}, {})
        self.assertFalse(ok)
        self.assertEqual(detail, "unknown_connector")

    def test_connect_disabled_by_kill_switch(self):
        ok, detail = hud_api.handle_command(
            "connector_connect", {"connector": "fakeconn"},
            {"connectors_enabled": False})
        self.assertFalse(ok)
        self.assertEqual(detail, "disabled")

    def test_wiring_command_list_matches_handlers(self):
        """Every command the wiring registers must be handled (no dead acks)."""
        for cmd in hud_api.HUD_COMMANDS:
            ok, detail = hud_api.handle_command(cmd, {"connector": "fakeconn",
                                                      "action": "fk_read",
                                                      "task": "tick",
                                                      "confirm_id": "none"}, {})
            self.assertNotIn("unknown_command", str(detail), f"{cmd} unhandled")


if __name__ == "__main__":
    unittest.main()
