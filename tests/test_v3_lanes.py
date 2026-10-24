"""v3's own model transport, pinned against a scripted network.

``lanes.transport.post_stream`` is the only function that touches the network, so each
test replaces it with a script of chunks and delays per lane and states one
rule the transport must keep.
"""
from __future__ import annotations

import threading
import time

import pytest

from syrix_v3 import gateway, lanes


def _chunk(text="", *, finish=None, tool=None, usage=None):
    delta = {}
    if text:
        delta["content"] = text
    if tool:
        delta["tool_calls"] = [tool]
    choice = {"index": 0, "delta": delta}
    if finish:
        choice["finish_reason"] = finish
    out = {"choices": [choice]}
    if usage:
        out["usage"] = usage
    return out


class _Net:
    """Per-lane scripts: a list of (delay_s, chunk) or an exception to raise."""

    def __init__(self, scripts, headers=None):
        self.scripts = scripts
        self.headers = headers or {}
        self.bodies = []
        self.cancelled = set()
        self.lock = threading.Lock()

    def __call__(self, lane, body, *, cancel, holder):
        with self.lock:
            self.bodies.append((lane.key, body))
        script = self.scripts.get(lane.key)
        if script is None:
            raise lanes.LaneError("HTTP 500", status=500)
        if isinstance(script, Exception):
            raise script
        if callable(script):
            script = script(body)

        def gen():
            for delay, chunk in script:
                end = time.monotonic() + delay
                while time.monotonic() < end:
                    if cancel.is_set():
                        with self.lock:
                            self.cancelled.add(lane.key)
                        return
                    time.sleep(0.005)
                if cancel.is_set():
                    with self.lock:
                        self.cancelled.add(lane.key)
                    return
                yield chunk

        return dict(self.headers.get(lane.key, {})), gen()


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    for name in ("GROQ_API_KEY", "NVIDIA_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.setenv(name, "test-key")
    lanes.reset_for_tests()
    monkeypatch.setattr(lanes, "_local_complete", lambda *a, **k: None)
    yield
    lanes.reset_for_tests()


def _answer(text, delay=0.0):
    return [(delay, _chunk(text)), (0.0, _chunk(finish="stop"))]


def test_the_best_lane_answers_and_streams(monkeypatch):
    net = _Net({"groq:gpt-oss-120b": [(0, _chunk("Hello ")), (0, _chunk("there.")),
                                      (0, _chunk(finish="stop"))]})
    monkeypatch.setattr(lanes.transport, "post_stream", net)
    seen = []
    out = lanes.complete([{"role": "user", "content": "hi"}], role=lanes.ANSWER,
                         on_delta=seen.append)
    assert out.text == "Hello there."
    assert out.lane == "groq:gpt-oss-120b"
    assert "".join(seen) == "Hello there."


def test_reasoning_is_set_low_for_answers_and_thinking_off_for_nemotron(monkeypatch):
    net = _Net({"groq:gpt-oss-120b": lanes.LaneError("HTTP 503", status=503),
                "nvidia:nemotron-3-super": _answer("ok")})
    monkeypatch.setattr(lanes.transport, "post_stream", net)
    lanes.complete([{"role": "user", "content": "hi"}], role=lanes.ANSWER)
    bodies = dict(net.bodies)
    assert bodies["groq:gpt-oss-120b"]["reasoning_effort"] == "low"
    assert bodies["nvidia:nemotron-3-super"]["chat_template_kwargs"] == {"enable_thinking": False}
    # NVIDIA mishandles stream_options; it must not be sent there.
    assert "stream_options" not in bodies["nvidia:nemotron-3-super"]


def test_a_slow_lane_is_raced_and_the_faster_one_wins(monkeypatch):
    net = _Net({
        "groq:gpt-oss-120b": _answer("slow answer", delay=5.0),
        "nvidia:nemotron-3-super": _answer("fast answer", delay=0.05),
    })
    monkeypatch.setattr(lanes.transport, "post_stream", net)
    monkeypatch.setitem(lanes.CATALOGUE, "groq:gpt-oss-120b",
                        lanes.CATALOGUE["groq:gpt-oss-120b"].__class__(
                            **{**lanes.CATALOGUE["groq:gpt-oss-120b"].__dict__, "hedge_s": 0.2}))
    started = time.monotonic()
    out = lanes.complete([{"role": "user", "content": "hi"}], role=lanes.ANSWER)
    assert out.text == "fast answer"
    assert time.monotonic() - started < 2.0
    time.sleep(0.1)
    assert "groq:gpt-oss-120b" in net.cancelled


def test_an_error_hands_over_at_once_without_waiting_for_the_hedge(monkeypatch):
    net = _Net({
        "groq:gpt-oss-120b": lanes.LaneError("HTTP 500", status=500),
        "nvidia:nemotron-3-super": _answer("second lane"),
    })
    monkeypatch.setattr(lanes.transport, "post_stream", net)
    started = time.monotonic()
    out = lanes.complete([{"role": "user", "content": "hi"}], role=lanes.ANSWER)
    assert out.text == "second lane"
    assert time.monotonic() - started < 1.0
    assert any("groq:gpt-oss-120b" in r for r in out.rotations)


def test_a_key_without_credit_is_not_asked_again(monkeypatch):
    net = _Net({
        "groq:gpt-oss-120b": lanes.LaneError("HTTP 402", status=402),
        "nvidia:nemotron-3-super": _answer("ok"),
    })
    monkeypatch.setattr(lanes.transport, "post_stream", net)
    lanes.complete([{"role": "user", "content": "hi"}], role=lanes.ANSWER)
    net.bodies.clear()
    lanes.complete([{"role": "user", "content": "hi"}], role=lanes.ANSWER)
    assert [k for k, _ in net.bodies] == ["nvidia:nemotron-3-super"]


def test_the_providers_own_minute_figure_decides_admission(monkeypatch):
    lane = lanes.CATALOGUE["groq:gpt-oss-120b"]
    net = _Net({"groq:gpt-oss-120b": _answer("ok")},
               headers={"groq:gpt-oss-120b": {"x-ratelimit-remaining-tokens": "500"}})
    monkeypatch.setattr(lanes.transport, "post_stream", net)
    lanes.complete([{"role": "user", "content": "hi"}], role=lanes.ANSWER, max_tokens=1000)
    possible, wait, _ = lanes.admission(lane, 3000)
    assert possible
    # 2,500 tokens short at 8,000 a minute is roughly 19 seconds.
    assert 15.0 < wait < 20.0


def test_a_rate_limited_lane_is_skipped_until_it_refills(monkeypatch):
    net = _Net({
        "groq:gpt-oss-120b": lanes.LaneError("HTTP 429", status=429, retry_after_s=30.0),
        "nvidia:nemotron-3-super": _answer("ok"),
    })
    monkeypatch.setattr(lanes.transport, "post_stream", net)
    lanes.complete([{"role": "user", "content": "hi"}], role=lanes.ANSWER)
    net.bodies.clear()
    lanes.complete([{"role": "user", "content": "hi"}], role=lanes.ANSWER)
    assert [k for k, _ in net.bodies] == ["nvidia:nemotron-3-super"]


def test_a_request_too_large_for_one_lane_does_not_cool_it_down(monkeypatch):
    lane = lanes.CATALOGUE["groq:gpt-oss-120b"]
    err = lanes.error_from(429, "Request too large for model", {}, False)
    assert err.too_large
    lanes.ledger.note_failure(lane, err)
    possible, wait, _ = lanes.admission(lane, 1000)
    assert possible and wait == 0.0


def test_a_lane_that_breaks_on_tools_answers_without_them(monkeypatch):
    calls = []

    def super_script(body):
        calls.append(bool(body.get("tools")))
        if body.get("tools"):
            raise lanes.error_from(500, "Internal server error", {}, True)
        return _answer("toolless answer")

    net = _Net({"nvidia:nemotron-3-super": super_script})
    monkeypatch.setattr(lanes.transport, "post_stream", net)
    tools = [{"type": "function", "function": {"name": "web_search", "parameters": {}}}]
    monkeypatch.setitem(lanes.CATALOGUE, "nvidia:nemotron-3-super",
                        lanes.CATALOGUE["nvidia:nemotron-3-super"].__class__(
                            **{**lanes.CATALOGUE["nvidia:nemotron-3-super"].__dict__, "tools": True}))
    out = lanes.complete([{"role": "user", "content": "hi"}], role=lanes.ANSWER,
                         tools=tools, only=["nvidia:nemotron-3-super"])
    assert out.text == "toolless answer"
    assert calls == [True, False]


def test_tool_calls_stream_in_pieces_and_are_assembled(monkeypatch):
    script = [
        (0, _chunk(tool={"index": 0, "id": "c1", "function": {"name": "web_", "arguments": ""}})),
        (0, _chunk(tool={"index": 0, "function": {"name": "search", "arguments": '{"query":'}})),
        (0, _chunk(tool={"index": 0, "function": {"arguments": '"nvda"}'}})),
        (0, _chunk(finish="tool_calls")),
    ]
    monkeypatch.setattr(lanes.transport, "post_stream", _Net({"groq:gpt-oss-120b": script}))
    out = lanes.complete([{"role": "user", "content": "hi"}], role=lanes.ANSWER,
                         tools=[{"type": "function", "function": {"name": "web_search"}}])
    assert out.tool_calls == [{"id": "c1", "name": "web_search", "arguments": '{"query":"nvda"}'}]


def test_a_reply_spent_entirely_on_thinking_moves_to_the_next_lane(monkeypatch):
    net = _Net({
        "groq:gpt-oss-120b": [(0, _chunk(finish="length"))],
        "nvidia:nemotron-3-super": _answer("a real answer"),
    })
    monkeypatch.setattr(lanes.transport, "post_stream", net)
    out = lanes.complete([{"role": "user", "content": "who made you"}], role=lanes.ANSWER)
    assert out.text == "a real answer"


def test_an_answer_cut_off_mid_sentence_is_continued(monkeypatch):
    replies = iter([
        [(0, _chunk("The first half of the answer and")), (0, _chunk(finish="length"))],
        [(0, _chunk(" the second half.")), (0, _chunk(finish="stop"))],
    ])
    net = _Net({"groq:gpt-oss-120b": lambda body: next(replies)})
    monkeypatch.setattr(lanes.transport, "post_stream", net)
    out = lanes.complete([{"role": "user", "content": "explain"}], role=lanes.ANSWER)
    assert out.text == "The first half of the answer and the second half."


def test_nothing_available_raises_rather_than_returning_blank(monkeypatch):
    monkeypatch.setattr(lanes.transport, "post_stream", _Net({}))
    with pytest.raises(lanes.NoLaneAvailable):
        lanes.complete([{"role": "user", "content": "hi"}], role=lanes.ANSWER,
                       timeout_s=3.0)


def test_the_local_model_is_the_floor(monkeypatch):
    monkeypatch.setattr(lanes.transport, "post_stream", _Net({}))
    monkeypatch.setattr(lanes, "_local_complete",
                        lambda *a, **k: lanes.Result(text="local answer", lane="local",
                                                     provider="local"))
    out = lanes.complete([{"role": "user", "content": "hi"}], role=lanes.ANSWER)
    assert out.text == "local answer"


def test_leaked_reasoning_is_removed_from_the_answer():
    assert lanes.strip_thinking("<think>plan it</think>\nThe answer.") == "The answer."
    assert lanes.strip_thinking("stray thoughts</think>The answer.") == "The answer."
    assert lanes.strip_thinking("Plain answer.") == "Plain answer."


@pytest.mark.parametrize("text,seconds", [
    ("7.66s", 7.66), ("2m59.56s", 179.56), ("240ms", 0.24), ("1h2m3s", 3723.0),
])
def test_provider_durations_parse(text, seconds):
    assert lanes.parse_duration(text) == pytest.approx(seconds)


def test_fitting_never_keeps_a_tool_result_without_its_call():
    msgs = [
        {"role": "system", "content": "s" * 100},
        {"role": "user", "content": "u" * 3000},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "tool_call_id": "1", "content": "t" * 3000},
        {"role": "user", "content": "last"},
    ]
    out = lanes.fit(msgs, 3200)
    roles = [m["role"] for m in out]
    assert roles[0] == "system" and roles[-1] == "user"
    assert roles[1] != "tool"


