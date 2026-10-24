"""Multi-brain routing and the second-opinion critic.

The user's constraint was "the best there can be, and free". So there is no paid
lane: the gain comes from using the free pool properly. Two things do that.

**Grading the step.** Deciding which of forty search results to open is real
reasoning; re-reading a page after a scroll is not. Sending both through one
tier wastes the strong lane on nothing and is slower overall.

**A second reader.** `_verify` catches mechanical dishonesty — no answer, a
claimed file that does not exist, figures with no page behind them. It cannot
catch an answer that is fluent, grounded and about the wrong question. A fresh
model with the goal in hand can.
"""
from __future__ import annotations

import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from supercomputer_testkit import SupercomputerTestMixin  # noqa: E402

from syrix_supercomputer import critic, loop, models  # noqa: E402


class TestStepGrading(SupercomputerTestMixin, unittest.TestCase):
    def test_the_opening_move_always_gets_the_strong_lane(self):
        """It sets the direction of the whole task."""
        self.assertEqual(loop._grade_for(0, "", True, 0), models.GRADE_HARD)

    def test_a_scroll_or_re_read_drops_to_the_fast_lane(self):
        for action in ("scroll", "read_page", "list_tabs", "switch_tab", "note"):
            self.assertEqual(loop._grade_for(5, action, True, 0),
                             models.GRADE_ROUTINE, action)

    def test_anything_that_failed_gets_the_strong_lane(self):
        """Working out an alternative route is the hardest thing it does."""
        self.assertEqual(loop._grade_for(5, "scroll", False, 0), models.GRADE_HARD)
        self.assertEqual(loop._grade_for(5, "open_url", False, 0), models.GRADE_HARD)

    def test_losing_the_protocol_gets_the_strong_lane(self):
        self.assertEqual(loop._grade_for(5, "read_page", True, 2), models.GRADE_HARD)

    def test_ordinary_progress_sits_in_the_middle(self):
        self.assertEqual(loop._grade_for(5, "open_url", True, 0), models.GRADE_NORMAL)


class TestTierSelection(SupercomputerTestMixin, unittest.TestCase):
    def test_the_grade_lowers_the_tier_and_never_raises_it(self):
        models.set_depth("spark")
        for grade in models.GRADES:
            self.assertEqual(models.tier_for(grade, {}), "T1_FAST",
                             "The user picking the fast depth is a ceiling, not a floor")

    def test_the_call_carries_the_grade_through_to_the_pool(self):
        from syrix_supercomputer import models as eb

        seen = []
        real = eb.pool_transport
        eb.pool_transport = lambda tier, msgs, **kw: (seen.append(tier) or ("ok", "fake"))
        try:
            models.set_depth("zenith")
            models.chat(models.ROLE_PLAN, [{"role": "user", "content": "x"}],
                        config={}, grade=models.GRADE_ROUTINE)
            models.chat(models.ROLE_PLAN, [{"role": "user", "content": "x"}],
                        config={}, grade=models.GRADE_HARD)
        finally:
            eb.pool_transport = real
        self.assertEqual(seen, ["T1_FAST", "T3_STRONG"])


class TestTheCritic(SupercomputerTestMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        from syrix_supercomputer import models as eb

        self._real = eb.pool_transport
        self._eb = eb
        self.calls = []

    def tearDown(self):
        self._eb.pool_transport = self._real
        super().tearDown()

    def reply(self, payload):
        def _fake(tier, messages, **kw):
            self.calls.append(messages[-1]["content"])
            return (payload, "fake")
        self._eb.pool_transport = _fake

    def test_an_accepted_answer_passes_through(self):
        self.reply(json.dumps({"verdict": "accept"}))
        out = critic.review("what is the price", "It is £1,299 per the vendor page.",
                            config={})
        self.assertTrue(out.accept)
        self.assertEqual(out.pushback, "")

    def test_a_revision_comes_back_as_one_concrete_pushback(self):
        self.reply(json.dumps({"verdict": "revise",
                               "problem": "you gave prices but not the setup that was asked for"}))
        out = critic.review("prices AND a trading setup", "Here are the prices…" * 8,
                            config={})
        self.assertFalse(out.accept)
        self.assertIn("not the setup", out.pushback)
        self.assertIn("call finish again", out.pushback)

    def test_the_critic_sees_the_goal_and_the_sources(self):
        self.reply(json.dumps({"verdict": "accept"}))
        critic.review("find the RTX 5090 price", "It costs £1,899." * 6, config={})
        prompt = self.calls[-1]
        self.assertIn("find the RTX 5090 price", prompt)
        self.assertIn("PAGES THE ASSISTANT READ", prompt)

    def test_every_failure_path_accepts_rather_than_blocking(self):
        """An answer already good enough to reach here must not be lost to a
        critic that could not answer."""
        for payload in ("", "not json at all", "{}", json.dumps({"verdict": "revise"})):
            self.reply(payload)
            self.assertTrue(critic.review("goal", "a real answer " * 10, config={}).accept,
                            repr(payload))

    def test_a_dead_model_accepts(self):
        def _boom(*a, **kw):
            raise RuntimeError("provider down")
        self._eb.pool_transport = _boom
        self.assertTrue(critic.review("goal", "a real answer " * 10, config={}).accept)

    def test_a_very_short_answer_is_not_worth_a_model_call(self):
        self.reply(json.dumps({"verdict": "revise", "problem": "x"}))
        self.assertTrue(critic.review("goal", "yes", config={}).accept)
        self.assertEqual(self.calls, [])

    def test_it_can_be_switched_off(self):
        self.reply(json.dumps({"verdict": "revise", "problem": "x"}))
        out = critic.review("goal", "a real answer " * 10,
                            config={"supercomputer_critic_enabled": False})
        self.assertTrue(out.accept)
        self.assertEqual(self.calls, [])

    def test_it_can_only_push_back_once_per_task(self):
        """A critic that can block twice is a critic that can loop."""
        self.reply(json.dumps({"verdict": "revise", "problem": "missing the setup"}))
        seen = set()
        ok, _ = loop._critique("goal", "answer " * 20, ctx=_Ctx(), pushed_back=seen, cfg={})
        self.assertFalse(ok)
        ok2, _ = loop._critique("goal", "answer " * 20, ctx=_Ctx(), pushed_back=seen, cfg={})
        self.assertTrue(ok2, "the second pushback must be waived")


class _Ctx:
    task_id = "t1"


if __name__ == "__main__":
    unittest.main()
