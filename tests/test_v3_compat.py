"""Third-party imports survive the host's safety wrapper around Popen."""
from __future__ import annotations

import subprocess

from syrix_v3 import compat


def test_a_wrapped_popen_can_be_subscripted_during_an_import_only(monkeypatch):
    original = subprocess.Popen
    calls = []

    def wrapper(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", wrapper)
    with compat.subscriptable_popen():
        assert subprocess.Popen[str] is not None      # what a class annotation does
    assert subprocess.Popen is wrapper                # the safety wrapper is back


def test_calls_during_the_import_still_go_through_the_wrapper(monkeypatch):
    seen = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: seen.append(a) or "ran")
    with compat.subscriptable_popen():
        assert subprocess.Popen(["x"]) == "ran"
    assert seen == [(["x"],)]


def test_a_real_popen_class_is_left_alone(monkeypatch):
    class RealPopen:  # stands in for the unwrapped class
        pass
    monkeypatch.setattr(subprocess, "Popen", RealPopen)
    before = subprocess.Popen
    with compat.subscriptable_popen():
        assert subprocess.Popen is before
    assert subprocess.Popen is before
