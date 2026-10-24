"""The wire: one streaming chat completion against an OpenAI-compatible API.

``post_stream`` is the only function in v3 that opens a model connection,
which is what lets the tests replace the network with a script. ``Attempt``
runs one lane's request on its own thread and reports to the ``Race`` it
belongs to; the first attempt to produce answer text claims the race.
"""
from __future__ import annotations

import json
import queue
import re
import threading
import time
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import requests

from . import ledger
from .catalogue import Lane, LaneError, Result
import syrix_obs


_DURATION = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")


def parse_duration(text: str) -> float:
    """Groq writes waits as ``2m59.56s``, ``7.66s`` or ``240ms``."""
    total = 0.0
    for number, unit in _DURATION.findall(str(text or "")):
        value = float(number)
        total += {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}[unit] * value
    return total


_TRY_AGAIN = re.compile(r"try again in ((?:\d+(?:\.\d+)?(?:ms|h|m|s))+)", re.I)


def error_from(status: int, body: str, headers: Dict[str, str], with_tools: bool) -> LaneError:
    low = body.lower()
    retry = 0.0
    if headers.get("retry-after"):
        try:
            retry = float(headers["retry-after"])
        except ValueError:
            retry = parse_duration(headers["retry-after"])
    match = _TRY_AGAIN.search(body)
    if match:
        retry = max(retry, parse_duration(match.group(1)))
    too_large = status == 413 or "request too large" in low or "reduce your message" in low
    tools_broken = with_tools and status in (400, 500) and "tool" in low
    if with_tools and status == 500 and not too_large:
        # Measured: nemotron-3-super answers HTTP 500 to any request carrying
        # tools. The lane is healthy without them.
        tools_broken = True
    return LaneError(f"HTTP {status}", status=status, retry_after_s=retry,
                     too_large=too_large, tools_broken=tools_broken)


def post_stream(lane: Lane, body: Dict[str, Any], *, cancel: threading.Event,
                 holder: Dict[str, Any]) -> Tuple[Dict[str, str], Iterator[Dict[str, Any]]]:
    """Open a streaming completion. Returns headers and an iterator of chunks.

    Replaced wholesale in tests; nothing else in this module touches the
    network.
    """
    headers = {"Content-Type": "application/json"}
    key = ledger.api_key(lane)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    headers.update(lane.extra_headers)
    resp = requests.post(
        f"{lane.base_url}/chat/completions", headers=headers, json=body,
        stream=True, timeout=(8.0, lane.stall_s),
    )
    holder["response"] = resp
    resp_headers = {k.lower(): v for k, v in resp.headers.items()}
    if resp.status_code != 200:
        text = ""
        try:
            text = resp.text[:600]
        except Exception as exc:
            syrix_obs.report("v3.lanes.transport.post_stream", exc, level="debug")
        resp.close()
        raise error_from(resp.status_code, text, resp_headers, bool(body.get("tools")))

    def chunks() -> Iterator[Dict[str, Any]]:
        try:
            for raw in resp.iter_lines():
                if cancel.is_set():
                    return
                if not raw or not raw.startswith(b"data:"):
                    continue
                data = raw[5:].strip()
                if data == b"[DONE]":
                    return
                try:
                    yield json.loads(data)
                except ValueError:
                    continue
        finally:
            resp.close()

    return resp_headers, chunks()


_THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.S | re.I)


def strip_thinking(text: str) -> str:
    """Remove reasoning a model leaked into its answer text."""
    original = text or ""
    out = _THINK_BLOCK.sub("", original)
    if "</think>" in out.lower():
        out = out[out.lower().rfind("</think>") + len("</think>"):]
    return out.lstrip() if out != original else out


def build_body(lane: Lane, role: str, messages: List[Dict[str, Any]], *,
          tools: Optional[List[Dict[str, Any]]], tool_choice: str,
          max_tokens: int, temperature: float, reasoning: str) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "model": lane.model,
        "messages": messages,
        "max_tokens": int(max_tokens),
        "temperature": float(temperature),
        "stream": True,
    }
    thinking = dict(lane.thinking.get(role) or {})
    if reasoning and "reasoning_effort" in thinking:
        thinking["reasoning_effort"] = reasoning
    if reasoning in ("medium", "high") and lane.thinks_on_request:
        thinking["chat_template_kwargs"] = {"enable_thinking": True}
    body.update(thinking)
    if lane.usage_in_stream:
        body["stream_options"] = {"include_usage": True}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = tool_choice or "auto"
    return body


class Race:
    """Shared state for lanes answering the same request."""

    def __init__(self, on_delta: Optional[Callable[[str], None]]):
        self.lock = threading.Lock()
        self.winner = ""
        self.on_delta = on_delta
        self.events: "queue.Queue[Tuple[str, str, Any]]" = queue.Queue()

    def claim(self, key: str) -> bool:
        with self.lock:
            if not self.winner:
                self.winner = key
                self.events.put(("first", key, None))
            return self.winner == key


