"""Stay out of the way: a background task must not stop the user gaming.

The trade is explicit — a task takes longer while something demanding is in
front, and the game does not stutter. On an idle machine there is no pause at
all, because an unpressured task should be fast.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from syrix_supercomputer import resources  # noqa: E402


class _Case(unittest.TestCase):
    def setUp(self):
        resources._reset_for_tests()

    def tearDown(self):
        resources._reset_for_tests()

    def _pressure(self, value):
        return mock.patch.object(resources, "_foreground_is_fullscreen",
                                 lambda: value)


class TestPacing(_Case):
    def test_an_idle_machine_is_never_slowed(self):
        with self._pressure(False):
            self.assertEqual(resources.step_pause({}), resources.PACE_FREE)

    def test_a_full_screen_app_in_front_makes_the_loop_yield(self):
        with self._pressure(True):
            self.assertGreaterEqual(resources.step_pause({}), resources.PACE_ECO)

    def test_performance_mode_never_throttles(self):
        resources.set_mode(resources.MODE_PERFORMANCE)
        with self._pressure(True):
            self.assertEqual(resources.step_pause({}), resources.PACE_FREE)
            self.assertFalse(resources.under_pressure({}))

    def test_eco_mode_is_gentle_even_on_an_idle_machine(self):
        resources.set_mode(resources.MODE_ECO)
        with self._pressure(False):
            self.assertGreater(resources.step_pause({}), 0.0)

    def test_the_pressed_pause_is_configurable(self):
        with self._pressure(True):
            self.assertEqual(
                resources.step_pause({"supercomputer_pace_pressed_s": 4.0}), 4.0)

    def test_the_kill_switch_disables_the_governor_entirely(self):
        cfg = {"supercomputer_resource_governor_enabled": False}
        with self._pressure(True):
            self.assertEqual(resources.step_pause(cfg), resources.PACE_FREE)
            self.assertFalse(resources.under_pressure(cfg))


class TestConcurrency(_Case):
    def test_workers_are_capped_harder_under_pressure(self):
        with self._pressure(False):
            free = resources.max_workers({})
        resources._reset_for_tests()
        with self._pressure(True):
            pressed = resources.max_workers({})
        self.assertGreater(free, pressed)
        self.assertGreaterEqual(pressed, 1, "never fewer than one worker")


class TestPressureDetection(_Case):
    def test_the_answer_is_cached_so_a_step_may_ask_freely(self):
        calls = []

        def probe():
            calls.append(1)
            return True

        with mock.patch.object(resources, "_foreground_is_fullscreen", probe):
            for _ in range(20):
                resources.under_pressure({})
        self.assertEqual(len(calls), 1, "the window manager is asked once, not 20 times")

    def test_detection_never_raises(self):
        with mock.patch.object(resources, "_foreground_is_fullscreen",
                               side_effect=RuntimeError("boom")):
            # under_pressure must not propagate; a governor that crashes the
            # task it is pacing is worse than no governor.
            try:
                resources.under_pressure({})
            except RuntimeError:
                self.fail("pressure detection must never raise")

    def test_it_never_polls_a_gpu_tool(self):
        """nvidia-smi was removed from SYRIX for spawning a process eight times
        a second. It must not come back through this door.

        Checks executable lines only — the module docstring says the word
        precisely so the next person knows not to reintroduce it.
        """
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(resources))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for forbidden in ("subprocess", "pynvml", "GPUtil", "nvidia_smi"):
            self.assertNotIn(forbidden, imported,
                             f"{forbidden} must not be used to detect pressure")

        # And no string literal naming a GPU tool, which would mean a shell-out
        # was assembled by hand rather than imported.
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.col_offset == 0:
                    continue  # a docstring, which names it on purpose
                self.assertNotIn("nvidia", node.value.lower())


class TestPriority(_Case):
    def test_lowering_and_restoring_is_idempotent(self):
        seen = []

        with mock.patch.object(resources, "_set_priority",
                               lambda *, below_normal: seen.append(below_normal) or True):
            self.assertTrue(resources.lower_priority({}))
            self.assertTrue(resources.lower_priority({}))  # already lowered
            self.assertTrue(resources.restore_priority({}))
            self.assertFalse(resources.restore_priority({}))  # already restored
        self.assertEqual(seen, [True, False])

    def test_the_kill_switch_stops_it_touching_priority(self):
        seen = []
        with mock.patch.object(resources, "_set_priority",
                               lambda *, below_normal: seen.append(below_normal) or True):
            resources.lower_priority({"supercomputer_resource_governor_enabled": False})
        self.assertEqual(seen, [])


class TestModes(_Case):
    def test_unknown_modes_are_ignored(self):
        resources.set_mode("turbo")
        self.assertEqual(resources.mode(), resources.MODE_AUTO)

    def test_reset_returns_to_the_configured_default(self):
        resources.set_mode(resources.MODE_ECO)
        resources.reset_mode({"supercomputer_resource_mode": "performance"})
        self.assertEqual(resources.mode(), resources.MODE_PERFORMANCE)
        resources.reset_mode({})
        self.assertEqual(resources.mode(), resources.MODE_AUTO)

    def test_snapshot_is_renderable(self):
        snap = resources.snapshot({})
        for key in ("mode", "modes", "pressure", "pause_s", "workers", "enabled"):
            self.assertIn(key, snap)


if __name__ == "__main__":
    unittest.main()
