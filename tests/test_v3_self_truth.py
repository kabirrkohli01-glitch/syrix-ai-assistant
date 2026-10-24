"""SYRIX answers questions about itself from itself, never from the web.

Live failure, 2026-10-07: "can u tell me more about yourself" cited ovirium.com
and techcrunch.com, and "What are the core technologies behind your AI model?"
was searched, found syrix.io (an unrelated company), and described that
product's architecture — layer counts, a vector database, a cloud host — as
SYRIX's own.
"""
from __future__ import annotations

import pytest

from syrix_v3 import creator, status
from syrix_v3.research import triage


@pytest.mark.parametrize("text", [
    "What are the core technologies behind your AI model?",
    "what tech stack do you use",
    "how were you trained",
    "what llm do you use",
    "what model powers you",
    "what ai is behind you",
    "tell me about your architecture",
    "can u tell me more about yourself ?",
])
def test_questions_about_how_syrix_is_built_are_about_syrix(text):
    assert triage.assess(text).reason == triage.SELF_REASON


@pytest.mark.parametrize("text", [
    "how do you install the latest cuda toolkit",
    "how do you train a model in pytorch",
    "what tech stack does netflix use",
    "what are the core technologies behind tesla autopilot",
    "what model runs the best on a 4090",
    "how do you make sourdough",
])
def test_the_impersonal_you_still_goes_to_the_web(text):
    assert triage.assess(text).reason != triage.SELF_REASON


def test_a_self_question_is_not_offered_the_web():
    import syrix_v3.turn as turn
    from syrix_v3.tools import names
    assert {"search", "read", "find_on_page"} <= set(names())
    assert turn._WEB_TOOLS == {"search", "read", "find_on_page"}
    kept = [t for t in names() if t not in turn._WEB_TOOLS]
    assert "self_knowledge" in kept and "runtime_status" in kept


def test_a_tool_that_was_not_offered_is_answered_but_not_run(monkeypatch):
    from syrix_v3 import loop
    ran = []
    monkeypatch.setattr(loop, "run_round", lambda calls, tctx, on_step=None: ran.extend(calls) or [])
    # The guard's own logic, as the loop applies it.
    offered = {"self_knowledge", "runtime_status"}
    tool_calls = [{"id": "1", "name": "search"}, {"id": "2", "name": "self_knowledge"}]
    calls = [c for c in tool_calls if c["name"] in offered]
    assert [c["name"] for c in calls] == ["self_knowledge"]
    import inspect
    src = inspect.getsource(loop.run)
    assert "not available for this question" in src
    assert "offered = set(tool_names) if tool_names is not None else None" in src
    assert "name in _registered()" in src


def test_how_syrix_is_built_is_stated_and_inventions_are_ruled_out():
    block = status.capability_block()
    assert "Python backend and an Electron interface" in block
    assert "faster-whisper" in block and "Kokoro" in block
    assert "never invent layer" in block
    assert "syrix.io" in block and "unrelated" in block


def test_the_ecosystem_name_is_spelled_correctly():
    text = "I power VANTAGE, Kohli, Kabir Education Partners and GMC Hub."
    fixed, note = creator.enforce("tell me about yourself", text, lang=None)
    assert "Kabirr Education Partners" in fixed and note
    ok = "Kabirr Education Partners is one of his apps."
    assert creator.enforce("tell me about yourself", ok, lang=None) == (ok, "")
