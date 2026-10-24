"""The model lanes v3 thinks on, and the one function that asks them.

v3's own transport. It replaces ``syrix_core_v2.brain_pool`` for every v3
call, and it is built around three measurements the pool never made:

**Providers publish their real limits on every response.** Groq returns
``x-ratelimit-remaining-tokens`` and ``x-ratelimit-reset-tokens`` with each
completion, and charges ``max_tokens`` against the minute when the request is
admitted. The pool priced requests from a static table and a character
estimate instead, refused lanes that had room, and admitted lanes that did
not — measured on 2026-09-20, the strong lane was skipped on an estimated
per-minute budget 32 times in one evening while the local 7B answered 115
requests. Here admission reads the provider's own numbers, refilled at the
provider's own rate.

**A slow lane is not a failed lane, but it must not hold the turn.** Lanes
are raced, not rotated: the best admissible lane starts first, and if it has
not produced a token by its hedge time the next one starts beside it. The
first lane to produce answer text wins and the others are cancelled. A lane
that errors hands over immediately. The local model is reached only when no
cloud lane can answer at all.

**Thinking is a dial, and the default setting was wrong.** gpt-oss runs at
``reasoning_effort: medium`` unless told otherwise, and the pool never told
it. At ``low`` the same lane answers a 2,700-token evidence prompt in 1.5s.
Nemotron Super with thinking switched off answers the same prompt in 1.3s,
with no per-minute token cap at all. Both are set per role below.

Streaming is native. ``on_delta`` receives answer text as it arrives, from
the winning lane only, so the interface can show the answer being written.
"""
from __future__ import annotations

import json
import os
import queue
import re
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from .. import config, state
from . import ledger, transport
from .catalogue import (ANSWER, CATALOGUE, DEEP, FAST, LOCAL_KEY, ORDER, ROLES,
                        TIER_ROLE, Lane, LaneError, NoLaneAvailable, Result,
                        _DEFAULT_TIMEOUT_S, _MAX_WAIT_S)
from .ledger import admission, estimate_tokens
from .transport import error_from, parse_duration, strip_thinking
import syrix_obs

__all__ = [
    "ANSWER", "CATALOGUE", "DEEP", "FAST", "ORDER", "ROLES", "TIER_ROLE",
    "Lane", "LaneError", "NoLaneAvailable", "Result", "admission", "complete",
    "error_from", "estimate_tokens", "fit", "parse_duration", "status",
    "strip_thinking",
]

_LOG_FILE = "lanes_log.jsonl"
_LOG_MAX_BYTES = 2_000_000
_log_lock = threading.Lock()


def _log(entry: Dict[str, Any]) -> None:
    """One line per call: which lanes were tried, what each did, how long.

    The only record of why a turn landed where it did. Without it a slow or
    failed turn can be diagnosed only by re-running it, and a re-run lands on
    a different minute's allowances.
    """
    try:
        path = state.path_for(_LOG_FILE, "runtime")
        with _log_lock:
            if os.path.exists(path) and os.path.getsize(path) > _LOG_MAX_BYTES:
                os.replace(path, path + ".1")
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        syrix_obs.report("v3.lanes._log", exc, level="debug")


_NO_TOOLS_NOTE = (
    "No tools can be called in this reply. Answer directly from what you already "
    "have, and work any arithmetic through step by step in the answer itself."
)


def fit(messages: List[Dict[str, Any]], max_chars: int) -> List[Dict[str, Any]]:
    """Trim a request to ``max_chars`` without breaking its structure.

    System messages and the final turn are kept; the oldest turns in between
    go first. A tool result is never kept without the assistant message that
    asked for it — providers reject that pairing outright.
    """
    msgs = list(messages or [])

    def size(m: Dict[str, Any]) -> int:
        content = m.get("content")
        n = len(content) if isinstance(content, str) else len(json.dumps(content or ""))
        if m.get("tool_calls"):
            n += len(json.dumps(m["tool_calls"]))
        return n

    if not max_chars or sum(size(m) for m in msgs) <= max_chars:
        return msgs
    system = [m for m in msgs if m.get("role") == "system"]
    rest = [m for m in msgs if m.get("role") != "system"]
    budget = max_chars - sum(size(m) for m in system)
    kept: List[Dict[str, Any]] = []
    for m in reversed(rest):
        cost = size(m)
        if kept and budget - cost < 0:
            break
        budget -= cost
        kept.append(m)
    kept.reverse()
    while kept and kept[0].get("role") == "tool":
        kept.pop(0)
    return system + kept if (system or kept) else msgs[-2:]


