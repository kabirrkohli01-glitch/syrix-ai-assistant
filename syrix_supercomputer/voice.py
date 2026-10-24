"""Supercomputer's own voice session.

Completely separate from SYRIX's existing voice mode, and it has to be: the
audit found ``syrix_voice_session``, ``syrix_voice_engine._ABORT`` and
``syrix_kokoro_tts._PIPELINE`` are all module-level singletons. Sharing any of
them would cross-cancel a live voice-mode utterance.

So this module owns:

* its own state and abort flag;
* its own WS event type (``sc_voice_turn``), so the existing voice overlay never
  consumes these events;
* its own playback path, with a private abort — it calls Kokoro to *synthesise*
  (sharing the loaded model saves 312 MB) but never calls the shared
  ``unload_pipeline()``, which would yank the model out from under voice mode.

The microphone is borrowed, not opened: it attaches an extra consumer queue to
the existing ``MicBroadcaster`` (see ``syrix_host_audio.add_consumer_queue``). A second
PyAudio input stream would fight the echo guard and the STT unload watcher for
the same device.

Teardown is guaranteed on toggle-off, on mode exit, and in a ``finally``. The
microphone must never be left attached.
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Any, Callable, Dict, List, Optional

import syrix_obs as obs

WS_EVENT = "sc_voice_turn"

STATE_OFF = "off"
STATE_LISTENING = "listening"
STATE_THINKING = "thinking"
STATE_SPEAKING = "speaking"

VOICE_FEMALE = "af_heart"
VOICE_MALE = "am_michael"
VOICES = {"female": VOICE_FEMALE, "male": VOICE_MALE}

_LOCK = threading.RLock()
_ACTIVE = False
_STATE = STATE_OFF
_QUEUE: Optional["queue.Queue[bytes]"] = None
_ABORT = threading.Event()          # private — never the shared engine's flag
_GENERATION = 0
_PUBLISHER: Optional[Callable[[Dict[str, Any]], None]] = None
_LAST_ERROR = ""


def set_publisher(fn: Optional[Callable[[Dict[str, Any]], None]]) -> None:
    """Injection seam for tests, mirroring syrix_voice_session.set_publisher."""
    global _PUBLISHER
    _PUBLISHER = fn


def enabled(config: Optional[Dict[str, Any]] = None) -> bool:
    cfg = config or {}
    return bool(cfg.get("supercomputer_enabled", True)) and bool(
        cfg.get("supercomputer_voice_enabled", True))


def voice_id(config: Optional[Dict[str, Any]] = None) -> str:
    choice = str((config or {}).get("supercomputer_voice_gender", "female") or "").lower()
    return VOICES.get(choice, VOICE_FEMALE)


def is_active() -> bool:
    with _LOCK:
        return _ACTIVE


def state() -> str:
    with _LOCK:
        return _STATE


def generation() -> int:
    with _LOCK:
        return _GENERATION


def _publish(kind: str, **fields: Any) -> None:
    payload = {"type": WS_EVENT, "kind": kind, "ts": time.time()}
    payload.update(fields)
    fn = _PUBLISHER
    if fn is not None:
        with obs.swallow("supercomputer.voice.publish_injected"):
            fn(payload)
        return
    with obs.swallow("supercomputer.voice.publish"):
        import syrix_host_hud_bridge

        syrix_host_hud_bridge.ws_send(payload)


def _set_state(new: str) -> None:
    global _STATE
    with _LOCK:
        _STATE = new
    _publish("state", state=new)


# ── microphone ─────────────────────────────────────────────────────────────
def _mic():
    """The one canonical mic stream, or None when it is not up."""
    try:
        import syrix_host_infra

        getter = getattr(syrix_host_infra, "get_primary_mic_stream", None)
        return getter() if callable(getter) else None
    except Exception as exc:
        obs.report("supercomputer.voice.mic_lookup", exc, level="debug")
        return None


def _attach_mic() -> bool:
    global _QUEUE
    mic = _mic()
    if mic is None or not hasattr(mic, "add_consumer_queue"):
        return False
    q: "queue.Queue[bytes]" = queue.Queue(maxsize=256)
    mic.add_consumer_queue(q)
    with _LOCK:
        _QUEUE = q
    return True


def _detach_mic() -> None:
    """Release the microphone. Idempotent, and safe when the mic is gone."""
    global _QUEUE
    with _LOCK:
        q = _QUEUE
        _QUEUE = None
    if q is None:
        return
    mic = _mic()
    if mic is not None and hasattr(mic, "remove_consumer_queue"):
        with obs.swallow("supercomputer.voice.detach"):
            mic.remove_consumer_queue(q)


def mic_attached() -> bool:
    with _LOCK:
        return _QUEUE is not None


# ── lifecycle ──────────────────────────────────────────────────────────────
def start(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Turn the Supercomputer voice toggle on. Idempotent."""
    global _ACTIVE, _GENERATION
    if not enabled(config):
        return {"ok": False, "reason": "voice_disabled"}
    with _LOCK:
        if _ACTIVE:
            return {"ok": True, "reason": "already_active"}
        _ACTIVE = True
        _GENERATION += 1
    _ABORT.clear()
    if not _attach_mic():
        # No mic is a real, reportable condition — not something to paper over.
        stop(reason="no_microphone")
        return {"ok": False, "reason": "microphone_unavailable"}
    _set_state(STATE_LISTENING)
    _start_listener(config)
    _publish("started", voice=voice_id(config))
    return {"ok": True, "voice": voice_id(config)}


