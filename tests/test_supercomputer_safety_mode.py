"""The safety-mode band: how much Supercomputer asks before it acts.

The property that matters is that the three modes are a BAND, not three
overlapping opinions. Every action has exactly one class, each mode names the
classes it stops for, and the sets are strictly nested — so given a mode and an
action there is one answer, and "more permissive" never means "asks about
something the stricter mode did not".

The other property, and the one worth being paranoid about: "don't ask" removes
the QUESTION, never a protection.
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

from syrix_supercomputer import (  # noqa: E402
    capabilities, gate, permissions, safety_mode, tasks,
)
from syrix_supercomputer.contract import TaskContract  # noqa: E402

_ON = {"supercomputer_enabled": True, "supercomputer_tools_enabled": True,
       "supercomputer_browser_enabled": True}


class TestTheBand(unittest.TestCase):
    def tearDown(self):
        safety_mode.reset()

    def test_the_default_is_balanced(self):
        self.assertEqual(safety_mode.mode(), safety_mode.MODE_BALANCED)

    def test_every_known_action_has_exactly_one_class(self):
        for action, klass in safety_mode._ACTION_CLASS.items():
            self.assertIn(klass, (safety_mode.CLASS_FREE, safety_mode.CLASS_BROWSE,
                                  safety_mode.CLASS_PRIVATE, safety_mode.CLASS_RISKY),
                          action)

    def test_every_gated_action_is_classified(self):
        """A verb the gate knows but the band does not would fall through to
        the unknown default. Deny-unknown makes that safe, but silence here
        means somebody forgot — so it should be deliberate."""
        for action in capabilities.enabled_actions(_ON):
            self.assertIn(action, safety_mode._ACTION_CLASS, action)

    def test_an_unknown_action_is_treated_as_risky(self):
        self.assertEqual(safety_mode.classify("rm_rf_slash"), safety_mode.CLASS_RISKY)
        self.assertTrue(safety_mode.asks_for("rm_rf_slash",
                                             current=safety_mode.MODE_BALANCED))

    def test_the_modes_are_strictly_nested(self):
        """Anything a permissive mode stops for, a stricter one also stops for.
        This is what stops the three from disagreeing in the middle."""
        auto = safety_mode._ASKS_FOR[safety_mode.MODE_AUTO]
        balanced = safety_mode._ASKS_FOR[safety_mode.MODE_BALANCED]
        strict = safety_mode._ASKS_FOR[safety_mode.MODE_ASK_ALWAYS]
        self.assertTrue(auto <= balanced <= strict)

    def test_free_actions_never_ask_in_any_mode(self):
        for m in safety_mode.MODES:
            for action in ("read_page", "web_search", "find_element", "screenshot"):
                self.assertFalse(safety_mode.asks_for(action, current=m), f"{m}/{action}")

    def test_balanced_asks_for_risky_and_private_only(self):
        m = safety_mode.MODE_BALANCED
        for action in ("send_email", "download", "purchase", "delete_online_content",
                       "publish", "upload", "post_comment"):
            self.assertTrue(safety_mode.asks_for(action, current=m), action)
        for action in ("read_email", "use_account"):
            self.assertTrue(safety_mode.asks_for(action, current=m), action)
        for action in ("open_url", "click", "scroll", "type_text"):
            self.assertFalse(safety_mode.asks_for(action, current=m), action)

    def test_ask_always_stops_before_opening_a_site(self):
        m = safety_mode.MODE_ASK_ALWAYS
        self.assertTrue(safety_mode.asks_for("open_url", current=m))
        self.assertTrue(safety_mode.asks_for("click", current=m))
        # It can still search and read — otherwise the mode is just "off".
        self.assertFalse(safety_mode.asks_for("web_search", current=m))
        self.assertFalse(safety_mode.asks_for("read_page", current=m))

    def test_auto_asks_for_nothing_except_the_irreversible(self):
        """"Don't ask" removes the question — but not for the always-ask set.

        The user's instruction on 2026-07-26 was that a job application must not be
        submitted, and a file of his must not leave the machine, without him
        seeing it first — in every mode. So `auto` waves through browsing,
        clicking, reading and computing, and still stops at the moments that
        cannot be taken back.
        """
        for action in safety_mode._ACTION_CLASS:
            expected = action in safety_mode.ALWAYS_ASK
            self.assertEqual(
                safety_mode.asks_for(action, current=safety_mode.MODE_AUTO),
                expected, action)

    def test_the_always_ask_set_is_exactly_the_irreversible_outward_ones(self):
        self.assertEqual(safety_mode.ALWAYS_ASK, frozenset({
            "send_email", "send_message", "submit_form", "upload",
            "purchase", "delete_online_content", "change_account_settings",
            "agree_to_terms", "publish",
        }))
        # Every one of them must be a real, classified action, or the pin is
        # protecting a verb nothing can emit.
        for action in safety_mode.ALWAYS_ASK:
            self.assertIn(action, safety_mode._ACTION_CLASS, action)

    def test_always_ask_can_be_switched_off_only_by_explicit_config(self):
        off = {"supercomputer_always_ask_enabled": False}
        self.assertFalse(safety_mode.always_asks("send_email", off))
        self.assertTrue(safety_mode.always_asks("send_email", {}))

    def test_an_unknown_mode_is_ignored_not_guessed(self):
        safety_mode.set_mode(safety_mode.MODE_AUTO)
        self.assertEqual(safety_mode.set_mode("yolo"), safety_mode.MODE_AUTO)


class TestModeDrivesTheGate(SupercomputerTestMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.ran = []
        tasks.create("goal", TaskContract(
            task_id="t1", goal="goal",
            allowed_domains=frozenset({"example.com", "gmail.com"}),
            allowed_actions=frozenset(capabilities.enabled_actions(_ON))))
        tasks.set_state("t1", tasks.STATE_RUNNING)

    def tearDown(self):
        safety_mode.reset()
        super().tearDown()

    def work(self):
        def _fn():
            self.ran.append(1)
            return {"ok": True, "data": {}}
        return _fn

    def run_action(self, action, scope="example.com", url="", config=None):
        return gate.execute(action, self.work(), task_id="t1", scope=scope, url=url,
                            config=dict(config or _ON), request_id="rid")

    def _cfg_all_on(self):
        cfg = dict(_ON)
        for cap in capabilities.CAPABILITIES.values():
            if cap.flag:
                cfg[cap.flag] = True
        return cfg

    def test_balanced_asks_before_sending(self):
        res = self.run_action("send_email", scope="gmail.com", config=self._cfg_all_on())
        self.assertTrue(res.needs_permission)
        self.assertEqual(self.ran, [])

    def test_auto_browses_without_asking(self):
        safety_mode.set_mode(safety_mode.MODE_AUTO)
        res = self.run_action("open_url", scope="example.com", config=self._cfg_all_on())
        self.assertTrue(res.ok)
        self.assertEqual(len(self.ran), 1)

    def test_auto_still_stops_before_sending(self):
        """The always-ask pin, through the real gate rather than the predicate."""
        safety_mode.set_mode(safety_mode.MODE_AUTO)
        res = self.run_action("send_email", scope="gmail.com", config=self._cfg_all_on())
        self.assertFalse(res.ok)
        self.assertTrue(res.needs_permission)
        self.assertEqual(self.ran, [], "nothing may run before the card is answered")

    def test_auto_still_refuses_what_settings_switched_off(self):
        """The nightmare: "don't ask" quietly granting what settings withhold.

        send_messages is off by default. It is also in ALWAYS_ASK, so this now
        raises a card rather than a flat refusal — which is the same guarantee
        by a better route: nothing runs, and the answer is one informed click
        away instead of buried in settings. What must NOT happen, and does not,
        is the action going through.
        """
        safety_mode.set_mode(safety_mode.MODE_AUTO)
        res = self.run_action("send_email", scope="gmail.com", config=dict(_ON))
        self.assertFalse(res.ok)
        self.assertTrue(res.needs_permission)
        self.assertEqual(self.ran, [])

    def test_auto_refuses_a_disabled_capability_that_never_asks(self):
        """A capability with no card behind it is still a hard refusal."""
        safety_mode.set_mode(safety_mode.MODE_AUTO)
        res = self.run_action("purchase", scope="amazon.co.uk",
                              config=dict(_ON, supercomputer_always_ask_enabled=False))
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "capability_disabled")
        self.assertEqual(self.ran, [])

    def test_auto_still_refuses_a_private_network_target(self):
        safety_mode.set_mode(safety_mode.MODE_AUTO)
        res = self.run_action("open_url", scope="192.168.1.1",
                              url="http://192.168.1.1/admin")
        self.assertFalse(res.ok)
        self.assertEqual(self.ran, [])

    def test_ask_always_stops_before_opening_a_page(self):
        safety_mode.set_mode(safety_mode.MODE_ASK_ALWAYS)
        res = self.run_action("open_url", url="https://example.com/x")
        self.assertTrue(res.needs_permission)
        self.assertEqual(self.ran, [])

    def test_ask_always_still_lets_it_search_and_read(self):
        safety_mode.set_mode(safety_mode.MODE_ASK_ALWAYS)
        self.assertTrue(self.run_action("read_page").ok)
        self.assertTrue(self.run_action("web_search", url="https://google.com/search").ok)

    def test_balanced_does_not_stop_before_opening_a_page(self):
        res = self.run_action("open_url", url="https://example.com/x")
        self.assertTrue(res.ok)

    def test_the_mode_resets_on_exit_so_it_cannot_outlive_the_session(self):
        from syrix_supercomputer import session

        session.enter({})
        safety_mode.set_mode(safety_mode.MODE_AUTO)
        session.exit_mode({})
        self.assertEqual(safety_mode.mode(), safety_mode.DEFAULT_MODE)


class TestDeclineSemantics(SupercomputerTestMixin, unittest.TestCase):
    """No means stop — and say how to do it by hand."""

    def setUp(self):
        super().setUp()
        self.ran = []
        tasks.create("goal", TaskContract(
            task_id="t1", goal="goal", allowed_domains=frozenset({"gmail.com"}),
            allowed_actions=frozenset(capabilities.enabled_actions(_ON))))
        tasks.set_state("t1", tasks.STATE_RUNNING)

    def tearDown(self):
        safety_mode.reset()
        super().tearDown()

    def _ask(self):
        cfg = dict(_ON, supercomputer_capability_send_messages=True)
        return gate.execute("send_email", lambda: {"ok": True}, task_id="t1",
                            scope="gmail.com", config=cfg, request_id="rid")

    def test_no_stops_the_task_where_it_stands(self):
        res = self._ask()
        gate.resolve_permission(res.permission_request_id, "decline")
        self.assertEqual(tasks.get("t1").state, tasks.STATE_STOPPED)

    def test_no_blocks_the_remaining_steps_rather_than_leaving_them_pending(self):
        tasks.plan_steps("t1", ["one", "two", "three"])
        tasks.set_step("t1", 0, tasks.STEP_RUNNING)
        res = self._ask()
        gate.resolve_permission(res.permission_request_id, "decline")
        statuses = [s.status for s in tasks.get("t1").steps]
        self.assertIn(tasks.STEP_BLOCKED, statuses)

    def test_chat_about_this_pauses_without_stopping(self):
        res = self._ask()
        gate.resolve_permission(res.permission_request_id, "discuss")
        self.assertEqual(tasks.get("t1").state, tasks.STATE_PAUSED_DISCUSSION)

    def test_the_decision_result_carries_what_the_reply_needs(self):
        res = self._ask()
        out = gate.resolve_permission(res.permission_request_id, "decline")
        self.assertEqual(out["task_id"], "t1")
        self.assertTrue(out["description"])


if __name__ == "__main__":
    unittest.main()
