"""Supercomputer must not spend the day's free tokens and leave normal chat poor.

The pool is shared. One long agentic task is twenty to sixty calls of several
thousand tokens, so without a reserve an afternoon in Supercomputer exhausts
every free lane's daily budget and the user's ordinary chat drops to a local 7B —
the mode making the rest of SYRIX worse, which was never the trade.

So Supercomputer passes a reserve and stops with a quarter of each daily budget
still there. Normal chat passes none and can use every last token.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from syrix_core_v2.brain_pool.ledger import QuotaLedger  # noqa: E402
from syrix_core_v2.brain_pool.providers import Caps  # noqa: E402


class TestDailyReserve(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._dir = tempfile.TemporaryDirectory()
        self.ledger = QuotaLedger(path=os.path.join(self._dir.name, "ledger.json"))
        # 1,000,000 tokens/day, like a Cerebras model. The 10% safety margin
        # makes the true ceiling 900,000.
        self.caps = Caps(tpd=1_000_000)

    def tearDown(self):
        self._dir.cleanup()

    def _spend(self, tokens):
        self.ledger.record("cerebras:test", tokens)

    def test_normal_chat_may_use_the_whole_budget(self):
        self._spend(800_000)
        ok, why = self.ledger.headroom("cerebras:test", self.caps, 4_000)
        self.assertTrue(ok, why)

    def test_supercomputer_stops_earlier(self):
        self._spend(800_000)
        ok, why = self.ledger.headroom("cerebras:test", self.caps, 4_000, reserve=0.25)
        self.assertFalse(ok)
        self.assertEqual(why, "tpd")

    def test_the_reserved_slice_is_still_there_for_normal_chat(self):
        """The point of the reserve: after Supercomputer is cut off, ordinary
        chat still has a real model."""
        self._spend(660_000)
        sc_ok, _ = self.ledger.headroom("cerebras:test", self.caps, 4_000, reserve=0.25)
        normal_ok, _ = self.ledger.headroom("cerebras:test", self.caps, 4_000)
        self.assertFalse(sc_ok)
        self.assertTrue(normal_ok)

    def test_both_share_the_early_part_of_the_day(self):
        self._spend(100_000)
        for reserve in (0.0, 0.25):
            ok, why = self.ledger.headroom("cerebras:test", self.caps, 4_000,
                                           reserve=reserve)
            self.assertTrue(ok, f"reserve={reserve} {why}")

    def test_per_minute_limits_are_never_reserved(self):
        """A minute refills by itself; reserving it would throttle for nothing."""
        caps = Caps(tpm=60_000)
        self._spend(50_000)
        ok_plain, _ = self.ledger.headroom("cerebras:test", caps, 4_000)
        ok_reserved, _ = self.ledger.headroom("cerebras:test", caps, 4_000, reserve=0.25)
        self.assertEqual(ok_plain, ok_reserved)

    def test_request_per_day_budgets_are_reserved_too(self):
        caps = Caps(rpd=2000, scope="provider")
        for _ in range(1400):
            self.ledger.record("nim", 10)
        self.assertTrue(self.ledger.headroom("nim", caps, 10)[0])
        self.assertFalse(self.ledger.headroom("nim", caps, 10, reserve=0.25)[0])

    def test_a_silly_reserve_cannot_lock_the_pool_out(self):
        for reserve in (-1.0, 5.0, float("inf")):
            ok, _ = self.ledger.headroom("cerebras:test", self.caps, 4_000,
                                         reserve=reserve)
            self.assertIsInstance(ok, bool)
        # Clamped at 0.8, so with the 10% safety margin a tenth of the day is
        # still usable — a courtesy limit must never lock the caller out.
        self.assertTrue(
            self.ledger.headroom("cerebras:test", self.caps, 4_000, reserve=5.0)[0])


class TestModeDefault(unittest.TestCase):
    def test_the_mode_reserves_a_quarter_by_default(self):
        from syrix_supercomputer import models

        self.assertAlmostEqual(models.budget_reserve({}), 0.25)
        self.assertAlmostEqual(models.budget_reserve(None), 0.25)

    def test_it_is_configurable_and_clamped(self):
        from syrix_supercomputer import models

        self.assertAlmostEqual(
            models.budget_reserve({"supercomputer_budget_reserve": 0.5}), 0.5)
        self.assertAlmostEqual(
            models.budget_reserve({"supercomputer_budget_reserve": 99}), 0.9)
        # The mode clamps at 0.9; the ledger clamps again at 0.8, so even that
        # leaves a usable slice.
        self.assertAlmostEqual(
            models.budget_reserve({"supercomputer_budget_reserve": -3}), 0.0)
        self.assertAlmostEqual(
            models.budget_reserve({"supercomputer_budget_reserve": "nonsense"}), 0.25)

    def test_it_can_be_switched_off_entirely(self):
        from syrix_supercomputer import models

        self.assertEqual(models.budget_reserve({"supercomputer_budget_reserve": 0}), 0.0)


if __name__ == "__main__":
    unittest.main()
