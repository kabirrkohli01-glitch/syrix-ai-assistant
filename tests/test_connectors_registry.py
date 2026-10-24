"""Connector registry — catalogue integrity, lazy load, version quarantine."""
from __future__ import annotations

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
from syrix_connectors import registry  # noqa: E402
from syrix_connectors.base import SCHEMA_VERSION, ConnectorBase  # noqa: E402


class TestCatalogue(kit.TempStateMixin, unittest.TestCase):
    def test_ids_unique_and_lowercase(self):
        ids = [e.id for e in registry.CATALOGUE]
        self.assertEqual(len(ids), len(set(ids)))
        for cid in ids:
            self.assertEqual(cid, cid.lower())

    def test_implemented_entries_have_import_path(self):
        for e in registry.CATALOGUE:
            if e.implemented:
                self.assertIn(":", e.import_path, f"{e.id} missing import_path")
            else:
                self.assertEqual(e.import_path, "")

    def test_shipped_catalogue_is_implemented(self):
        self.assertTrue(registry.is_implemented("gmail"))
        self.assertTrue(registry.is_implemented("notion"))
        self.assertTrue(registry.is_implemented("ibkr"))
        self.assertTrue(registry.is_implemented("hostinger_mail"))
        self.assertFalse(registry.is_implemented("nonexistent"))

    def test_entry_lookup_normalises(self):
        self.assertIsNotNone(registry.entry("  GMAIL "))
        self.assertIsNone(registry.entry("nope"))

    def test_every_external_non_oauth_connector_has_hosted_setup_page(self):
        exempt_auth = {"oauth_google", "local"}
        for entry in registry.CATALOGUE:
            if not entry.implemented:
                continue
            inst = registry.get_connector(entry.id)
            self.assertIsNotNone(inst, entry.id)
            if str(getattr(inst, "auth_kind", "")) in exempt_auth:
                continue
            url = str(getattr(inst, "setup_url", "") or "")
            self.assertTrue(url.startswith("https://"), f"{entry.id} missing hosted setup URL")
            self.assertNotIn("localhost", url.lower(), entry.id)
            self.assertNotIn("127.0.0.1", url, entry.id)


class TestInstanceLoad(kit.TempStateMixin, unittest.TestCase):
    def test_gmail_loads_and_matches_schema(self):
        inst = registry.get_connector("gmail")
        self.assertIsInstance(inst, ConnectorBase)
        self.assertEqual(inst.schema_version, SCHEMA_VERSION)
        self.assertEqual(registry.quarantine_reason("gmail"), "")
        # Cached — same instance on the second call.
        self.assertIs(registry.get_connector("gmail"), inst)

    def test_expanded_connector_loads(self):
        self.assertIsInstance(registry.get_connector("notion"), ConnectorBase)

    def test_version_mismatch_quarantines(self):
        mod = types.ModuleType("fake_bad_conn_mod")

        class BadConn(ConnectorBase):
            id = "badconn"
            name = "Bad"
            schema_version = SCHEMA_VERSION + 99

            def actions(self):
                return {}

            def execute(self, action_key, args, *, config, request_id=""):
                raise AssertionError("must never run")

            def connect(self, *, config, progress=None):
                return False, "no"

            def disconnect(self, *, config):
                pass

            def health(self, *, config):
                return "available"

        mod.BadConn = BadConn
        sys.modules["fake_bad_conn_mod"] = mod
        entry = registry.CatalogueEntry(
            id="badconn", name="Bad", description="", icon="", category="test",
            implemented=True, import_path="fake_bad_conn_mod:BadConn",
        )
        registry.CATALOGUE.append(entry)
        registry._BY_ID[entry.id] = entry
        try:
            self.assertIsNone(registry.get_connector("badconn"))
            self.assertIn("schema_version", registry.quarantine_reason("badconn"))
        finally:
            registry._BY_ID.pop("badconn", None)
            registry.CATALOGUE[:] = [e for e in registry.CATALOGUE if e.id != "badconn"]
            sys.modules.pop("fake_bad_conn_mod", None)

    def test_import_failure_quarantines(self):
        entry = registry.CatalogueEntry(
            id="ghostconn", name="Ghost", description="", icon="", category="test",
            implemented=True, import_path="module_that_does_not_exist:Nope",
        )
        registry.CATALOGUE.append(entry)
        registry._BY_ID[entry.id] = entry
        try:
            self.assertIsNone(registry.get_connector("ghostconn"))
            self.assertTrue(registry.quarantine_reason("ghostconn").startswith("load_failed"))
        finally:
            registry._BY_ID.pop("ghostconn", None)
            registry.CATALOGUE[:] = [e for e in registry.CATALOGUE if e.id != "ghostconn"]


if __name__ == "__main__":
    unittest.main()
