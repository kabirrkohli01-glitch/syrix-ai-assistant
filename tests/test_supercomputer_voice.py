"""Supercomputer's voice session.

Two things carry the weight:

**Isolation** — it must not share a single global with SYRIX's existing voice
mode. The audit found `syrix_voice_session`, `syrix_voice_engine._ABORT` and
`syrix_kokoro_tts._PIPELINE` are all module-level singletons, so sharing any of
them would cross-cancel a live utterance.

**Teardown** — the microphone must never be left attached. Toggle off, mode
exit, and a failure part-way through all have to release it.

The testkit stubs every audio path, so nothing here can play sound or open a
real microphone.
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

from syrix_supercomputer import hud_api, session, voice  # noqa: E402

_CFG = {"supercomputer_enabled": True, "supercomputer_voice_enabled": True}


class _VoiceCase(SupercomputerTestMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.events = []
        voice.set_publisher(self.events.append)


class TestLifecycle(_VoiceCase):
    def test_start_attaches_the_mic_and_listens(self):
        res = voice.start(_CFG)
        self.assertTrue(res["ok"])
        self.assertTrue(voice.is_active())
        self.assertEqual(voice.state(), voice.STATE_LISTENING)
        self.assertIn("attach", self.mic_attached_calls)

    def test_start_is_idempotent(self):
        voice.start(_CFG)
        second = voice.start(_CFG)
        self.assertEqual(second["reason"], "already_active")

    def test_stop_releases_the_microphone(self):
        voice.start(_CFG)
        voice.stop()
        self.assertFalse(voice.is_active())
        self.assertEqual(voice.state(), voice.STATE_OFF)
        self.assertIn("detach", self.mic_attached_calls)

    def test_stop_when_off_is_safe(self):
        res = voice.stop()
        self.assertTrue(res["ok"])
        self.assertFalse(res["was_active"])

    def test_the_mic_is_released_even_if_playback_teardown_fails(self):
        """Leaving the microphone attached is the one failure the user would not
        forgive, so it happens in a finally."""
        voice.start(_CFG)

        def _boom():
            raise RuntimeError("audio device gone")

        voice._stop_playback = _boom
        voice.stop()
        self.assertIn("detach", self.mic_attached_calls)
        self.assertFalse(voice.is_active())

    def test_a_missing_microphone_is_reported_not_faked(self):
        voice._mic = lambda: None
        res = voice.start(_CFG)
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "microphone_unavailable")
        self.assertFalse(voice.is_active())

    def test_disabled_voice_never_starts(self):
        res = voice.start({"supercomputer_enabled": True,
                           "supercomputer_voice_enabled": False})
        self.assertFalse(res["ok"])
        self.assertFalse(voice.is_active())

    def test_mode_exit_stops_voice_and_releases_the_mic(self):
        session.enter(_CFG)
        voice.start(_CFG)
        session.exit_mode(_CFG)
        self.assertFalse(voice.is_active())
        self.assertIn("detach", self.mic_attached_calls)


class TestInterruption(_VoiceCase):
    def test_interrupt_bumps_the_generation(self):
        voice.start(_CFG)
        before = voice.generation()
        voice.interrupt()
        self.assertGreater(voice.generation(), before)

    def test_interrupt_returns_to_listening(self):
        voice.start(_CFG)
        voice.interrupt()
        self.assertEqual(voice.state(), voice.STATE_LISTENING)

    def test_a_stale_generation_never_speaks(self):
        """A reply whose turn was superseded must not start playing late — and
        must bail BEFORE loading a TTS model, not after."""
        import syrix_supercomputer.voice as v

        v.start(_CFG)
        v.speak = self._prev_voice["speak"]  # exercise the real guard
        issued_under = v.generation()

        v.interrupt()  # The user barged in; that turn is over

        # The reply from the superseded turn arrives late and must not play.
        # It must also bail BEFORE loading a TTS model: reaching SPEAKING would
        # mean it got as far as synthesising.
        reached_speaking = []
        real_state = v._set_state
        v._set_state = lambda new: (reached_speaking.append(new)
                                    if new == v.STATE_SPEAKING else None) or real_state(new)
        try:
            res = v.speak("stale reply", config=_CFG, generation_at_issue=issued_under)
        finally:
            v._set_state = real_state

        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "interrupted")
        self.assertEqual(reached_speaking, [], "it must not start synthesising")

    def test_speaking_while_inactive_is_refused(self):
        voice.speak = self._prev_voice["speak"]
        res = voice.speak("hello", config=_CFG)
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "voice_inactive")


class TestVoiceSelection(_VoiceCase):
    def test_male_and_female_both_resolve(self):
        self.assertEqual(voice.voice_id({"supercomputer_voice_gender": "female"}),
                         voice.VOICE_FEMALE)
        self.assertEqual(voice.voice_id({"supercomputer_voice_gender": "male"}),
                         voice.VOICE_MALE)

    def test_an_unknown_gender_falls_back_to_female(self):
        self.assertEqual(voice.voice_id({"supercomputer_voice_gender": "wharrgarbl"}),
                         voice.VOICE_FEMALE)

    def test_preview_does_not_start_a_session(self):
        """Previewing a voice in settings must not leave the mic attached."""
        res = voice.preview_voice("male", config=_CFG)
        self.assertTrue(res["ok"])
        self.assertFalse(voice.is_active())
        self.assertNotIn("attach", self.mic_attached_calls)


class TestHudCommands(_VoiceCase):
    def test_voice_commands_route_and_toggle(self):
        ok, _ = hud_api.handle_command("supercomputer_voice_on", {}, _CFG)
        self.assertTrue(ok)
        self.assertTrue(voice.is_active())

        ok, _ = hud_api.handle_command("supercomputer_voice_off", {}, _CFG)
        self.assertTrue(ok)
        self.assertFalse(voice.is_active())

    def test_interrupt_command_routes(self):
        hud_api.handle_command("supercomputer_voice_on", {}, _CFG)
        ok, _ = hud_api.handle_command("supercomputer_voice_interrupt", {}, _CFG)
        self.assertTrue(ok)

    def test_preview_command_routes(self):
        ok, _ = hud_api.handle_command("supercomputer_voice_preview",
                                       {"gender": "male"}, _CFG)
        self.assertTrue(ok)
        self.assertIn("preview:male", self.spoken)

    def test_voice_state_reaches_the_snapshot(self):
        voice.start(_CFG)
        snap = session.snapshot(_CFG)
        self.assertTrue(snap["voice"]["active"])
        self.assertTrue(snap["voice"]["mic_attached"])


class TestListenLoop(_VoiceCase):
    """Capture → partial → final → reply → speak, with the seams injected."""

    def setUp(self):
        super().setUp()
        self.utterances = []
        self.turns = []
        voice.set_transcriber(lambda pcm, rate: "what is on this page")
        voice.set_turn_handler(lambda text: (self.turns.append(text) or "Here is the page."))

    def _drive_once(self, pcm=b"\x00\x01" * 800, partials=("what", "what is")):
        """Run one iteration of the loop with a fake collector."""
        import syrix_host_audio

        real = syrix_host_audio.collect_utterance_from_queue
        state = {"calls": 0}

        def _fake_collect(view, **kw):
            state["calls"] += 1
            if state["calls"] > 1:
                voice.stop()          # end the loop after one utterance
                return b""
            on_partial = kw.get("on_partial")
            if on_partial:
                for _ in partials:
                    on_partial(pcm, view.processing_rate)
            return pcm

        syrix_host_audio.collect_utterance_from_queue = _fake_collect
        try:
            voice._listen_loop(_CFG)
        finally:
            syrix_host_audio.collect_utterance_from_queue = real


    def test_the_loop_does_not_run_when_inactive(self):
        voice._listen_loop(_CFG)   # never started
        self.assertEqual(self.turns, [])

    def test_the_mic_view_exposes_only_what_the_collector_needs(self):
        """It stands in for a broadcaster; anything more would be a shortcut to
        the real device."""
        self.assertEqual(set(voice._MicView.__slots__),
                         {"command_queue", "processing_rate", "output_chunk"})


if __name__ == "__main__":
    unittest.main()
