"""The local model's lifecycle.

Two properties matter and both are about not being left running: a model
resident in VRAM keeps the card awake, which is heat and watts on a machine
that is also used for games, and a server that outlives the app that
started it is exactly what the owner asked never to happen again.

No test here starts a real server.
"""
from __future__ import annotations

import time

import pytest

from syrix_v3 import local_model


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    import syrix_v3.state as state

    def _directory(*parts):
        path = tmp_path.joinpath(*parts)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    monkeypatch.setattr(state, "directory", _directory)
    monkeypatch.setattr(local_model, "_child", None)
    monkeypatch.setattr(local_model, "_ours", False)
    monkeypatch.setattr(local_model, "_last_used", 0.0)
    yield


class _FakeChild:
    def __init__(self, pid=4242):
        self.pid = pid
        self.terminated = False
        self.waited = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        self.waited = True
        return 0


def test_nothing_starts_until_something_asks(monkeypatch):
    """No boot-time launch. The GPU stays idle until a turn needs it.

    ``is_listening`` is stubbed rather than trusted: this machine may well
    have a server up for some other reason, and a test that reads the real
    port is asserting about the room instead of about the code.
    """
    monkeypatch.setattr(local_model, "is_listening", lambda timeout=1.0: False)
    spawned = []
    monkeypatch.setattr(local_model, "_spawn", lambda: spawned.append(1) or True)

    assert local_model.status().running is False
    assert spawned == [], "importing or inspecting the module started a server"


def test_an_already_listening_server_is_used_not_restarted(monkeypatch):
    monkeypatch.setattr(local_model, "is_listening", lambda timeout=1.0: True)
    spawned = []
    monkeypatch.setattr(local_model, "_spawn", lambda: spawned.append(1) or True)

    assert local_model.ensure_running() is True
    assert spawned == [], "started a second server on top of a live one"


def test_a_server_we_did_not_start_is_never_killed(monkeypatch):
    """Using someone else's server does not give us the right to stop it."""
    monkeypatch.setattr(local_model, "is_listening", lambda timeout=1.0: True)
    killed = []
    monkeypatch.setattr(local_model, "_kill", lambda pid: killed.append(pid) or True)

    local_model.ensure_running()
    assert local_model.stop() is False
    assert killed == []


def test_the_one_we_started_is_stopped(monkeypatch):
    """The whole tree, not just the parent.

    The server holds the loaded model in a separate runner process.
    Terminating only the parent closed the port and left 5.1 GB of VRAM
    occupied by a process nothing was tracking any more — status read "not
    running" while the card was still full.
    """
    child = _FakeChild(pid=5150)
    killed = []
    monkeypatch.setattr(local_model, "_child", child)
    monkeypatch.setattr(local_model, "_ours", True)
    monkeypatch.setattr(local_model, "_kill", lambda pid: killed.append(pid) or True)
    monkeypatch.setattr(local_model, "is_listening", lambda timeout=1.0: False)
    monkeypatch.setattr(local_model, "_sweep_strays", lambda: 0)

    assert local_model.stop() is True
    assert 5150 in killed, "killed the parent only, orphaning the model runner"
    assert local_model._child is None
    assert local_model._ours is False


def test_stopping_sweeps_anything_left_behind(monkeypatch):
    child = _FakeChild()
    swept = []
    monkeypatch.setattr(local_model, "_child", child)
    monkeypatch.setattr(local_model, "_ours", True)
    monkeypatch.setattr(local_model, "_kill", lambda pid: True)
    monkeypatch.setattr(local_model, "is_listening", lambda timeout=1.0: False)
    monkeypatch.setattr(local_model, "_sweep_strays", lambda: swept.append(1) or 1)

    local_model.stop()
    assert swept == [1], "left strays holding VRAM after the server was down"


def test_a_live_server_is_not_swept(monkeypatch):
    """Sweeping is only ever safe when nothing is serving."""
    child = _FakeChild()
    swept = []
    monkeypatch.setattr(local_model, "_child", child)
    monkeypatch.setattr(local_model, "_ours", True)
    monkeypatch.setattr(local_model, "_kill", lambda pid: True)
    monkeypatch.setattr(local_model, "is_listening", lambda timeout=1.0: True)
    monkeypatch.setattr(local_model, "_sweep_strays", lambda: swept.append(1) or 1)

    local_model.stop()
    assert swept == []


def test_stopping_twice_is_harmless():
    assert local_model.stop() is False
    assert local_model.stop() is False


def test_a_failed_start_leaves_nothing_behind(monkeypatch):
    """A server that never answers must not be left half-running."""
    monkeypatch.setattr(local_model, "is_listening", lambda timeout=1.0: False)
    child = _FakeChild()

    def _spawn():
        local_model._child = child
        local_model._ours = True
        return True

    killed = []
    monkeypatch.setattr(local_model, "_spawn", _spawn)
    monkeypatch.setattr(local_model, "_kill", lambda pid: killed.append(pid) or True)
    monkeypatch.setattr(local_model, "_sweep_strays", lambda: 0)

    assert local_model.ensure_running(timeout_s=2.0) is False
    assert killed, "a server that never answered was left running"
    assert local_model._child is None


def test_an_orphan_from_a_crashed_session_is_reaped(monkeypatch):
    """A pid this module recorded and never cleaned up gets killed on start."""
    killed = []
    monkeypatch.setattr(local_model, "_kill", lambda pid: killed.append(pid) or True)
    local_model._write_pid(9876)

    assert local_model.reap_orphan() is True
    assert killed == [9876]
    # And the record goes, so the next start does not kill a recycled pid.
    assert local_model._read_pid() == 0


def test_nothing_is_reaped_when_there_is_no_record(monkeypatch):
    killed = []
    monkeypatch.setattr(local_model, "_kill", lambda pid: killed.append(pid) or True)
    assert local_model.reap_orphan() is False
    assert killed == []


def test_weights_leave_vram_quickly():
    """A long keep-alive is a GPU that will not clock down."""
    seconds = int(local_model._KEEP_ALIVE.rstrip("smh"))
    assert local_model._KEEP_ALIVE.endswith("s")
    assert seconds <= 120, "weights would sit in VRAM too long between turns"


def test_the_server_is_stopped_after_a_quiet_spell():
    assert 60.0 <= local_model._IDLE_STOP_S <= 900.0


def test_use_defers_the_idle_stop():
    local_model.note_use()
    first = local_model._last_used
    assert first > 0
    time.sleep(0.01)
    local_model.note_use()
    assert local_model._last_used > first


def test_it_is_registered_to_die_with_the_process():
    import atexit
    registered = [f for f, _a, _k in getattr(atexit, "_exithandlers", [])] \
        if hasattr(atexit, "_exithandlers") else None
    if registered is None:
        pytest.skip("this interpreter does not expose atexit handlers")
    assert local_model.stop in registered
