"""Permission store — conservative defaults, deny-unknown, locked actions,
task toggles, schema-version safety."""
from __future__ import annotations

import json
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
from syrix_connectors import paths, permissions  # noqa: E402
from syrix_connectors.base import SCHEMA_VERSION  # noqa: E402


class TestDefaults(kit.TempStateMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.inst = kit.register_fake()

    def test_connect_seeds_conservative_defaults(self):
        permissions.set_connected(self.inst, "fak…@test.com")
        rec = permissions.connector_record("fakeconn")
        self.assertTrue(rec["connected"])
        self.assertEqual(rec["permissions"]["fk_read"], permissions.ALLOWED)
        self.assertEqual(rec["permissions"]["fk_send"], permissions.BLOCKED)
        self.assertEqual(rec["permissions"]["fk_never"], permissions.BLOCKED)
        self.assertFalse(rec["tasks"]["tick"])  # background work is opt-in

    def test_reconnect_preserves_user_toggles(self):
        permissions.set_connected(self.inst, "a")
        permissions.set_permission("fakeconn", self.inst.actions()["fk_read"], False)
        permissions.set_task("fakeconn", "tick", True)
        permissions.set_connected(self.inst, "a")  # reconnect
        rec = permissions.connector_record("fakeconn")
        self.assertEqual(rec["permissions"]["fk_read"], permissions.BLOCKED)
        self.assertTrue(rec["tasks"]["tick"])

    def test_unseeded_declared_actions_fall_back_to_their_seed(self):
        """A connector update can add actions after the user connected: a new
        read action works immediately (its seed is allowed); a new destructive
        action stays blocked. None is always denied."""
        permissions.set_connected(self.inst, "a")
        self.assertFalse(permissions.is_allowed("fakeconn", None))
        from syrix_connectors.base import ConnectorAction, PERM_DELETE, PERM_READ

        new_read = ConnectorAction(key="added_read", label="", description="",
                                   verb=PERM_READ)
        new_del = ConnectorAction(key="added_delete", label="", description="",
                                  verb=PERM_DELETE)
        self.assertTrue(permissions.is_allowed("fakeconn", new_read))
        self.assertFalse(permissions.is_allowed("fakeconn", new_del))
        # An explicit stored block beats the seed.
        permissions.set_permission("fakeconn", new_read, False)
        self.assertFalse(permissions.is_allowed("fakeconn", new_read))

    def test_default_allowed_flag_seeds_allowed(self):
        from syrix_connectors.base import ConnectorAction, PERM_CREATE

        drafty = ConnectorAction(key="mk_draft", label="", description="",
                                 verb=PERM_CREATE, default_allowed=True)
        self.assertEqual(permissions.default_permission_state(drafty),
                         permissions.ALLOWED)

    def test_not_implemented_locked_blocked(self):
        permissions.set_connected(self.inst, "a")
        act = self.inst.actions()["fk_never"]
        self.assertFalse(permissions.set_permission("fakeconn", act, True))
        self.assertFalse(permissions.is_allowed("fakeconn", act))
        # Even a tampered state file cannot allow it (risk check wins).
        p = paths.state_path()
        with open(p, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["connectors"]["fakeconn"]["permissions"]["fk_never"] = "allowed"
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        self.assertFalse(permissions.is_allowed("fakeconn", act))

    def test_toggle_persists(self):
        permissions.set_connected(self.inst, "a")
        act = self.inst.actions()["fk_read"]
        self.assertTrue(permissions.set_permission("fakeconn", act, False))
        self.assertFalse(permissions.is_allowed("fakeconn", act))
        self.assertTrue(permissions.set_permission("fakeconn", act, True))
        self.assertTrue(permissions.is_allowed("fakeconn", act))

    def test_disconnect_keeps_permissions(self):
        permissions.set_connected(self.inst, "acct")
        permissions.set_permission("fakeconn", self.inst.actions()["fk_read"], False)
        permissions.set_disconnected("fakeconn")
        rec = permissions.connector_record("fakeconn")
        self.assertFalse(rec["connected"])
        self.assertEqual(rec["account"], "")
        self.assertEqual(rec["permissions"]["fk_read"], permissions.BLOCKED)

    def test_newer_schema_file_is_read_only(self):
        permissions.set_connected(self.inst, "a")
        p = paths.state_path()
        with open(p, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["schema_version"] = SCHEMA_VERSION + 5
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        # Mutations refuse to clobber a newer file...
        self.assertFalse(permissions.set_task("fakeconn", "tick", True))
        with open(p, "r", encoding="utf-8") as fh:
            after = json.load(fh)
        self.assertEqual(after["schema_version"], SCHEMA_VERSION + 5)

    def test_malformed_file_degrades_to_empty(self):
        with open(paths.state_path(), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(permissions.load_state()["connectors"], {})
        self.assertFalse(permissions.is_connected("fakeconn"))


if __name__ == "__main__":
    unittest.main()