def _private_mode() -> bool:
    """Whether the host has Private mode on. Read from the loaded module so
    v3 never imports host code, and is False wherever the host is absent."""
    mod = sys.modules.get("syrix_private")
    try:
        return bool(mod is not None and mod.active())
    except Exception:
        return False


def _candidates(role: str, *, tools: bool, only: Optional[List[str]] = None) -> List[Lane]:
    order = list(only or ORDER.get(role) or ORDER[ANSWER])
    lanes = [CATALOGUE[k] for k in order if k in CATALOGUE]
    return [lane for lane in lanes if ledger.configured(lane)]


def complete(
    messages: List[Dict[str, Any]],
    *,
    role: str = ANSWER,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: str = "auto",
    max_tokens: int = 1600,
    temperature: float = 0.5,
    reasoning: str = "",
    max_chars: int = 0,
    timeout_s: Optional[float] = None,
    on_delta: Optional[Callable[[str], None]] = None,
    allow_local: bool = True,
    only: Optional[List[str]] = None,
) -> Result:
    """Ask the best available lane, racing the next one if it is slow.

    Raises :class:`NoLaneAvailable` when nothing — cloud or local — answered.
    """
    role = role if role in ROLES else ANSWER
    started = time.monotonic()
    budget_s = float(timeout_s) if timeout_s else _DEFAULT_TIMEOUT_S[role]
    deadline = started + budget_s
    msgs = fit(messages, max_chars) if max_chars else list(messages)
    prompt_cost = estimate_tokens(msgs)
    rotations: List[str] = []

    pending = _candidates(role, tools=bool(tools), only=only)
    if _private_mode():
        # Private mode: no cloud lane is offered anything; the turn goes
        # straight to the local model below. Off, this line changes nothing.
        pending = []
        rotations.append("private mode: cloud lanes off")
    race = transport.Race(on_delta)
    running: Dict[str, transport.Attempt] = {}
    next_hedge_at: Optional[float] = None

    # Lanes passed over because their minute was refilling. They are the
    # last resort before giving up: a lane four seconds from ready is an
    # answer, not an exhausted pool.
    deferred: Dict[str, float] = {}
    # Lanes that returned nothing with tools offered; asked once more without.
    toolless: set = set()
    # Lanes that were briefly overloaded and have been given their second try.
    retried: set = set()

    def launch() -> bool:
        nonlocal next_hedge_at
        waited = False
        while pending:
            lane = pending[0]
            live = ledger.state_of(lane.key)
            use_tools = tools if (tools and lane.tools and not live.tools_broken
                                  and lane.key not in toolless) else None
            cost = prompt_cost + int(max_tokens)
            possible, wait, why = admission(lane, cost)
            if not possible:
                pending.pop(0)
                rotations.append(f"{lane.key}: {why}")
                continue
            if wait > 0:
                # Worth waiting for only when it is the best lane still in
                # play, nothing else is running, and the wait is short.
                if (not waited and not running and wait <= _MAX_WAIT_S[role]
                        and time.monotonic() + wait < deadline - 2.0):
                    waited = True
                    time.sleep(wait)
                    continue
                pending.pop(0)
                deferred[lane.key] = time.monotonic() + wait
                rotations.append(f"{lane.key}: {why}, {wait:.0f}s to refill")
                continue
            pending.pop(0)
            request = msgs
            if tools and not use_tools:
                # Told about tools it cannot call, a lane writes the call as
                # text — measured, a digit-sum question answered with the
                # calculator's argument string and nothing else.
                request = msgs + [{"role": "system", "content": _NO_TOOLS_NOTE}]
            body = transport.build_body(lane, role, request, tools=use_tools, tool_choice=tool_choice,
                         max_tokens=max_tokens, temperature=temperature,
                         reasoning=reasoning)
            ledger.note_admitted(lane, cost)
            attempt = transport.Attempt(lane, body, race, cost)
            running[lane.key] = attempt
            attempt.start()
            next_hedge_at = time.monotonic() + lane.hedge_s
            return True
        next_hedge_at = None
        return False

    def wait_for_deferred() -> bool:
        """Nothing else can answer: wait for the lane that refills soonest."""
        while deferred:
            key = min(deferred, key=deferred.get)
            ready_at = deferred.pop(key)
            if ready_at > deadline - 3.0:
                continue
            time.sleep(max(0.0, ready_at - time.monotonic()) + 0.2)
            rotations.append(f"{key}: waited for its minute to refill")
            pending.insert(0, CATALOGUE[key])
            if launch():
                return True
        return False

    def stop_all(except_key: str = "") -> None:
        for key, attempt in list(running.items()):
            if key != except_key:
                attempt.stop()

    result: Optional[Result] = None
    if not launch():
        wait_for_deferred()
    while running:
        now = time.monotonic()
        if now >= deadline:
            rotations.append("out of time")
            break
        wait = deadline - now
        if not race.winner and next_hedge_at is not None:
            wait = min(wait, max(0.02, next_hedge_at - now))
        try:
            kind, key, payload = race.events.get(timeout=wait)
        except queue.Empty:
            if not race.winner and next_hedge_at is not None and time.monotonic() >= next_hedge_at:
                launch()
            continue
        if kind == "first":
            # One lane is writing the answer; the rest are only spending the
            # minute's allowance now.
            for other_key, other in list(running.items()):
                if other_key != key:
                    other.stop()
                    running.pop(other_key, None)
                    rotations.append(f"{other_key}: slower")
            continue
        attempt = running.pop(key, None)
        lane = CATALOGUE.get(key)
        if kind == "done":
            found, headers = payload
            if lane is not None and attempt is not None:
                ledger.note_success(lane, found, headers, attempt.cost)
            if race.winner and race.winner != key:
                continue
            result = found
            break
        err: LaneError = payload
        if lane is not None:
            ledger.note_failure(lane, err)
            if err.tools_broken and attempt is not None and attempt.body.get("tools"):
                # Same lane, same request, no tools: it answers that fine.
                pending.insert(0, lane)
            elif (err.reason == "empty reply" and attempt is not None
                  and attempt.body.get("tools") and lane.key not in toolless):
                toolless.add(lane.key)
                pending.insert(0, lane)
        rotations.append(f"{key}: {err.reason}")
        salvage = attempt.partial() if (attempt is not None and race.winner == key) else ""
        if len(salvage.strip()) >= _SALVAGE_MIN_CHARS and not tools:
            # The lane had already written a good part of the answer, and
            # that part is on screen. It is kept and finished rather than
            # replaced: measured, a lane dropped mid-answer with "overloaded",
            # nothing else was free, and the local model's fresh answer
            # swapped a cited, specific one for a vaguer one in front of the
            # reader.
            result = Result(text=salvage.strip(), raw_text=salvage, lane=key,
                            provider=lane.provider if lane else "", model=lane.model if lane else "",
                            finish_reason="length")
            rotations.append(f"{key}: kept the {len(salvage)} characters it had written")
            break
        if (lane is not None and ledger.overloaded(err) and key not in retried
                and not err.tools_broken):
            # One more try once the moment has passed, after anything else
            # still in line has had its turn.
            retried.add(key)
            deferred[key] = ledger.state_of(key).cooldown_until
        if race.winner == key:
            # It had started answering and then failed. A new race lets the
            # next lane claim the answer.
            race = transport.Race(on_delta)
        if not race.winner:
            if not launch() and not running:
                wait_for_deferred()

    stop_all()
    _log({
        "at": time.strftime("%Y-%m-%d %H:%M:%S"), "role": role,
        "tools": bool(tools), "prompt_tokens_est": prompt_cost,
        "max_tokens": int(max_tokens), "budget_s": round(budget_s, 1),
        "took_s": round(time.monotonic() - started, 2),
        "answered_by": result.lane if result is not None else "",
        "first_token_ms": round(result.first_token_ms) if result is not None else 0,
        "rotations": rotations,
    })
    if result is not None:
        result.rotations = rotations
        if (result.finish_reason == "length" and result.text
                and not result.tool_calls and time.monotonic() < deadline - 1.0):
            result = _continue(result, msgs, role=role, max_tokens=max_tokens,
                               temperature=temperature, reasoning=reasoning,
                               deadline=deadline, on_delta=on_delta)
        if result.finish_reason == "length" and result.text and not result.tool_calls:
            # Still unfinished: end it at its last complete sentence rather
            # than mid-word.
            result.text = _last_sentence(result.text)
        return result

    if allow_local:
        local = _local_complete(msgs, max_tokens=max_tokens, temperature=temperature,
                                on_delta=on_delta)
        if local is not None:
            local.rotations = rotations
            return local
        rotations.append("local: did not answer")
    raise NoLaneAvailable("; ".join(rotations) or "no lane configured")