def test_the_gateway_speaks_through_the_lanes(monkeypatch):
    monkeypatch.setattr(lanes.transport, "post_stream",
                        _Net({"groq:gpt-oss-120b": _answer("via lanes")}))
    got = gateway.complete([{"role": "user", "content": "hi"}], tier="T2_BALANCED")
    assert got.text == "via lanes"
    assert got.provider == "groq"


def test_a_lane_seconds_from_refilling_is_waited_for_rather_than_giving_up(monkeypatch):
    net = _Net({"groq:gpt-oss-120b": _answer("worth the wait")})
    monkeypatch.setattr(lanes.transport, "post_stream", net)
    msgs = [{"role": "user", "content": "hi"}]
    cost = lanes.estimate_tokens(msgs) + 1600
    for key in ("groq:gpt-oss-120b", "groq:gpt-oss-20b"):
        live = lanes.ledger.state_of(key)
        live.tpm_remaining = cost - 260          # about two seconds short
        live.tpm_seen_at = time.monotonic()
    started = time.monotonic()
    out = lanes.complete(msgs, role=lanes.ANSWER, timeout_s=20)
    assert out.text == "worth the wait"
    assert 1.5 < time.monotonic() - started < 6.0
    assert any("waited" in r for r in out.rotations)


def test_an_empty_reply_with_tools_is_asked_again_without_them(monkeypatch):
    seen = []

    def ultra(body):
        seen.append(bool(body.get("tools")))
        return [(0, _chunk("\n")), (0, _chunk(finish="stop"))] if body.get("tools") else _answer("fine")

    monkeypatch.setattr(lanes.transport, "post_stream", _Net({"nvidia:nemotron-3-ultra": ultra}))
    out = lanes.complete([{"role": "user", "content": "q"}], role=lanes.ANSWER,
                         tools=[{"type": "function", "function": {"name": "calculate"}}],
                         only=["nvidia:nemotron-3-ultra"])
    assert out.text == "fine" and seen == [True, False]
    # A model saying nothing once is not a sick lane.
    possible, wait, _ = lanes.admission(lanes.CATALOGUE["nvidia:nemotron-3-ultra"], 100)
    assert possible and wait == 0.0


