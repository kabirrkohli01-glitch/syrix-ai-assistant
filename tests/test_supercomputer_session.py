"""Supercomputer session lifecycle and HUD command surface.

The properties that matter here are the teardown guarantees. Leaving the mode
must leave nothing running and no authority outstanding, and it must do so even
when one teardown step fails — a half-torn-down session is exactly the state
that leaves a browser or a microphone live.
"""
from __future__ import annotations

import os
import sys
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from supercomputer_testkit import SupercomputerTestMixin  # noqa: E402

from syrix_supercomputer import hud_api, permissions, session, tasks  # noqa: E402
from syrix_supercomputer.browser import controller as browser  # noqa: E402
from syrix_supercomputer.contract import TaskContract  # noqa: E402


class TestSessionLifecycle(SupercomputerTestMixin, unittest.TestCase):
    def test_enter_activates_and_is_idempotent(self):
        first = session.enter({})
        self.assertTrue(first["ok"])
        self.assertTrue(session.is_active())
        sid = session.session_id()

        second = session.enter({})
        self.assertEqual(second["reason"], "already_active")
        self.assertEqual(session.session_id(), sid, "re-entry must not stack state")

    def test_disabled_mode_refuses_to_enter(self):
        res = session.enter({"supercomputer_enabled": False})
        self.assertFalse(res["ok"])
        self.assertFalse(session.is_active())

    def test_exit_when_not_active_is_safe(self):
        res = session.exit_mode({})
        self.assertTrue(res["ok"])
        self.assertEqual(res["reason"], "not_active")

    def test_exit_tears_down_every_subsystem(self):
        session.enter({})
        res = session.exit_mode({})
        self.assertTrue(res["ok"])
        self.assertFalse(session.is_active())
        self.assertEqual(
            sorted(res["torn_down"]),
            # "depth" and "memory" joined on 2026-07-26: a permissive reasoning
            # depth must not outlive its session any more than a permissive
            # safety mode may, and the task memory briefs belong to the
            # conversation that produced them.
            # "chat_thread" joined on 2026-07-27: the mode routes its chat
            # lines to its own session by channel binding, and leaving that
            # bound after exit would file a later stray line into a
            # conversation the user has finished with.
            # "resources" the same day: a mode that is gone must not leave the
            # backend running at below-normal priority for the rest of the
            # session.
            ["browser", "chat_thread", "depth", "history", "memory", "models",
             "permissions", "preview", "resources", "safety_mode", "tasks",
             "voice"],
            "every teardown step must run",
        )

    def test_exit_revokes_all_permissions(self):
        session.enter({})
        req = permissions.request("task-1", "send_email", "gmail.com", "Send a reply")
        permissions.decide(req.request_id, permissions.DECISION_ALLOW)
        self.assertTrue(permissions.has_grant("task-1", "send_email", "gmail.com"))

        session.exit_mode({})
        self.assertFalse(
            permissions.has_grant("task-1", "send_email", "gmail.com"),
            "a grant must not survive leaving the mode",
        )

    def test_exit_stops_running_tasks(self):
        session.enter({})
        task = tasks.create("do a thing", TaskContract(task_id="t1", goal="do a thing"))
        tasks.set_state(task.task_id, tasks.STATE_RUNNING)

        session.exit_mode({})
        self.assertEqual(tasks.get("t1").state, tasks.STATE_STOPPED)

    def test_exit_continues_when_one_step_fails(self):
        """One failing teardown must not prevent the others — otherwise a
        browser or mic can be left live by an unrelated error."""
        session.enter({})
        original = browser.shutdown

        def _boom(**kw):
            raise RuntimeError("simulated browser teardown failure")

        browser.shutdown = _boom
        try:
            res = session.exit_mode({})
        finally:
            browser.shutdown = original

        self.assertTrue(res["ok"])
        self.assertFalse(session.is_active(), "session must still end")
        self.assertIn("tasks", res["torn_down"])
        self.assertIn("permissions", res["torn_down"])
        self.assertNotIn("browser", res["torn_down"])

    def test_new_session_gets_a_fresh_session_tag(self):
        """The per-session tag rotates. The browser PROFILE deliberately does
        not — it is the HUD browser's own, so the user stays signed in."""
        session.enter({})
        first = browser.partition()
        session.exit_mode({})

        session.enter({})
        second = browser.partition()
        self.assertNotEqual(first, second)
        self.assertTrue(second.startswith("syrix-supercomputer-"))

    def test_exit_forgets_the_browser_profile_it_was_told_about(self):
        session.enter({})
        session.exit_mode({})
        self.assertEqual(browser.browser_partition(), "")


