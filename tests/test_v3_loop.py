"""The loop's behavioural contract, pinned against a fake lane.

No network and no model: ``gateway.complete`` is replaced by a scripted
sequence, so each test states one rule about how the loop must behave and
fails for exactly that reason.

Each of these corresponds to something the measured baseline got wrong.
"""
from __future__ import annotations

import time
from dataclasses import replace

import pytest

from syrix_v3 import gateway, loop
from syrix_v3.budget import for_mode
from syrix_v3.tools import ToolContext, ToolResult, registry


class _Scripted:
    """Returns the next prepared completion each time it is called."""

    def __init__(self, *completions):
        self.queue = list(completions)
        self.calls = []

    def __call__(self, messages, **kwargs):
        self.calls.append({"messages": list(messages), **kwargs})
        if self.queue:
            return self.queue.pop(0)
        return gateway.Completion(text="fallback", provider="fake")


def _completion(text="", tool_calls=None):
    return gateway.Completion(
        text=text,
        tool_calls=list(tool_calls or []),
        provider="fake",
        model="fake-1",
    )


def _call(name, arguments="{}", call_id="c1"):
    return {"id": call_id, "name": name, "arguments": arguments}


@pytest.fixture
def fake_tool():
    """A registered tool that records its invocations."""
    seen = []

    def _run(args, tctx):
        seen.append((dict(args), tctx.request_id))
        return ToolResult(content=f"observed {args.get('q', '')}",
                          sources=[{"url": "https://example.test/a", "title": "A"}])

    registry.register(registry.Tool(
        name="probe_tool",
        description="test double",
        parameters={"type": "object", "properties": {"q": {"type": "string"}}},
        run=_run,
    ))
    yield seen
    registry._REGISTRY.pop("probe_tool", None)


