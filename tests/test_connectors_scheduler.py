"""Background scheduler — every gate honoured, intervals respected."""
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
from syrix_connectors import permissions, scheduler  # noqa: E402


class TestSchedulerGates(kit.TempStateMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.inst = kit.register_fake()
        scheduler._last_run.clear()
        scheduler._config = {}

    def tearDown(self):
        scheduler.stop()
        scheduler._last_run.clear()
        super().tearDown()

    def _tick(self, config=None):
        scheduler._config = dict(config or {})
        scheduler._tick()

    def test_task_off_by_default(self):
        kit.mark_connected(self.inst)
        self._tick()
        self.assertEqual(self.inst.task_runs, [])

    def test_enabled_task_runs_once_per_interval(self):
        kit.mark_connected(self.inst)
        permissions.set_task("fakeconn", "tick", True)
        self._tick()
        self.assertEqual(self.inst.task_runs, ["tick"])
        self._tick()  # interval (60s min) not elapsed → no second run
        self.assertEqual(self.inst.task_runs, ["tick"])
        scheduler._last_run["fakeconn.tick"] = 0.0  # force interval elapsed
        self._tick()
        self.assertEqual(self.inst.task_runs, ["tick", "tick"])

    def test_not_connected_never_runs(self):
        permissions.set_task("fakeconn", "tick", True)
        self._tick()
        self.assertEqual(self.inst.task_runs, [])

    def test_background_kill_switch(self):
        kit.mark_connected(self.inst)
        permissions.set_task("fakeconn", "tick", True)
        self._tick({"connectors_background_enabled": False})
        self.assertEqual(self.inst.task_runs, [])
        self._tick({"connectors_enabled": False})
        self.assertEqual(self.inst.task_runs, [])

    def test_start_noop_when_disabled(self):
        scheduler.start({"connectors_background_enabled": False})
        self.assertFalse(scheduler._running)

    def test_failing_task_does_not_break_tick(self):
        kit.mark_connected(self.inst)
        permissions.set_task("fakeconn", "tick", True)
        orig = self.inst.run_task
        self.inst.run_task = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            self._tick()  # must not raise
        finally:
            self.inst.run_task = orig
        from syrix_connectors import audit

        self.assertEqual(audit.recent(1)[0]["result"], audit.RESULT_ERROR)


if __name__ == "__main__":
    unittest.main()