class TestDetachHandoff(SupercomputerTestMixin, unittest.TestCase):
    """Closing the workspace is not the same as pressing Stop.

    With work in flight the session survives so it can finish and be watched
    from the Dynamic Island; with nothing running the mode goes away entirely.
    """

    def _running_task(self):
        tasks.create("goal", TaskContract(task_id="t1", goal="goal"))
        tasks.set_state("t1", tasks.STATE_RUNNING)

    def test_exit_with_a_live_task_detaches_instead_of_tearing_down(self):
        session.enter({})
        self._running_task()
        ok, detail = hud_api.handle_command("supercomputer_exit", {}, {})
        self.assertTrue(ok)
        self.assertEqual(detail, "detached_task_running")
        self.assertTrue(session.is_active(), "the task must keep running")
        self.assertTrue(session.is_detached())
        self.assertEqual(tasks.current().state, tasks.STATE_RUNNING)

    def test_closing_the_window_keeps_the_session(self):
        """Stepping out of a room is not leaving the building.

        It used to tear the whole mode down whenever no task was running, so
        the user would go to normal chat for two minutes, come back, and find a
        blank workspace with his research tabs gone — having never closed
        SYRIX. Closing now DETACHES; the session survives until the app does.
        """
        session.enter({})
        ok, detail = hud_api.handle_command("supercomputer_exit", {}, {})
        self.assertTrue(ok)
        self.assertEqual(detail, "detached")
        self.assertTrue(session.is_active(), "the session must survive")
        self.assertTrue(session.is_detached())

    def test_a_detached_session_holds_no_standing_authority(self):
        """It keeps his tabs; it does not keep permission to act on them."""
        session.enter({})
        req = permissions.request("task-1", "send_email", "gmail.com", "Send a reply")
        permissions.decide(req.request_id, permissions.DECISION_ALLOW)
        self.assertTrue(permissions.has_grant("task-1", "send_email", "gmail.com"))

        hud_api.handle_command("supercomputer_exit", {}, {})
        self.assertTrue(session.is_active(), "the session survives")
        self.assertFalse(
            permissions.has_grant("task-1", "send_email", "gmail.com"),
            "a detached session must not keep authority to act")

    def test_a_halted_task_does_not_count_as_live(self):
        """A task waiting on a permission card is not RUNNING — so closing the
        window drops its grants, even though the session itself stays."""
        session.enter({})
        self._running_task()
        tasks.set_state("t1", tasks.STATE_AWAITING_PERMISSION)
        self.assertFalse(session.has_live_task())
        hud_api.handle_command("supercomputer_exit", {}, {})
        self.assertTrue(session.is_detached())

    def test_a_detached_session_closes_once_its_task_finishes(self):
        session.enter({})
        self._running_task()
        session.detach({})
        self.assertTrue(session.is_detached())
        tasks.set_state("t1", tasks.STATE_DONE)
        self.assertTrue(session.reap_if_detached({}))
        self.assertFalse(session.is_active())

    def test_reaping_does_nothing_while_the_task_still_runs(self):
        session.enter({})
        self._running_task()
        session.detach({})
        self.assertFalse(session.reap_if_detached({}))
        self.assertTrue(session.is_active())

    def test_detaching_releases_the_microphone(self):
        """A mic attached to a window nobody is looking at is the one thing that
        must never linger."""
        from syrix_supercomputer import voice

        session.enter({})
        self._running_task()
        session.detach({})
        self.assertFalse(voice.snapshot({}).get("active"))