def _start_listener(config: Optional[Dict[str, Any]]) -> None:
    global _listener
    if _listener is not None and _listener.is_alive():
        return
    t = threading.Thread(target=_listen_loop, args=(config,),
                         name="sc-voice-listen", daemon=True)
    _listener = t
    t.start()


def stop(*, reason: str = "user_toggle") -> Dict[str, Any]:
    """Turn it off and release everything. Safe to call when already off.

    The microphone release happens in a ``finally`` because leaving it attached
    is the one failure here that the user would rightly not forgive.
    """
    global _ACTIVE, _STATE, _GENERATION
    with _LOCK:
        was = _ACTIVE
        _ACTIVE = False
        _GENERATION += 1
    _ABORT.set()
    # Teardown must never raise: a failure stopping playback cannot be allowed
    # to skip the microphone release, and stop() is called from mode exit.
    try:
        with obs.swallow("supercomputer.voice.stop_playback_teardown"):
            _stop_playback()
    finally:
        _detach_mic()
    with _LOCK:
        _STATE = STATE_OFF
    if was:
        _publish("stopped", reason=reason)
    return {"ok": True, "was_active": was}


def interrupt(*, reason: str = "barge_in") -> Dict[str, Any]:
    """Stop speaking immediately and go back to listening."""
    global _GENERATION
    with _LOCK:
        _GENERATION += 1
    _ABORT.set()
    _stop_playback()
    _ABORT.clear()
    if is_active():
        _set_state(STATE_LISTENING)
    _publish("interrupted", reason=reason)
    return {"ok": True}


def _stop_playback() -> None:
    with obs.swallow("supercomputer.voice.stop_playback"):
        import sounddevice as sd

        sd.stop()


# ── listen loop ────────────────────────────────────────────────────────────
class _MicView:
    """Adapter presenting our own queue to ``collect_utterance_from_queue``.

    That function reads exactly three attributes off a broadcaster, so a shim is
    enough to reuse its endpointing — Silero VAD gate, adaptive noise floor,
    pre-roll and partial-transcript callbacks — instead of reimplementing all of
    it against our own queue and getting the edge cases subtly wrong.
    """

    __slots__ = ("command_queue", "processing_rate", "output_chunk")

    def __init__(self, q, mic):
        self.command_queue = q
        self.processing_rate = int(getattr(mic, "processing_rate", 16000) or 16000)
        self.output_chunk = int(getattr(mic, "output_chunk", 1024) or 1024)


_transcriber: Optional[Callable[[bytes, int], str]] = None
_turn_handler: Optional[Callable[[str], Optional[str]]] = None
_listener: Optional[threading.Thread] = None


