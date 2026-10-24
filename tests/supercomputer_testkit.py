"""Shared fixtures for the Supercomputer test suite.

Modelled on ``connectors_testkit`` — save-and-restore of module attributes, and
crucially a **per-test** ``syrix_host`` stub.

NOTE: the syrix_host stub is installed in ``setUp`` and removed in
``tearDown``, never at module import. A module-level
``sys.modules['syrix_host'] = stub`` leaked once already: every later test in
the suite that imports the real 60k-line syrix_host got the stub instead and
failed. Supercomputer code only lazy-imports syrix_host inside functions, so a
per-test stub is sufficient and fully isolated.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def make_jc_stub() -> types.ModuleType:
    """Fresh syrix_host stub for one test (captures hud-state writes)."""
    stub = types.ModuleType("syrix_host")
    stub.__is_test_stub__ = True
    stub._hud_updates = []
    stub._island_calls = []

    def _syrix_update_hud_state(fields):  # noqa: ANN001
        stub._hud_updates.append(dict(fields))
        return True

    def _publish_island_notice(text, **kw):  # noqa: ANN001
        stub._island_calls.append((text, kw))
        return True

    stub._syrix_update_hud_state = _syrix_update_hud_state
    stub._publish_island_notice = _publish_island_notice
    return stub


class SupercomputerTestMixin:
    """setUp/tearDown: isolate Supercomputer state + HUD writes to a temp dir."""

    def setUp(self):  # noqa: N802
        super().setUp()
        self._tmp = tempfile.mkdtemp(prefix="syrix_sc_test_")

        # Per-test syrix_host stub; remember whatever was there before.
        self._had_jc = "syrix_host" in sys.modules
        self._prev_jc = sys.modules.get("syrix_host")
        self.jc = make_jc_stub()
        sys.modules["syrix_host"] = self.jc

        from syrix_supercomputer import paths as sc_paths

        self._prev_dir_fn = sc_paths.supercomputer_dir
        sc_paths.supercomputer_dir = lambda: self._tmp

        # Stub the browser transport. Without this every verb blocks for its
        # full timeout waiting on a renderer that does not exist in a test
        # process — one annotate call once parked the suite for five minutes.
        # Cases that care about transport behaviour override controller.send
        # themselves after calling super().setUp().
        from syrix_supercomputer.browser import controller as sc_controller

        self._prev_send = sc_controller.send
        self.sent_ops = []

        def _stub_send(verb, *, timeout_s=12.0, **fields):
            self.sent_ops.append(dict(fields, verb=verb))
            return {"ok": True, "reason": "", "data": {}}

        sc_controller.send = _stub_send

        # Stub every audio path. The user's standing instruction is that nothing
        # plays sound or opens the microphone unannounced, and a test suite is
        # the last place that should happen — `voice_preview` really did speak
        # through the speakers before this was added. Cases that test voice
        # behaviour assert on these recordings instead.
        from syrix_supercomputer import voice as sc_voice

        self._prev_voice = {
            "speak": sc_voice.speak,
            "preview": sc_voice.preview_voice,
            "mic": sc_voice._mic,
            "playback": sc_voice._stop_playback,
            "listener": sc_voice._start_listener,
        }
        self.spoken = []
        self.mic_attached_calls = []

        # Fake the DEVICE, not the attach logic, so the real
        # add/remove_consumer_queue path is what the tests exercise.
        kit = self

        class _FakeMic:
            def add_consumer_queue(self, q):
                kit.mic_attached_calls.append("attach")

            def remove_consumer_queue(self, q):
                kit.mic_attached_calls.append("detach")
                return True

        sc_voice._mic = lambda: _FakeMic()
        sc_voice.speak = lambda text, **kw: (
            self.spoken.append(str(text)) or {"ok": True, "segments": 1})
        sc_voice.preview_voice = lambda gender, **kw: (
            self.spoken.append(f"preview:{gender}") or {"ok": True, "voice": gender})
        sc_voice._stop_playback = lambda: None
        # The capture thread would block on an empty queue; tests that care
        # about the loop drive _listen_loop directly with injected seams.
        self.listener_started = []
        sc_voice._start_listener = lambda cfg=None: self.listener_started.append(cfg)

        self._reset_modules()

    def tearDown(self):  # noqa: N802
        from syrix_supercomputer import paths as sc_paths
        from syrix_supercomputer.browser import controller as sc_controller

        from syrix_supercomputer import voice as sc_voice

        sc_paths.supercomputer_dir = self._prev_dir_fn
        sc_controller.send = self._prev_send
        sc_voice.speak = self._prev_voice["speak"]
        sc_voice.preview_voice = self._prev_voice["preview"]
        sc_voice._mic = self._prev_voice["mic"]
        sc_voice._stop_playback = self._prev_voice["playback"]
        sc_voice._start_listener = self._prev_voice["listener"]

        if self._had_jc:
            sys.modules["syrix_host"] = self._prev_jc
        else:
            sys.modules.pop("syrix_host", None)

        self._reset_modules()
        shutil.rmtree(self._tmp, ignore_errors=True)
        super().tearDown()

    @staticmethod
    def _reset_modules() -> None:
        from syrix_supercomputer import (hud_api, memory, models, permissions,
                                         preview, resume, results, safety_mode,
                                         session, spine, tasks, voice)
        from syrix_supercomputer.browser import controller

        session._reset_for_tests()
        permissions._reset_for_tests()
        tasks._reset_for_tests()
        results._reset_for_tests()
        controller._reset_for_tests()
        hud_api._reset_for_tests()
        voice._reset_for_tests()
        models._reset_for_tests()
        preview._reset_for_tests()
        # Cross-test leakage here is silent and confusing: a task brief or a
        # parked "awaiting a reply" record from one test changes what the NEXT
        # test's agent is told it already knows.
        memory._reset_for_tests()
        resume._reset_for_tests()
        safety_mode._reset_for_tests()
        spine.forget()

    # -- helpers ------------------------------------------------------------
    def hud_snapshots(self):
        """Every `syrix_supercomputer` payload written during this test."""
        return [u["syrix_supercomputer"] for u in self.jc._hud_updates
                if "syrix_supercomputer" in u]


__all__ = ["SupercomputerTestMixin", "make_jc_stub"]