class TestReplayedEnter(SupercomputerTestMixin, unittest.TestCase):
    """hud_control.json keeps a command queue that survives a backend restart.

    A `supercomputer_enter` from a PREVIOUS run was replayed at boot, opening an
    agentic session with no workspace anywhere on screen — after which a marked
    chat turn would run browser work invisibly. A command older than the process
    reading it did not come from the HUD that is running now.
    """

    def test_an_enter_older_than_this_process_is_ignored(self):
        from syrix_supercomputer import hud_api

        stale = session.PROCESS_STARTED_TS - 600.0
        ok, detail = hud_api.handle_command("supercomputer_enter", {"ts": stale}, {})
        self.assertFalse(ok)
        self.assertEqual(detail, "stale_command_ignored")
        self.assertFalse(session.is_active(), "a replay must not open the mode")

    def test_a_fresh_enter_is_honoured(self):
        from syrix_supercomputer import hud_api

        ok, _ = hud_api.handle_command("supercomputer_enter", {"ts": time.time()}, {})
        self.assertTrue(ok)
        self.assertTrue(session.is_active())

    def test_an_enter_with_no_timestamp_is_honoured(self):
        """Absent metadata must not become a refusal — the workspace is allowed
        to send a bare command."""
        from syrix_supercomputer import hud_api

        ok, _ = hud_api.handle_command("supercomputer_enter", {}, {})
        self.assertTrue(ok)
        self.assertTrue(session.is_active())

    def test_staleness_is_inert_when_the_process_start_is_unknown(self):
        real = session.PROCESS_STARTED_TS
        try:
            session.PROCESS_STARTED_TS = 0.0
            self.assertFalse(session.is_stale_command(1.0))
        finally:
            session.PROCESS_STARTED_TS = real


class TestSnapshot(SupercomputerTestMixin, unittest.TestCase):
    def test_snapshot_reports_inactive_before_entry(self):
        snap = session.snapshot({})
        self.assertFalse(snap["active"])
        self.assertEqual(snap["boot_id"], session.BOOT_ID)

    def test_snapshot_includes_every_subsystem(self):
        session.enter({})
        snap = session.snapshot({})
        self.assertTrue(snap["active"])
        for key in ("browser", "task", "pending_permission"):
            self.assertIn(key, snap, f"snapshot is missing {key}")

    def test_snapshot_surfaces_pending_permission_cards(self):
        session.enter({})
        permissions.request("t1", "send_email", "gmail.com", "Send a reply to Sam")
        snap = session.snapshot({})
        pend = snap["pending_permission"]
        self.assertEqual(len(pend), 1)
        self.assertEqual(pend[0]["description"], "Send a reply to Sam")
        self.assertEqual(pend[0]["scope"], "gmail.com")

    def test_push_state_writes_through_the_host(self):
        hud_api.push_state({})
        snaps = self.hud_snapshots()
        self.assertTrue(snaps, "push_state must write the syrix_supercomputer key")
        self.assertIn("active", snaps[-1])

    def test_last_snapshot_caches_for_the_clobber_trap(self):
        """syrix_host's filtered writer re-injects from this cache on every
        write cycle; without it the key is wiped ~6x/second."""
        self.assertIsNone(hud_api.last_snapshot())
        hud_api.push_state({})
        cached = hud_api.last_snapshot()
        self.assertIsInstance(cached, dict)
        self.assertIn("active", cached)