# Below this, a lane that failed had barely started, and a fresh answer from
# the next lane is the better outcome.
_SALVAGE_MIN_CHARS = 240


def _last_sentence(text: str) -> str:
    """``text`` cut back to its last complete sentence, when it has one."""
    body = str(text or "").rstrip()
    if re.search(r"[.!?][)\]\"'*_]*$", body):
        return body
    ends = [m.end() for m in re.finditer(r"[.!?](?=[\s\n])", body)]
    if ends and ends[-1] >= len(body) * 0.5:
        return body[:ends[-1]].rstrip()
    return body


def _overlap_suffix(existing: str, more: str) -> str:
    """The part of ``more`` that does not repeat the end of ``existing``."""
    probe = existing[-400:]
    for size in range(min(len(probe), len(more)), 20, -1):
        if probe.endswith(more[:size]):
            return more[size:]
    return more


def _continue(first: Result, msgs: List[Dict[str, Any]], *, role: str,
              max_tokens: int, temperature: float, reasoning: str,
              deadline: float, on_delta: Optional[Callable[[str], None]]) -> Result:
    """One continuation for an answer that hit the reply ceiling mid-sentence."""
    follow = list(msgs) + [
        {"role": "assistant", "content": first.text},
        {"role": "user", "content": (
            "Continue exactly where you stopped. Do not repeat anything already "
            "written and do not add a preamble.")},
    ]
    try:
        more = complete(follow, role=role, max_tokens=max_tokens,
                        temperature=temperature, reasoning=reasoning,
                        timeout_s=max(5.0, deadline - time.monotonic()),
                        on_delta=on_delta, allow_local=False)
    except NoLaneAvailable:
        return first
    head = first.raw_text or first.text
    tail = more.raw_text or more.text
    joined = head + _overlap_suffix(head, tail)
    first.raw_text = joined
    first.text = joined.strip()
    first.finish_reason = more.finish_reason
    first.completion_tokens += more.completion_tokens
    first.rotations.append(f"continued on {more.lane}")
    return first