class Attempt:
    def __init__(self, lane: Lane, body: Dict[str, Any], race: Race, cost: int):
        self.lane = lane
        self.body = body
        self.race = race
        self.cost = cost
        self.cancel = threading.Event()
        self.holder: Dict[str, Any] = {}
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name=f"v3-lane-{lane.key}")
        self.started = time.monotonic()
        # What the lane has written so far, kept where the caller can read it
        # if the lane fails part-way through.
        self.text_parts: List[str] = []

    def partial(self) -> str:
        return strip_thinking("".join(self.text_parts))

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.cancel.set()
        resp = self.holder.get("response")
        if resp is not None:
            try:
                resp.close()
            except Exception as exc:
                syrix_obs.report("v3.lanes.transport.stop", exc, level="debug")

    def _run(self) -> None:
        lane, race = self.lane, self.race
        result = Result(lane=lane.key, provider=lane.provider, model=lane.model,
                        tools_used=bool(self.body.get("tools")))
        calls: Dict[int, Dict[str, Any]] = {}
        text_parts = self.text_parts
        claimed = False
        try:
            headers, chunks = post_stream(lane, self.body, cancel=self.cancel,
                                           holder=self.holder)
            for chunk in chunks:
                if self.cancel.is_set():
                    return
                if isinstance(chunk.get("error"), dict):
                    # NVIDIA reports overload as HTTP 200 followed by an error
                    # object in the stream. Read as an empty reply, it carried
                    # no cooldown and the overloaded lane was asked again at
                    # once, turn after turn.
                    err = chunk["error"]
                    try:
                        code = int(err.get("code") or 0)
                    except (TypeError, ValueError):
                        code = 0
                    raise LaneError(f"HTTP {code or 'error'} in stream: "
                                    f"{str(err.get('message') or '')[:80]}",
                                    status=code or 503)
                usage = chunk.get("usage") or (chunk.get("x_groq") or {}).get("usage")
                if isinstance(usage, dict):
                    result.prompt_tokens = int(usage.get("prompt_tokens") or 0)
                    result.completion_tokens = int(usage.get("completion_tokens") or 0)
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or choice.get("message") or {}
                    if choice.get("finish_reason"):
                        result.finish_reason = str(choice["finish_reason"])
                    for call in delta.get("tool_calls") or []:
                        idx = int(call.get("index") or 0)
                        slot = calls.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                        if call.get("id"):
                            slot["id"] = call["id"]
                        fn = call.get("function") or {}
                        if fn.get("name"):
                            slot["name"] += fn["name"]
                        if fn.get("arguments"):
                            args = fn["arguments"]
                            slot["arguments"] += args if isinstance(args, str) else json.dumps(args)
                        if not claimed:
                            claimed = race.claim(lane.key)
                            if not claimed:
                                return
                            result.first_token_ms = (time.monotonic() - self.started) * 1000.0
                    piece = delta.get("content")
                    if isinstance(piece, str) and piece:
                        if not claimed and piece.strip():
                            claimed = race.claim(lane.key)
                            if not claimed:
                                return
                            result.first_token_ms = (time.monotonic() - self.started) * 1000.0
                        text_parts.append(piece)
                        if claimed and race.on_delta is not None:
                            try:
                                race.on_delta(piece)
                            except Exception as exc:
                                syrix_obs.report("v3.lanes.transport._run", exc, level="debug")
            if self.cancel.is_set():
                return
            result.raw_text = strip_thinking("".join(text_parts))
            result.text = result.raw_text.strip()
            result.tool_calls = [
                {"id": slot["id"] or f"call_{i}", "name": slot["name"],
                 "arguments": slot["arguments"] or "{}"}
                for i, slot in sorted(calls.items()) if slot["name"]
            ]
            result.latency_ms = round((time.monotonic() - self.started) * 1000.0, 1)
            if not result.text and not result.tool_calls:
                reason = ("the reply allowance went on thinking"
                          if result.finish_reason == "length" else "empty reply")
                raise LaneError(reason)
            race.events.put(("done", lane.key, (result, headers)))
        except LaneError as err:
            race.events.put(("error", lane.key, err))
        except requests.exceptions.Timeout:
            race.events.put(("error", lane.key, LaneError("stalled")))
        except requests.exceptions.RequestException as exc:
            if self.cancel.is_set():
                return
            race.events.put(("error", lane.key, LaneError(f"network: {type(exc).__name__}")))
        except Exception as exc:  # pragma: no cover - defensive
            if self.cancel.is_set():
                return
            race.events.put(("error", lane.key, LaneError(f"{type(exc).__name__}: {exc}")))