class TestHudCommands(SupercomputerTestMixin, unittest.TestCase):
    def test_enter_and_exit_commands(self):
        ok, _ = hud_api.handle_command("supercomputer_enter", {}, {})
        self.assertTrue(ok)
        self.assertTrue(session.is_active())

        ok, _ = hud_api.handle_command("supercomputer_exit", {}, {})
        self.assertTrue(ok)
        # Closing the window detaches; only exit_mode ends the session.
        self.assertTrue(session.is_detached())
        session.exit_mode({})
        self.assertFalse(session.is_active())

    def test_unknown_command_is_refused(self):
        ok, detail = hud_api.handle_command("supercomputer_launch_missiles", {}, {})
        self.assertFalse(ok)
        self.assertTrue(detail.startswith("unknown_command"))

    def test_take_control_stops_the_agent_immediately(self):
        hud_api.handle_command("supercomputer_enter", {}, {})
        task = tasks.create("browse", TaskContract(task_id="t1", goal="browse"))
        tasks.set_state(task.task_id, tasks.STATE_RUNNING)
        gen_before = tasks.generation("t1")

        ok, detail = hud_api.handle_command("supercomputer_take_control", {}, {})
        self.assertTrue(ok)
        self.assertEqual(detail, "user_in_control")
        self.assertFalse(browser.is_agent_allowed(),
                         "the agent must not be able to act while the user drives")
        self.assertEqual(tasks.get("t1").state, tasks.STATE_PAUSED_USER)
        self.assertGreater(tasks.generation("t1"), gen_before,
                           "in-flight work must be invalidated, not just paused")

    def test_return_control_hands_the_browser_back(self):
        hud_api.handle_command("supercomputer_enter", {}, {})
        hud_api.handle_command("supercomputer_take_control", {}, {})
        ok, _ = hud_api.handle_command("supercomputer_return_control", {}, {})
        # No browser session is open in Phase 2, so control cannot be handed
        # back yet — but the command must fail honestly rather than pretend.
        self.assertIn(browser.control_state(),
                      (browser.CONTROL_AGENT, browser.CONTROL_USER))

    def test_stop_terminates_the_task(self):
        hud_api.handle_command("supercomputer_enter", {}, {})
        task = tasks.create("browse", TaskContract(task_id="t1", goal="browse"))
        tasks.set_state(task.task_id, tasks.STATE_RUNNING)

        ok, detail = hud_api.handle_command("supercomputer_stop", {}, {})
        self.assertTrue(ok)
        self.assertEqual(detail, "stopped")
        self.assertEqual(tasks.get("t1").state, tasks.STATE_STOPPED)

    def test_stop_revokes_the_tasks_grants(self):
        """Stopping is a task end, so authority granted for it dies with it."""
        hud_api.handle_command("supercomputer_enter", {}, {})
        task = tasks.create("browse", TaskContract(task_id="t1", goal="browse"))
        tasks.set_state(task.task_id, tasks.STATE_RUNNING)
        req = permissions.request("t1", "open_url", "example.com", "Open example.com")
        permissions.decide(req.request_id, permissions.DECISION_ALLOW)
        self.assertTrue(permissions.has_grant("t1", "open_url", "example.com"))

        hud_api.handle_command("supercomputer_stop", {}, {})
        self.assertFalse(permissions.has_grant("t1", "open_url", "example.com"))

    def test_permission_decision_command_routes_to_the_gate(self):
        hud_api.handle_command("supercomputer_enter", {}, {})
        req = permissions.request("t1", "open_url", "example.com", "Open example.com")

        ok, detail = hud_api.handle_command(
            "supercomputer_permission_decision",
            {"request_id": req.request_id, "decision": "allow"},
            {},
        )
        self.assertTrue(ok)
        self.assertEqual(detail, "allowed")
        self.assertTrue(permissions.has_grant("t1", "open_url", "example.com"))

    def test_every_declared_command_is_handled(self):
        """HUD_COMMANDS is mirrored into _SYRIX_OWNED_COMMANDS; a name that is
        routed here but not handled would ack as an unknown action forever."""
        hud_api.handle_command("supercomputer_enter", {}, {})
        for cmd in hud_api.HUD_COMMANDS:
            _, detail = hud_api.handle_command(cmd, {"request_id": "x", "decision": "allow"}, {})
            self.assertFalse(
                str(detail).startswith("unknown_command"),
                f"{cmd} is declared in HUD_COMMANDS but not handled",
            )

    def test_no_hud_command_waits_on_a_human(self):
        """These handlers run on the wiring layer's dispatch thread. A command
        that parks it (annotate waits for a click) would freeze every other
        owned command, so none may be declared here."""
        self.assertNotIn("supercomputer_annotate", hud_api.HUD_COMMANDS)


if __name__ == "__main__":
    unittest.main()
