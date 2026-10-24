"""The answer reaches the chat as it is written, once, and nothing else does."""
from __future__ import annotations

from syrix_v3 import check, gateway, loop, wire
from syrix_v3.budget import for_mode


class _Lane:
    """Scripted rounds; each streams its text through ``on_delta`` in pieces."""

    def __init__(self, *rounds):
        self.rounds = list(rounds)

    def __call__(self, messages, **kwargs):
        text, calls = self.rounds.pop(0) if self.rounds else ("fallback answer", [])
        sink = kwargs.get("on_delta")
        if sink is not None:
            for i in range(0, len(text), 7):
                sink(text[i:i + 7])
        return gateway.Completion(text=text, tool_calls=calls, provider="fake")


def _capture(monkeypatch):
    shown = []
    monkeypatch.setattr(wire, "stream", lambda text, request_id: shown.append(text))
    return shown


def test_the_answer_streams_as_it_is_written(monkeypatch):
    shown = _capture(monkeypatch)
    answer = "Canberra is the capital of Australia, chosen in 1908 as a compromise."
    monkeypatch.setattr(gateway, "complete", _Lane((answer, [])))
    out = loop.run([{"role": "user", "content": "capital of australia"}],
                   budget=for_mode("spark"), request_id="r1", question="capital of australia")
    assert out.text == answer
    assert "".join(shown) == answer


def test_a_tool_call_preamble_is_never_shown(monkeypatch):
    shown = _capture(monkeypatch)
    call = {"id": "c1", "name": "no_such_tool", "arguments": "{}"}
    answer = "The answer, written after looking, is long enough to stream out."
    monkeypatch.setattr(gateway, "complete", _Lane(("Let me check.", [call]), (answer, [])))
    loop.run([{"role": "user", "content": "q"}], budget=for_mode("spark"),
             request_id="r2", question="q")
    assert "Let me check." not in "".join(shown)
    assert "".join(shown) == answer


def test_a_repair_does_not_stream_a_second_copy(monkeypatch):
    shown = _capture(monkeypatch)
    first = "First draft of the answer, which the check will send back for a fix."
    second = "Second draft of the answer, which replaces the first when the turn ends."
    monkeypatch.setattr(gateway, "complete", _Lane((first, []), (second, [])))
    verdicts = iter([check.Verdict(problems=["x"]), check.Verdict()])
    monkeypatch.setattr(check, "run", lambda *a, **k: next(verdicts))
    monkeypatch.setattr(check, "repair_message", lambda v: {"role": "system", "content": "fix"})
    out = loop.run([{"role": "user", "content": "q"}], budget=for_mode("spark"),
                   request_id="r3", question="q")
    assert out.text == second
    assert "".join(shown) == first