# The local model: the floor that never runs out, started only when needed.
_LOCAL_TIMEOUT_S = 120.0


def _local_lane() -> Lane:
    host = str(config.get("ollama_host", "http://127.0.0.1:11434")).rstrip("/")
    model = str(config.get("ollama_model", "qwen2.5:7b-instruct"))
    return Lane(key=LOCAL_KEY, provider="local", model=model,
                base_url=f"{host}/v1", key_env="", tools=False,
                hedge_s=_LOCAL_TIMEOUT_S, stall_s=_LOCAL_TIMEOUT_S, local=True)


def _local_complete(messages: List[Dict[str, Any]], *, max_tokens: int,
                    temperature: float,
                    on_delta: Optional[Callable[[str], None]]) -> Optional[Result]:
    if not config.flag("v3_local_fallback_enabled", True):
        return None
    try:
        from .. import local_model
        if not local_model.ensure_running():
            return None
    except Exception:
        return None
    lane = _local_lane()
    race = transport.Race(on_delta)
    body = transport.build_body(lane, ANSWER, messages, tools=None, tool_choice="none",
                 max_tokens=max_tokens, temperature=temperature, reasoning="")
    attempt = transport.Attempt(lane, body, race, 0)
    attempt.start()
    ends = time.monotonic() + _LOCAL_TIMEOUT_S
    # The attempt reports "first" when it starts writing and "done" when it
    # has finished; only the second carries the answer.
    while True:
        try:
            kind, _key, payload = race.events.get(timeout=max(0.1, ends - time.monotonic()))
        except queue.Empty:
            attempt.stop()
            return None
        if kind == "first":
            continue
        if kind != "done":
            return None
        break
    found, _headers = payload
    try:
        from .. import local_model
        local_model.note_use()
    except Exception as exc:
        syrix_obs.report("v3.lanes._local_complete", exc, level="debug")
    return found


def status() -> Dict[str, Any]:
    """Every lane, in the order it would be tried, with what is known about it."""
    now = time.monotonic()
    rows = []
    seen = set()
    for role in (ANSWER, DEEP, FAST):
        for key in ORDER[role]:
            if key in seen or key not in CATALOGUE:
                continue
            seen.add(key)
            lane = CATALOGUE[key]
            live = ledger.state_of(key)
            possible, wait, why = admission(lane, 4000, now)
            if not ledger.configured(lane):
                condition = "no key"
            elif not possible:
                condition = why
            elif wait > 0:
                condition = f"refilling ({wait:.0f}s)"
            else:
                condition = "ready"
            rows.append({
                "name": key,
                "provider": lane.provider,
                "model": lane.model,
                "available": possible and ledger.configured(lane),
                "state": condition,
                "tools": lane.tools and not live.tools_broken,
                "used_today": live.requests,
                "limit_today": lane.rpd or "",
                "tokens_today": live.tokens,
                "last_ms": live.last_ms,
            })
    return {"configured": True, "engine": "v3 lanes", "providers": rows}


def reset_for_tests() -> None:
    ledger.reset()
