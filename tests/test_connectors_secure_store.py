"""Secure token store — encrypted-or-nothing contract, corruption handling."""
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
from syrix_connectors import paths, secure_store  # noqa: E402


class TestSecureStore(kit.TempStateMixin, unittest.TestCase):
    def test_roundtrip(self):
        doc = {"token": "tok-123", "refresh_token": "ref-456", "n": 7}
        self.assertTrue(secure_store.save_token("gmail", doc))
        self.assertTrue(secure_store.has_token("gmail"))
        self.assertEqual(secure_store.load_token("gmail"), doc)

    def test_blob_on_disk_is_not_plaintext(self):
        secure_store.save_token("gmail", {"token": "SUPER_SECRET_VALUE"})
        blob_path = os.path.join(paths.tokens_dir(), "gmail.dat")
        with open(blob_path, "rb") as fh:
            raw = fh.read()
        self.assertNotIn(b"SUPER_SECRET_VALUE", raw)

    def test_corrupted_blob_returns_none(self):
        secure_store.save_token("gmail", {"token": "x"})
        blob_path = os.path.join(paths.tokens_dir(), "gmail.dat")
        with open(blob_path, "wb") as fh:
            fh.write(b"garbage-not-dpapi")
        self.assertIsNone(secure_store.load_token("gmail"))

    def test_delete(self):
        secure_store.save_token("gmail", {"token": "x"})
        self.assertTrue(secure_store.delete_token("gmail"))
        self.assertFalse(secure_store.has_token("gmail"))
        self.assertIsNone(secure_store.load_token("gmail"))
        # Deleting again is fine (idempotent).
        self.assertTrue(secure_store.delete_token("gmail"))

    def test_unsafe_connector_ids_rejected(self):
        self.assertFalse(secure_store.save_token("../evil", {"a": 1}))
        self.assertFalse(secure_store.save_token("", {"a": 1}))
        self.assertIsNone(secure_store.load_token("..\\up"))
        self.assertFalse(secure_store.has_token("a/b"))

    def test_encrypted_or_not_at_all(self):
        """No DPAPI backend → saves fail; nothing is written in plaintext."""
        secure_store._win32crypt = lambda: None
        try:
            self.assertFalse(secure_store.available())
            self.assertFalse(secure_store.save_token("gmail", {"token": "x"}))
            self.assertFalse(secure_store.has_token("gmail"))
            self.assertIsNone(secure_store.load_token("gmail"))
        finally:
            secure_store._win32crypt = lambda: kit.FakeCrypt()

    def test_status_counts(self):
        secure_store.save_token("gmail", {"a": 1})
        secure_store.save_token("github", {"b": 2})
        st = secure_store.status()
        self.assertEqual(st["backend"], "dpapi")
        self.assertTrue(st["available"])
        self.assertEqual(st["stored_tokens"], 2)


class TestRealDpapi(kit.TempStateMixin, unittest.TestCase):
    """One roundtrip through the real Windows DPAPI when pywin32 is present."""

    def setUp(self):
        super().setUp()
        secure_store._win32crypt = self._orig_crypt_fn  # restore the real one

    def test_real_roundtrip(self):
        if secure_store._win32crypt() is None:
            self.skipTest("pywin32 not available")
        doc = {"token": "real-dpapi-roundtrip"}
        self.assertTrue(secure_store.save_token("dpapi_probe", doc))
        self.assertEqual(secure_store.load_token("dpapi_probe"), doc)
        self.assertTrue(secure_store.delete_token("dpapi_probe"))


if __name__ == "__main__":
    unittest.main()