def test_native_tool_call_round_trips(monkeypatch, fake_tool):
    """A tool_calls response runs the tool and feeds the result back.

    The runtime being replaced asked the model to write a tool request into
    its prose and parsed it back out with regexes, which is why tool markup
    reached the screen.
    """
    scripted = _Scripted(
        _completion(tool_calls=[_call("probe_tool", '{"q": "hello"}')]),
        _completion(text="the answer"),
    )
    monkeypatch.setattr(gateway, "complete", scripted)

    result = loop.run(
        [{"role": "user", "content": "q"}],
        budget=for_mode("spark"),
        request_id="rid1",
        tool_names=["probe_tool"],
    )

    assert result.text == "the answer"
    assert result.tool_calls == 1
    assert fake_tool == [({"q": "hello"}, "rid1")]
    assert result.sources == [{"url": "https://example.test/a", "title": "A"}]

    # The observation must go back as a tool message tied to the call id, not
    # as a user turn pretending to be one.
    second_round = scripted.calls[1]["messages"]
    tool_messages = [m for m in second_round if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "c1"
    assert "observed hello" in tool_messages[0]["content"]


def test_tools_are_offered_natively_not_as_prose(monkeypatch, fake_tool):
    scripted = _Scripted(_completion(text="done"))
    monkeypatch.setattr(gateway, "complete", scripted)

    loop.run([{"role": "user", "content": "q"}], budget=for_mode("zenith"),
             request_id="rid2", tool_names=["probe_tool"])

    offered = scripted.calls[0]["tools"]
    assert offered and offered[0]["function"]["name"] == "probe_tool"
    assert offered[0]["type"] == "function"


def test_expired_deadline_answers_instead_of_apologising(monkeypatch, fake_tool):
    """Out of time means answer from what you have, not give up.

    The baseline shipped "That took longer than I'm willing to wait" after
    sixty seconds for "what is the capital of france".
    """
    scripted = _Scripted(_completion(text="partial but real answer"))
    monkeypatch.setattr(gateway, "complete", scripted)

    plan = for_mode("spark").with_deadline(0.01)
    result = loop.run([{"role": "user", "content": "q"}], budget=plan,
                      request_id="rid3", tool_names=["probe_tool"],
                      started_at=time.monotonic() - 10.0)

    assert result.deadline_hit is True
    assert result.text == "partial but real answer"
    # Tools are withdrawn once the clock is up; there is nothing left to gather.
    assert scripted.calls[0]["tools"] is None
    instruction = scripted.calls[0]["messages"][-1]["content"]
    assert "from what you already have" in instruction
    assert "do not apologise" in instruction


def test_last_round_withdraws_tools_so_the_turn_must_answer(monkeypatch, fake_tool):
    scripted = _Scripted(
        _completion(tool_calls=[_call("probe_tool")]),
        _completion(text="final"),
    )
    monkeypatch.setattr(gateway, "complete", scripted)

    # A two-round budget so the second round IS the last one. Spark's own
    # round count varies with how hard the question looked; this test is
    # about the rule, not the number.
    plan = for_mode("spark")
    plan = replace(plan, max_rounds=2)
    result = loop.run([{"role": "user", "content": "q"}], budget=plan,
                      request_id="rid4", tool_names=["probe_tool"])

    assert result.rounds == 2
    assert scripted.calls[0]["tools"] is not None
    assert scripted.calls[1]["tools"] is None
    assert result.text == "final"


def test_a_failing_tool_returns_text_the_model_can_act_on(monkeypatch):
    """A tool that raises must not end the turn."""
    def _boom(args, tctx):
        raise RuntimeError("the site refused")

    registry.register(registry.Tool(
        name="boom_tool", description="always fails",
        parameters={"type": "object", "properties": {}}, run=_boom,
    ))
    try:
        scripted = _Scripted(
            _completion(tool_calls=[_call("boom_tool")]),
            _completion(text="answered anyway"),
        )
        monkeypatch.setattr(gateway, "complete", scripted)

        result = loop.run([{"role": "user", "content": "q"}], budget=for_mode("spark"),
                          request_id="rid5", tool_names=["boom_tool"])

        assert result.text == "answered anyway"
        observation = [m for m in scripted.calls[1]["messages"] if m.get("role") == "tool"][0]
        assert "the site refused" in observation["content"]
    finally:
        registry._REGISTRY.pop("boom_tool", None)


def test_an_unknown_tool_name_is_reported_not_guessed(monkeypatch):
    scripted = _Scripted(
        _completion(tool_calls=[_call("no_such_tool")]),
        _completion(text="recovered"),
    )
    monkeypatch.setattr(gateway, "complete", scripted)

    loop.run([{"role": "user", "content": "q"}], budget=for_mode("spark"),
             request_id="rid6", tool_names=["search"])

    observation = [m for m in scripted.calls[1]["messages"] if m.get("role") == "tool"][0]
    assert "no tool called" in observation["content"]


def test_an_empty_reply_is_asked_again_with_tools_withdrawn(monkeypatch, fake_tool):
    scripted = _Scripted(_completion(text=""), _completion(text="second time"))
    monkeypatch.setattr(gateway, "complete", scripted)

    result = loop.run([{"role": "user", "content": "q"}], budget=for_mode("zenith"),
                      request_id="rid7", tool_names=["probe_tool"])

    assert result.text == "second time"
    assert scripted.calls[1]["tools"] is None


def test_no_lane_available_ends_the_turn_without_raising(monkeypatch):
    def _no_lane(messages, **kwargs):
        raise gateway.NoLaneAvailable("everything is spent")

    monkeypatch.setattr(gateway, "complete", _no_lane)
    result = loop.run([{"role": "user", "content": "q"}], budget=for_mode("spark"),
                      request_id="rid8")

    assert result.text == ""
    assert any("no lane available" in n for n in result.notes)


def test_a_round_runs_every_call_at_once():
    """Three calls in one round take one call's time, not three."""
    def _slow(args, tctx):
        time.sleep(0.25)
        return ToolResult(content=f"slept {args.get('n')}")

    registry.register(registry.Tool(
        name="slow_tool", description="sleeps",
        parameters={"type": "object", "properties": {"n": {"type": "string"}}},
        run=_slow,
    ))
    try:
        calls = [_call("slow_tool", '{"n": "%d"}' % i, f"c{i}") for i in range(3)]
        tctx = ToolContext(request_id="rid9", budget=for_mode("spark"))
        started = time.monotonic()
        results = registry.run_round(calls, tctx)
        elapsed = time.monotonic() - started

        assert elapsed < 0.6, f"ran sequentially: {elapsed:.2f}s for 3 x 0.25s"
        # Order follows the requests so the observations read in the order asked.
        assert [r[0] for r in results] == ["c0", "c1", "c2"]
        assert [r[2].content for r in results] == ["slept 0", "slept 1", "slept 2"]
    finally:
        registry._REGISTRY.pop("slow_tool", None)


@pytest.mark.parametrize("raw,expected", [
    ('{"a": 1}', {"a": 1}),
    ({"a": 1}, {"a": 1}),
    ("{'a': 1}", {"a": 1}),
    ('{"a": 1,}', {"a": 1}),
    ("", {}),
    ("not json at all", {}),
    ("[1,2,3]", {}),
])
def test_tool_arguments_survive_a_model_that_writes_untidy_json(raw, expected):
    assert registry.parse_arguments(raw) == expected


def test_a_repair_that_still_fails_the_check_keeps_the_original(monkeypatch):
    """A repair note is one more instruction to agree with.

    Measured on the local lane: notes about a denied capability produced
    replies opening "Got it." that restated the false premise more firmly than
    the original had — concessions rose from 2 of 5 to 3 of 5. Only length was
    guarded, and this rewrite was not shorter, just wronger.
    """
    first = "I don't have access to the internet, so I cannot check that."
    worse = "Got it. Since I don't have internet access, let's work from what I know."
    scripted = _Scripted(_completion(text=first), _completion(text=worse))
    monkeypatch.setattr(gateway, "complete", scripted)

    result = loop.run([{"role": "user", "content": "you cant search the web right"}],
                      budget=for_mode("spark"), request_id="rid-rep",
                      question="you cant search the web right",
                      tool_names=[])

    assert result.repaired, "the check should have fired on the first answer"
    assert result.text == first
    assert any("did not clear the check" in n for n in result.notes), result.notes


def test_a_repair_that_fixes_the_problem_is_kept(monkeypatch):
    """The guard must not pin every turn to its first draft."""
    first = "I don't have access to the internet, so I cannot check that."
    better = ("I can search the web and read live pages, so I will go and "
              "check rather than guess.")
    scripted = _Scripted(_completion(text=first), _completion(text=better))
    monkeypatch.setattr(gateway, "complete", scripted)

    result = loop.run([{"role": "user", "content": "you cant search the web right"}],
                      budget=for_mode("spark"), request_id="rid-rep2",
                      question="you cant search the web right",
                      tool_names=[])

    assert result.text == better, result.notes


def test_a_registered_tool_that_was_not_offered_is_not_run(monkeypatch, fake_tool):
    """A question about SYRIX is not offered the web; a model that names
    "search" anyway is told so and nothing is searched."""
    ran = []
    real_round = loop.run_round
    monkeypatch.setattr(loop, "run_round",
                        lambda calls, tctx, on_step=None: ran.extend(c["name"] for c in calls)
                        or real_round(calls, tctx, on_step=on_step))
    scripted = _Scripted(
        _completion(tool_calls=[_call("search", '{"queries": ["SYRIX AI"]}')]),
        _completion(text="answered from what I have"),
    )
    monkeypatch.setattr(gateway, "complete", scripted)

    result = loop.run([{"role": "user", "content": "q"}], budget=for_mode("spark"),
                      request_id="rid-offered", tool_names=["probe_tool"])

    assert ran == []
    observation = [m for m in scripted.calls[1]["messages"] if m.get("role") == "tool"][0]
    assert "not available for this question" in observation["content"]
    assert result.text == "answered from what I have"
