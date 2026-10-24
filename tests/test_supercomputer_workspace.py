"""Workspace surfaces: derived status, results, steps, and browser chrome verbs.

The status pill and the Steps panel are the two things the user reads to know what
is happening, so the properties worth pinning are that they are *derived* and
cannot lie: status is computed from state that already exists, and a blocked
task shows its remaining steps as blocked rather than leaving them looking
pending.
"""
from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from supercomputer_testkit import SupercomputerTestMixin  # noqa: E402

from syrix_supercomputer import results, session, status, tasks  # noqa: E402
from syrix_supercomputer.browser import controller  # noqa: E402
from syrix_supercomputer.contract import TaskContract  # noqa: E402

_CFG = {"supercomputer_enabled": True, "supercomputer_browser_enabled": True}


class TestStatusDerivation(unittest.TestCase):
    """Every label in §13, and the precedence between them."""

    def test_idle_before_anything_runs(self):
        self.assertEqual(status.derive()["key"], status.IDLE)

    def test_each_task_state_maps_to_a_label(self):
        cases = {
            "planning": status.PLANNING,
            "running": status.WORKING,
            "done": status.COMPLETE,
            "stopped": status.BLOCKED,
            "failed": status.BLOCKED,
            "paused_user": status.USER_CONTROL,
            "paused_discussion": status.WAITING,
        }
        for state, expected in cases.items():
            got = status.derive(task={"task_id": "t", "state": state})["key"]
            self.assertEqual(got, expected, f"{state} should map to {expected}")

    def test_a_pending_card_outranks_everything(self):
        got = status.derive(
            task={"task_id": "t", "state": "running"},
            browser={"control": "agent"},
            pending_permission=[{"request_id": "r"}],
        )
        self.assertEqual(got["key"], status.WAITING)
        self.assertEqual(got["label"], "Waiting for permission")

    def test_user_control_outranks_a_running_task(self):
        got = status.derive(task={"task_id": "t", "state": "running"},
                            browser={"control": "user"})
        self.assertEqual(got["key"], status.USER_CONTROL)
        self.assertEqual(got["label"], "User in control")

    def test_reading_is_inferred_from_the_active_step(self):
        got = status.derive(task={
            "task_id": "t", "state": "running",
            "steps": [{"label": "Read and extract key data", "status": "running"}],
        })
        self.assertEqual(got["key"], status.READING)

    def test_verifying_is_inferred_from_the_active_step(self):
        got = status.derive(task={
            "task_id": "t", "state": "running",
            "steps": [{"label": "Verify the totals add up", "status": "running"}],
        })
        self.assertEqual(got["key"], status.VERIFYING)

    def test_every_key_has_a_label_and_a_tone(self):
        for key in status.LABELS:
            self.assertIn(key, status.TONE, f"{key} has no tone")

    def test_status_is_derived_not_settable(self):
        """Nothing may write a status directly, or the pill could drift from
        what is really happening."""
        for name in ("set", "set_status", "update"):
            self.assertFalse(hasattr(status, name))