def set_transcriber(fn: Optional[Callable[[bytes, int], str]]) -> None:
    """Injection seam. Defaults to SYRIX's faster-whisper when unset."""
    global _transcriber
    _transcriber = fn


def set_turn_handler(fn: Optional[Callable[[str], Optional[str]]]) -> None:
    """What a finished utterance does. Defaults to a Supercomputer turn."""
    global _turn_handler
    _turn_handler = fn


def _default_transcribe(pcm: bytes, rate: int) -> str:
    try:
        import syrix_host as _jc

        fn = getattr(_jc, "transcribe_audio_with_whisper", None)
        if not callable(fn):
            return ""
        out = fn(pcm, source_sample_rate=rate)
        if isinstance(out, tuple):
            out = out[0]
        return str(out or "").strip()
    except Exception as exc:
        obs.report("supercomputer.voice.transcribe", exc)
        return ""


def _default_turn(text: str) -> Optional[str]:
    """Route a spoken utterance through the Supercomputer spine."""
    try:
        from . import spine

        env = spine.handle_turn(text, source="supercomputer_voice")
        return getattr(env, "speak_text", "") or getattr(env, "chat_text", "")
    except Exception as exc:
        obs.report("supercomputer.voice.turn", exc)
        return None


def _listen_loop(config: Optional[Dict[str, Any]]) -> None:
    """Capture → partial transcripts → final → reply → speak, until stopped."""
    cfg = config or {}
    while is_active():
        gen = generation()
        with _LOCK:
            q = _QUEUE
        mic = _mic()
        if q is None or mic is None:
            return

        try:
            from syrix_host_audio import collect_utterance_from_queue
        except Exception as exc:
            obs.report("supercomputer.voice.collector_import", exc)
            return

        view = _MicView(q, mic)
        last_partial = {"text": ""}

        def _on_partial(pcm: bytes, rate: int) -> None:
            # Streamed while the user is still talking, so the workspace can
            # show words appearing rather than a spinner.
            if generation() != gen or not is_active():
                return
            text = (_transcriber or _default_transcribe)(pcm, rate)
            if text and text != last_partial["text"]:
                last_partial["text"] = text
                _publish("partial", text=text)

        try:
            pcm = collect_utterance_from_queue(
                view,
                max_seconds=float(cfg.get("supercomputer_voice_max_seconds", 20.0)),
                silence_seconds=float(cfg.get("supercomputer_voice_silence_seconds", 0.6)),
                pre_speech_timeout=3.0,
                silero_vad_enabled=bool(cfg.get("syrix_silero_vad_gate_enabled", False)),
                on_partial=_on_partial,
                discard_while=lambda: not is_active() or generation() != gen,
            )
        except Exception as exc:
            obs.report("supercomputer.voice.capture", exc)
            return

        if not is_active() or generation() != gen:
            return
        if not pcm:
            continue

        _set_state(STATE_THINKING)
        text = (_transcriber or _default_transcribe)(pcm, view.processing_rate)
        if not text:
            if is_active():
                _set_state(STATE_LISTENING)
            continue
        _publish("final", text=text)

        reply = (_turn_handler or _default_turn)(text)
        if reply and is_active() and generation() == gen:
            speak(reply, config=cfg, generation_at_issue=gen)
        elif is_active():
            _set_state(STATE_LISTENING)


