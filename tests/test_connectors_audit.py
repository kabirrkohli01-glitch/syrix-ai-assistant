"""Audit log — append/read/filter, rotation, no sensitive payloads."""
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
from syrix_connectors import audit, paths  # noqa: E402


class TestAudit(kit.TempStateMixin, unittest.TestCase):
    def test_record_and_recent_newest_first(self):
        for i in range(5):
            audit.record("gmail", f"act_{i}", actor="brain", result="ok")
        tail = audit.recent(3)
        self.assertEqual([e["action"] for e in tail], ["act_4", "act_3", "act_2"])

    def test_filter_by_connector(self):
        audit.record("gmail", "a", actor="hud", result="ok")
        audit.record("github", "b", actor="hud", result="ok")
        tail = audit.recent(10, connector="gmail")
        self.assertTrue(all(e["connector"] == "gmail" for e in tail))
        self.assertEqual(len(tail), 1)

    def test_reason_truncated_and_fields_bounded(self):
        audit.record("gmail", "x" * 500, actor="b" * 100, result="ok",
                     reason="r" * 500, detail="d" * 500, request_id="q" * 100)
        e = audit.recent(1)[0]
        self.assertLessEqual(len(e["action"]), 60)
        self.assertLessEqual(len(e["actor"]), 16)
        self.assertLessEqual(len(e["reason"]), 160)
        self.assertLessEqual(len(e["detail"]), 200)
        self.assertLessEqual(len(e["request_id"]), 40)

    def test_count_and_latency_recorded(self):
        audit.record("gmail", "list", actor="brain", result="ok",
                     latency_ms=412.7, count=3)
        e = audit.recent(1)[0]
        self.assertEqual(e["count"], 3)
        self.assertEqual(e["latency_ms"], 412)

    def test_rotation(self):
        orig = audit._MAX_BYTES
        audit._MAX_BYTES = 512
        try:
            for i in range(60):
                audit.record("gmail", f"act_{i}", actor="brain", result="ok")
            self.assertTrue(os.path.isfile(paths.audit_path() + ".1"))
            # recent() still reads the live file without raising.
            self.assertTrue(audit.recent(5))
        finally:
            audit._MAX_BYTES = orig

    def test_garbage_lines_skipped(self):
        audit.record("gmail", "good", actor="brain", result="ok")
        with open(paths.audit_path(), "a", encoding="utf-8") as fh:
            fh.write("NOT JSON AT ALL\n")
        tail = audit.recent(5)
        self.assertEqual(tail[0]["action"], "good")


if __name__ == "__main__":
    unittest.main()