def test_the_local_floor_returns_its_answer(monkeypatch):
    monkeypatch.undo()
    for name in ("GROQ_API_KEY", "NVIDIA_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.setenv(name, "test-key")
    from syrix_v3 import local_model
    monkeypatch.setattr(local_model, "ensure_running", lambda **k: True)
    monkeypatch.setattr(local_model, "note_use", lambda: None)
    monkeypatch.setattr(lanes.transport, "post_stream", _Net({"local": _answer("from the floor")}))
    got = lanes._local_complete([{"role": "user", "content": "q"}], max_tokens=50,
                                temperature=0.2, on_delta=None)
    assert got is not None and got.text == "from the floor"


def test_an_error_inside_a_stream_is_a_failure_not_an_empty_reply(monkeypatch):
    overloaded = [(0, {"error": {"message": "Service temporarily overloaded", "code": 503}})]
    net = _Net({"groq:gpt-oss-120b": overloaded, "nvidia:nemotron-3-super": _answer("ok")})
    monkeypatch.setattr(lanes.transport, "post_stream", net)
    out = lanes.complete([{"role": "user", "content": "hi"}], role=lanes.ANSWER)
    assert out.text == "ok"
    assert any("overloaded" in r for r in out.rotations)
    possible, wait, _ = lanes.admission(lanes.CATALOGUE["groq:gpt-oss-120b"], 100)
    assert wait > 0          # an overloaded lane is given time to recover


def test_a_briefly_overloaded_lane_is_asked_again_rather_than_benched(monkeypatch):
    """NVIDIA answers "Service temporarily overloaded" for a moment at a time.
    Counted as a fault it cooled the lane for 20s and then 40s, and turns in
    between had nothing left; seconds later the same lane answered in 1.5s."""
    calls = []

    def flaky(body):
        calls.append(time.monotonic())
        if len(calls) == 1:
            raise lanes.LaneError("HTTP 503 in stream: Service temporarily overloaded", status=503)
        return _answer("second try")

    net = _Net({"nvidia:nemotron-3-super": flaky})
    monkeypatch.setattr(lanes.transport, "post_stream", net)
    out = lanes.complete([{"role": "user", "content": "hi"}], role=lanes.ANSWER,
                         only=["nvidia:nemotron-3-super"], timeout_s=10)
    assert out.text == "second try"
    assert len(calls) == 2 and calls[1] - calls[0] < 4.0
    assert lanes.ledger.state_of("nvidia:nemotron-3-super").failures == 0


_WRITTEN = ("NVDA is down today at $225.51, off $3.36 from yesterday's close. It opened at "
            "$228, touched $228.95 and slid to $224.02 on volume of about 85.8M shares. Two "
            "things pushed it down: a director's share sale took it under $224, and it failed "
            "at $230 again as the growth outlook met rising risks. The counterweight")


def test_an_answer_a_lane_dropped_part_way_is_finished_not_replaced(monkeypatch):
    """Measured: a lane streamed most of a cited answer, then NVIDIA sent
    "overloaded" in the stream; nothing else was free and the local model's
    fresh answer replaced the one on screen."""
    def dies_mid_answer(body):
        if body["messages"][-1]["content"].startswith("Continue exactly"):
            return _answer(" is that analysts upgraded it today.")
        return [(0.0, _chunk(_WRITTEN)),
                (0.0, {"error": {"code": 503, "message": "Service temporarily overloaded"}})]

    local_called = []
    monkeypatch.setattr(lanes, "_local_complete", lambda *a, **k: local_called.append(1))
    net = _Net({"nvidia:nemotron-3-super": dies_mid_answer})
    monkeypatch.setattr(lanes.transport, "post_stream", net)
    out = lanes.complete([{"role": "user", "content": "how is nvda doing"}], role=lanes.ANSWER,
                         only=["nvidia:nemotron-3-super"], timeout_s=10)
    assert out.text.startswith("NVDA is down today at $225.51")
    assert out.text.endswith("analysts upgraded it today.")
    assert not local_called


def test_an_unfinished_answer_ends_at_its_last_sentence():
    assert lanes._last_sentence(_WRITTEN).endswith("rising risks.")
    assert lanes._last_sentence("Done.") == "Done."