# ── speech ─────────────────────────────────────────────────────────────────
def speak(
    text: str,
    *,
    config: Optional[Dict[str, Any]] = None,
    generation_at_issue: Optional[int] = None,
) -> Dict[str, Any]:
    """Speak one reply. Returns immediately if interrupted or inactive.

    ``generation_at_issue`` is the generation the turn was created under, the
    same pattern the browser controller uses. Without it a reply produced by an
    already-interrupted turn would still start playing, because a check taken
    at the top of this function reads the *current* generation, not the one the
    caller was working under.

    Uses Kokoro for synthesis but never touches the shared abort flag or the
    shared pipeline lifecycle — cross-cancelling voice mode is exactly what this
    module exists to avoid.
    """
    body = str(text or "").strip()
    if not body:
        return {"ok": False, "reason": "empty"}
    if not is_active():
        return {"ok": False, "reason": "voice_inactive"}

    gen = generation()
    if generation_at_issue is not None and int(generation_at_issue) != gen:
        return {"ok": False, "reason": "interrupted", "segments": 0}
    if _ABORT.is_set():
        return {"ok": False, "reason": "interrupted", "segments": 0}
    _set_state(STATE_SPEAKING)
    spoken = 0
    try:
        # Re-check before the heavy import: there is no point loading a TTS
        # model for a turn that has already been superseded.
        if generation() != gen or not is_active():
            return {"ok": False, "reason": "interrupted", "segments": 0}
        import numpy as np
        import sounddevice as sd

        import syrix_kokoro_tts as kok

        segments = kok.synthesize_streaming_segments(body, voice=voice_id(config))
        for seg in segments:
            if _ABORT.is_set() or generation() != gen or not is_active():
                return {"ok": False, "reason": "interrupted", "segments": spoken}
            audio, rate = _segment_audio(seg)
            if audio is None:
                continue
            sd.play(np.asarray(audio), rate)
            sd.wait()
            spoken += 1
            _publish("spoke", chars=len(body), segment=spoken)
    except Exception as exc:
        obs.report("supercomputer.voice.speak", exc)
        return {"ok": False, "reason": f"tts_failed:{type(exc).__name__}"}
    finally:
        if is_active():
            _set_state(STATE_LISTENING)
    return {"ok": True, "segments": spoken}


def _segment_audio(seg: Any):
    """Audio and sample rate from one synthesize_streaming_segments item.

    Items are ``(segment_text, audio, sample_rate)``. Reading item 0 handed
    the sentence text to sounddevice, so every spoken reply and every voice
    preview failed with TypeError before a sound was made.
    """
    if isinstance(seg, tuple):
        audio = seg[1] if len(seg) > 1 else None
        rate = int(seg[2]) if len(seg) > 2 and seg[2] else 24000
        return audio, rate
    return seg, 24000


def preview_voice(gender: str, *, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Settings preview. Speaks one line in the chosen voice without starting
    a session, so it cannot leave the microphone attached."""
    name = VOICES.get(str(gender or "").lower())
    if not name:
        return {"ok": False, "reason": "unknown_voice"}
    try:
        import numpy as np
        import sounddevice as sd

        import syrix_kokoro_tts as kok

        for seg in kok.synthesize_streaming_segments(
                "This is how I will sound in Supercomputer mode.", voice=name):
            audio, rate = _segment_audio(seg)
            if audio is not None:
                sd.play(np.asarray(audio), rate)
                sd.wait()
        return {"ok": True, "voice": name}
    except Exception as exc:
        obs.report("supercomputer.voice.preview", exc, voice=name)
        return {"ok": False, "reason": f"tts_failed:{type(exc).__name__}"}


def snapshot(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    with _LOCK:
        return {
            "active": _ACTIVE,
            "state": _STATE,
            "mic_attached": _QUEUE is not None,
            "voice": voice_id(config),
            "enabled": enabled(config),
            "generation": _GENERATION,
            "last_error": _LAST_ERROR,
        }


def _reset_for_tests() -> None:
    global _ACTIVE, _STATE, _QUEUE, _GENERATION, _LAST_ERROR, _PUBLISHER
    global _transcriber, _turn_handler, _listener
    _detach_mic()
    with _LOCK:
        _ACTIVE = False
        _STATE = STATE_OFF
        _QUEUE = None
        _GENERATION = 0
        _LAST_ERROR = ""
    _ABORT.clear()
    _PUBLISHER = None
    _transcriber = None
    _turn_handler = None
    _listener = None


__all__ = [
    "WS_EVENT", "STATE_OFF", "STATE_LISTENING", "STATE_THINKING", "STATE_SPEAKING",
    "VOICES", "VOICE_MALE", "VOICE_FEMALE", "set_publisher", "enabled", "voice_id",
    "is_active", "state", "generation", "start", "stop", "interrupt", "speak",
    "preview_voice", "snapshot", "mic_attached", "set_transcriber", "set_turn_handler",
]
