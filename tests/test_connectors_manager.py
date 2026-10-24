"""Manager choke point — every gate in order, confirmations, timeouts,
needs_reauth conversion, audit trail. All offline; no network, no real HUD."""
from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import connectors_testkit as kit  # noqa: E402
from syrix_connectors import audit, manager, permissions  # noqa: E402
from syrix_connectors.base import NeedsReauth  # noqa: E402


class TestExecuteGates(kit.TempStateMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.inst = kit.register_fake()

    def test_kill_switch(self):
        r = manager.execute("fakeconn", "fk_read", config={"connectors_enabled": False})
        self.assertFalse(r.ok)
        self.assertEqual(r.error, "disabled")

    def test_unknown_connector(self):
        r = manager.execute("nosuch", "fk_read", config={})
        self.assertEqual(r.error, "unknown_connector")

    def test_real_connector_cannot_execute_before_connect(self):
        r = manager.execute("notion", "anything", config={})
        self.assertEqual(r.error, audit.RESULT_NOT_CONNECTED)

    def test_not_connected(self):
        r = manager.execute("fakeconn", "fk_read", config={})
        self.assertEqual(r.error, audit.RESULT_NOT_CONNECTED)
        self.assertEqual(self.inst.executed, [])

    def test_unknown_action_denied(self):
        kit.mark_connected(self.inst)
        r = manager.execute("fakeconn", "fk_ghost", config={})
        self.assertEqual(r.error, audit.RESULT_DENIED)

    def test_not_implemented_blocked(self):
        kit.mark_connected(self.inst)
        r = manager.execute("fakeconn", "fk_never", config={})
        self.assertEqual(r.error, audit.RESULT_BLOCKED)
        self.assertEqual(self.inst.executed, [])

    def test_permission_denied_and_audited(self):
        kit.mark_connected(self.inst)
        permissions.set_permission("fakeconn", self.inst.actions()["fk_read"], False)
        r = manager.execute("fakeconn", "fk_read", actor=audit.ACTOR_BRAIN,
                            reason="test query", config={})
        self.assertEqual(r.error, audit.RESULT_DENIED)
        tail = audit.recent(3)
        self.assertEqual(tail[0]["result"], audit.RESULT_DENIED)
        self.assertEqual(tail[0]["actor"], "brain")

    def test_allowed_runs_and_audits_ok(self):
        kit.mark_connected(self.inst)
        r = manager.execute("fakeconn", "fk_read", {"q": 1}, request_id="rid-1", config={})
        self.assertTrue(r.ok)
        self.assertEqual(self.inst.executed, [("fk_read", {"q": 1})])
        tail = audit.recent(2)
        self.assertEqual(tail[0]["result"], audit.RESULT_OK)
        self.assertEqual(tail[0]["request_id"], "rid-1")

    def test_thinking_panel_announce_every_execution(self):
        kit.mark_connected(self.inst)
        from syrix_core_v2 import activity_feed

        before = len([e for e in list(activity_feed._RING)
                      if e.get("provider") == "connectors"])
        manager.execute("fakeconn", "fk_read", request_id="rid-feed", config={})
        events = [e for e in list(activity_feed._RING)
                  if e.get("provider") == "connectors"][before:]
        labels = [e["label"] for e in events]
        self.assertIn("Using FakeConn connector", labels)

    def test_timeout_budget(self):
        kit.mark_connected(self.inst)
        self.inst.execute_sleep = 5.0  # action budget is 2.0s
        r = manager.execute("fakeconn", "fk_read", config={})
        self.assertEqual(r.error, "timeout")
        self.assertEqual(audit.recent(1)[0]["result"], audit.RESULT_TIMEOUT)

    def test_needs_reauth_sets_health_override(self):
        kit.mark_connected(self.inst)
        self.inst.raise_exc = NeedsReauth("expired")
        r = manager.execute("fakeconn", "fk_read", config={})
        self.assertEqual(r.error, "needs_reauth")
        self.assertEqual(manager.connector_health("fakeconn", {}), "needs_reauth")

    def test_generic_exception_becomes_clean_error(self):
        kit.mark_connected(self.inst)
        self.inst.raise_exc = RuntimeError("boom")
        r = manager.execute("fakeconn", "fk_read", config={})
        self.assertFalse(r.ok)
        self.assertEqual(r.error, "exec_failed")
        self.assertEqual(audit.recent(1)[0]["result"], audit.RESULT_ERROR)


class TestConfirmations(kit.TempStateMixin, unittest.TestCase):
    """Confirmation gate is connector-agnostic: keyed off risk only."""

    def setUp(self):
        super().setUp()
        self.inst = kit.register_fake()
        kit.mark_connected(self.inst)
        # fk_send is destructive → blocked by default; user allows it here.
        permissions.set_permission("fakeconn", self.inst.actions()["fk_send"], True)

    def test_confirm_risk_parks_instead_of_running(self):
        r = manager.execute("fakeconn", "fk_send", {"to": "x"}, config={})
        self.assertFalse(r.ok)
        self.assertTrue(r.needs_confirmation)
        self.assertTrue(r.confirm_id)
        self.assertEqual(self.inst.executed, [])
        pend = manager.pending_confirmations()
        self.assertEqual(len(pend), 1)
        self.assertNotIn("args", pend[0])  # args never leave Python

    def test_confirm_executes_once(self):
        r = manager.execute("fakeconn", "fk_send", {"to": "x"}, config={})
        r2 = manager.confirm_action(r.confirm_id, config={})
        self.assertTrue(r2.ok)
        self.assertEqual(self.inst.executed, [("fk_send", {"to": "x"})])
        # Single-use: the same id is gone.
        r3 = manager.confirm_action(r.confirm_id, config={})
        self.assertEqual(r3.error, "confirmation_expired")

    def test_deny(self):
        r = manager.execute("fakeconn", "fk_send", config={})
        self.assertTrue(manager.deny_action(r.confirm_id))
        self.assertEqual(self.inst.executed, [])
        self.assertEqual(manager.pending_confirmations(), [])
        self.assertEqual(audit.recent(1)[0]["result"], audit.RESULT_CONFIRM_DENIED)

    def test_expiry(self):
        r = manager.execute("fakeconn", "fk_send", config={})
        with manager._CONFIRM_LOCK:
            manager._PENDING_CONFIRMS[r.confirm_id]["expires_ts"] = time.time() - 1
        self.assertEqual(manager.pending_confirmations(), [])
        r2 = manager.confirm_action(r.confirm_id, config={})
        self.assertEqual(r2.error, "confirmation_expired")
        self.assertEqual(self.inst.executed, [])


class TestSnapshotAndDisconnect(kit.TempStateMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.inst = kit.register_fake()

    def test_connect_opens_setup_in_default_browser_when_credential_is_needed(self):
        self.inst.auth_kind = "token"
        self.inst.setup_url = "https://provider.example/setup"
        self.inst.health = lambda *, config: "needs_setup"
        with patch.object(manager.webbrowser, "_syrix_hud_orig_open", None, create=True):
            with patch("syrix_connectors.manager.webbrowser.open", return_value=True) as opened:
                result = manager.start_connect("fakeconn", config={})
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "setup_opened")
        opened.assert_called_once_with("https://provider.example/setup", new=2)

    def test_connect_bypasses_hud_browser_shim_for_provider_setup(self):
        self.inst.auth_kind = "token"
        self.inst.setup_url = "https://provider.example/setup"
        self.inst.health = lambda *, config: "needs_setup"
        opened = Mock(return_value=True)
        with patch.object(manager.webbrowser, "_syrix_hud_orig_open", opened, create=True):
            with patch("syrix_connectors.manager.webbrowser.open", return_value=False) as hud_open:
                result = manager.start_connect("fakeconn", config={})
        self.assertTrue(result["ok"])
        opened.assert_called_once_with("https://provider.example/setup", new=2)
        hud_open.assert_not_called()

    def test_duplicate_connect_opens_only_one_setup_tab(self):
        self.inst.auth_kind = "token"
        self.inst.setup_url = "https://provider.example/setup"
        self.inst.health = lambda *, config: "needs_setup"
        opened = Mock(return_value=True)
        with patch.object(manager.webbrowser, "_syrix_hud_orig_open", opened, create=True):
            first = manager.start_connect("fakeconn", config={})
            duplicate = manager.start_connect("fakeconn", config={})
        self.assertEqual(first["status"], "setup_opened")
        self.assertEqual(duplicate["status"], "auth_in_progress")
        opened.assert_called_once_with("https://provider.example/setup", new=2)

    def test_failed_local_connection_opens_provider_setup(self):
        self.inst.auth_kind = "local_gateway"
        self.inst.setup_url = "https://provider.example/sign-in"
        self.inst.health = lambda *, config: "available"
        opened = Mock(return_value=True)
        with patch.object(manager.webbrowser, "_syrix_hud_orig_open", opened, create=True):
            with patch.object(self.inst, "connect", return_value=(False, "gateway_not_authenticated")):
                result = manager.start_connect("fakeconn", config={})
                self.assertTrue(result["ok"])
                manager._AUTH_THREADS["fakeconn"].join(timeout=3)
        opened.assert_called_once_with("https://provider.example/sign-in", new=2)
        self.assertEqual(manager.auth_flow_state()["status"], "setup_opened")

    def test_other_connector_auth_does_not_block_this_connector(self):
        release = threading.Event()
        other = threading.Thread(target=lambda: release.wait(2), daemon=True)
        other.start()
        with manager._AUTH_LOCK:
            manager._AUTH_THREADS["google_drive"] = other
        try:
            result = manager.start_connect("fakeconn", config={})
            self.assertTrue(result["ok"])
            self.assertNotEqual(result["status"], "auth_in_progress")
            manager._AUTH_THREADS["fakeconn"].join(timeout=3)
        finally:
            release.set()
            other.join(timeout=3)

    def test_snapshot_shape(self):
        kit.mark_connected(self.inst)
        snap = manager.snapshot({})
        self.assertTrue(snap["enabled"])
        ids = [c["id"] for c in snap["connectors"]]
        self.assertIn("gmail", ids)
        self.assertIn("fakeconn", ids)
        fk = next(c for c in snap["connectors"] if c["id"] == "fakeconn")
        self.assertEqual(fk["health"], "connected")
        locked = next(p for p in fk["permissions"] if p["key"] == "fk_never")
        self.assertTrue(locked["locked"])
        self.assertEqual(locked["state"], "blocked")

    def test_disconnect_wipes_token_and_state(self):
        from syrix_connectors import secure_store

        kit.mark_connected(self.inst)
        secure_store.save_token("fakeconn", {"token": "x"})
        self.assertTrue(manager.disconnect("fakeconn", config={}))
        self.assertFalse(secure_store.has_token("fakeconn"))
        self.assertFalse(permissions.is_connected("fakeconn"))
        self.assertFalse(self.inst.connected_flag)


if __name__ == "__main__":
    unittest.main()