class TestStepLifecycle(SupercomputerTestMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        tasks.create("goal", TaskContract(task_id="t1", goal="goal"))

    def test_plan_seeds_every_step_as_pending(self):
        tasks.plan_steps("t1", ["One", "Two", "Three"])
        steps = tasks.snapshot()["steps"]
        self.assertEqual([s["status"] for s in steps],
                         ["pending", "pending", "pending"])

    def test_advance_moves_running_to_done_and_starts_the_next(self):
        tasks.plan_steps("t1", ["One", "Two"])
        tasks.set_step("t1", 0, tasks.STEP_RUNNING)
        idx = tasks.advance_step("t1")
        self.assertEqual(idx, 1)
        steps = tasks.snapshot()["steps"]
        self.assertEqual(steps[0]["status"], "done")
        self.assertEqual(steps[1]["status"], "running")

    def test_blocking_marks_the_rest_blocked_not_pending(self):
        """A stopped task must show plainly what did NOT happen."""
        tasks.plan_steps("t1", ["One", "Two", "Three"])
        tasks.set_step("t1", 1, tasks.STEP_RUNNING)
        tasks.set_step("t1", 0, tasks.STEP_DONE)
        tasks.block_remaining("t1", detail="declined")
        steps = tasks.snapshot()["steps"]
        self.assertEqual([s["status"] for s in steps], ["done", "blocked", "blocked"])

    def test_out_of_range_step_is_ignored_not_fatal(self):
        tasks.plan_steps("t1", ["One"])
        tasks.set_step("t1", 99, tasks.STEP_DONE)
        self.assertEqual(tasks.snapshot()["steps"][0]["status"], "pending")

    def test_the_goal_is_locked_for_the_whole_task(self):
        """The task bar shows contract.goal, which is frozen at creation."""
        tasks.plan_steps("t1", ["One"])
        tasks.set_state("t1", tasks.STATE_RUNNING)
        self.assertEqual(tasks.snapshot()["goal"], "goal")
        tasks.add_step("t1", "something else entirely")
        self.assertEqual(tasks.snapshot()["goal"], "goal")


class TestResults(SupercomputerTestMixin, unittest.TestCase):
    def test_each_kind_is_recorded_with_something_verifiable(self):
        results.file_created("t1", r"C:\ws\index.html")
        results.summary("t1", "Findings", "Investment is up 41%.")
        results.source("t1", "Insight Report", "https://example.com/r")
        results.code_change("t1", "app.py", added=12, removed=3)
        results.test_run("t1", "pytest", passed=True, detail="3 passed")
        results.screenshot("t1", "Report page")

        got = results.for_task("t1")
        self.assertEqual(len(got), 6)
        kinds = {r["kind"] for r in got}
        self.assertEqual(kinds, {"file", "summary", "source", "code", "test", "screenshot"})

    def test_a_failing_test_is_recorded_as_failing(self):
        results.test_run("t1", "pytest", passed=False, detail="1 failed")
        self.assertIs(results.for_task("t1")[0]["passed"], False)

    def test_code_changes_carry_their_diff_size(self):
        results.code_change("t1", "app.py", added=12, removed=3)
        r = results.for_task("t1")[0]
        self.assertEqual((r["added"], r["removed"]), (12, 3))

    def test_clearing_drops_references_only(self):
        results.file_created("t1", r"C:\ws\index.html")
        self.assertEqual(results.clear("t1"), 1)
        self.assertEqual(results.for_task("t1"), [])

    def test_results_do_not_leak_between_tasks(self):
        results.summary("t1", "A", "a")
        self.assertEqual(results.for_task("t2"), [])


class TestSnapshotShape(SupercomputerTestMixin, unittest.TestCase):
    """The workspace reads exactly these keys; a rename would blank a panel."""

    def test_snapshot_carries_every_panel_the_ui_reads(self):
        session.enter(_CFG)
        snap = session.snapshot(_CFG)
        for key in ("active", "browser", "task", "pending_permission",
                    "status", "results", "activity"):
            self.assertIn(key, snap, f"the workspace reads snap.{key}")

    def test_status_in_the_snapshot_is_derived_from_the_rest(self):
        session.enter(_CFG)
        controller.take_control()
        snap = session.snapshot(_CFG)
        self.assertEqual(snap["status"]["key"], status.USER_CONTROL)


class TestBrowserChromeVerbs(SupercomputerTestMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        controller.ensure_open(config=_CFG)
        controller.set_contract(TaskContract(
            task_id="t1", goal="g", allowed_domains=frozenset({"example.com"})))

    def test_tab_management_is_available_to_the_user(self):
        """new_tab / focus / close are the user's own chrome, so they keep
        working even when the agent is paused or the user has taken over."""
        controller.take_control()
        for call in (lambda: controller.new_tab(config=_CFG),
                     lambda: controller.focus("sc1", config=_CFG),
                     lambda: controller.close("sc1", config=_CFG)):
            self.assertTrue(call()["ok"])

    def test_history_navigation_does_not_need_a_scope_check(self):
        """Back can only reach pages this task already visited."""
        res = controller.navigate("back", config=_CFG)
        self.assertTrue(res["ok"])

    def test_goto_is_scope_checked_like_any_navigation(self):
        """A sensitive destination stops for a card; an ordinary public one is
        browsing and goes through."""
        blocked = controller.navigate("goto", url="https://mail.google.com/", config=_CFG)
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["reason"], "needs_scope_permission")

        allowed = controller.navigate("goto", url="https://example.com/x", config=_CFG)
        self.assertTrue(allowed["ok"])

    def test_goto_honours_the_open_navigation_kill_switch(self):
        cfg = dict(_CFG, supercomputer_open_navigation=False)
        res = controller.navigate("goto", url="https://elsewhere.example/", config=cfg)
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "domain_not_in_task_scope")

    def test_goto_still_refuses_the_local_network(self):
        controller.set_contract(TaskContract(
            task_id="t1", goal="g", allowed_domains=frozenset({"192.168.1.1"})))
        res = controller.navigate("goto", url="http://192.168.1.1/", config=_CFG)
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "private_network")

    def test_annotate_works_while_the_user_is_in_control(self):
        """Annotating is how the user hands SYRIX a target without giving up
        the wheel, so it must not be gated on agent control."""
        controller.take_control()
        self.assertTrue(controller.annotate(config=_CFG)["ok"])

    def test_annotate_respects_the_capability_switch(self):
        res = controller.annotate(config={"supercomputer_enabled": True,
                                          "supercomputer_browser_enabled": False})
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "browser_capability_disabled")


if __name__ == "__main__":
    unittest.main()
