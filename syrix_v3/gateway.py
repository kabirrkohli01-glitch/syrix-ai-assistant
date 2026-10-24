"""The one place v3 talks to a model.

Every call goes to ``lanes`` — v3's own transport, which races the measured
free lanes, admits requests against each provider's reported limits, streams
answer text as it arrives and keeps the local model as the floor. The
``v3_lanes_enabled`` setting set to false routes through the previous
``brain_pool`` rotation instead, unchanged, so the switch can be undone by
configuration alone.

What this layer adds on top of either transport:

* **Native tool calling.** The transport has always spoken OpenAI ``tools`` /
  ``tool_calls``; the old loop asked the model to write ``<action>{...}</action>``
  into its prose and regexed it back out. That is why tool markup reached the
  screen and why rounds were spent on retries.
* **A prompt budget that follows the lane.** A fast lane has a small
  per-minute token allowance and a strong lane can carry a dossier. One flat
  figure either wastes the strong lane or overruns the fast one.
* **A toolless fall-back.** Only cloud lanes advertise tool support; the local
  floor does not. When every tool-capable lane is spent, the turn continues
  without tools and answers from what it already gathered, rather than
  failing with an apology.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import config, lanes

_configure_lock = threading.Lock()
_configured = False

# What the local lane gets when it is the only lane left. Generous on
# purpose: the first call after a cold start loads weights into VRAM, and
# an answer that arrives late beats no answer at all.
_LOCAL_TIMEOUT_S = 120.0


class NoLaneAvailable(RuntimeError):
    """Every lane for this tier is spent, cooling down or absent."""


@dataclass
class Completion:
    """One model round trip."""

    text: str = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    latency_ms: float = 0.0
    rotations: List[str] = field(default_factory=list)
    tools_offered: bool = False

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


def _pool_module():
    from syrix_core_v2 import brain_pool  # noqa: WPS433 — kept plumbing, imported late
    return brain_pool


def _local_model():
    from . import local_model  # noqa: WPS433 — imported late; it spawns a process
    return local_model


def ensure_configured() -> None:
    """Build the pool from settings on first use.

    The running application configures the pool during boot; the probe and a
    bare test process do not, and a pool that was never configured raises on
    every call. Doing it here means v3 works identically in all three.
    """
    global _configured
    if _configured:
        return
    with _configure_lock:
        if _configured:
            return
        brain_pool = _pool_module()
        if brain_pool.get_pool() is None:
            brain_pool.configure(config.settings(), log_fn=None)
        _configured = True


def _normalise_tool_calls(raw: Any) -> List[Dict[str, Any]]:
    """Reduce a provider's tool calls to ``{id, name, arguments}``.

    Providers differ in whether arguments arrive as a JSON string or an object
    and in whether an id is present at all. The loop should never have to care,
    so the shape is settled once, here.
    """
    out: List[Dict[str, Any]] = []
    for i, call in enumerate(raw or []):
        if not isinstance(call, dict):
            continue
        fn = call.get("function") if isinstance(call.get("function"), dict) else call
        name = str(fn.get("name") or "").strip()
        if not name:
            continue
        out.append({
            "id": str(call.get("id") or f"call_{i}"),
            "name": name,
            "arguments": fn.get("arguments"),
        })
    return out


def complete(
    messages: List[Dict[str, Any]],
    *,
    tier: str,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: str = "auto",
    max_tokens: int = 4096,
    temperature: float = 0.5,
    prompt_chars: int = 18000,
    timeout_s: Optional[float] = None,
    allow_toolless_fallback: bool = True,
    allow_local_fallback: bool = True,
    reasoning: str = "",
    on_delta: Optional[Any] = None,
) -> Completion:
    """Ask a lane for one completion.

    ``timeout_s`` is the ceiling for the whole attempt including rotation, so
    a caller working to a deadline can hand it what is left rather than
    discovering afterwards that one lane spent it all. ``on_delta`` receives
    answer text as it streams in; ``reasoning`` overrides how hard a
    reasoning model thinks (``low``, ``medium`` or ``high``).
    """
    if config.flag("v3_lanes_enabled", True):
        started = time.perf_counter()
        try:
            result = lanes.complete(
                messages,
                role=lanes.TIER_ROLE.get(tier, lanes.ANSWER),
                tools=tools,
                tool_choice=tool_choice,
                max_tokens=max_tokens,
                temperature=temperature,
                reasoning=reasoning,
                max_chars=prompt_chars,
                timeout_s=timeout_s,
                on_delta=on_delta,
                allow_local=allow_local_fallback,
            )
        except lanes.NoLaneAvailable as exc:
            raise NoLaneAvailable(str(exc)) from exc
        return Completion(
            text=result.text,
            tool_calls=_normalise_tool_calls(
                [{"id": c["id"], "function": {"name": c["name"], "arguments": c["arguments"]}}
                 for c in result.tool_calls]),
            provider=result.provider if result.provider != "local" else "ollama",
            model=result.model,
            latency_ms=round((time.perf_counter() - started) * 1000.0, 1),
            rotations=list(result.rotations),
            tools_offered=result.tools_used,
        )
    ensure_configured()
    brain_pool = _pool_module()
    started = time.perf_counter()

    def _call(with_tools: Optional[List[Dict[str, Any]]],
              *, deadline: Optional[float] = None,
              pinned: str = "") -> Any:
        return brain_pool.chat(
            tier,
            messages,
            tools=with_tools,
            tool_choice=tool_choice if with_tools else "none",
            max_tokens=int(max_tokens),
            temperature=float(temperature),
            max_prompt_chars=int(prompt_chars),
            overall_timeout_s=timeout_s if deadline is None else deadline,
            pinned_provider=pinned,
            use_cache=not with_tools,
        )

    def _is_exhausted(exc: BaseException) -> bool:
        return type(exc).__name__ == "PoolExhausted"

    def _wake_local_and_retry(with_tools):
        """Last resort: bring the local model up and ask it.

        Nothing starts it in advance. This is the only path that does, so
        the GPU stays idle — and the machine stays quiet and cool enough to
        game on — until a turn genuinely has nowhere else to go.
        """
        if not allow_local_fallback:
            return None
        if not _local_model().ensure_running():
            return None
        # The pool cooled the local lane down while its server was dead, and
        # would go on skipping it now that the server is up. Starting a model
        # and then not being allowed to use it is the worst of both.
        pool = brain_pool.get_pool()
        if pool is not None and hasattr(pool, "clear_cooldown"):
            pool.clear_cooldown("ollama")
        try:
            # A cold local model loads weights into VRAM before it answers a
            # word, which takes longer than any fast-turn deadline. Handing
            # it the remainder of a Spark budget guarantees a timeout, and
            # then the turn reports no lane available with a working model
            # sitting right there. This lane gets the time it needs, or
            # there is no point starting it.
            answer = _call(with_tools, deadline=_LOCAL_TIMEOUT_S, pinned="ollama")
        except Exception:
            return None
        _local_model().note_use()
        return answer

    tools_offered = bool(tools)
    try:
        result = _call(tools)
    except Exception as exc:
        if not _is_exhausted(exc):
            raise
        result = None
        if tools and allow_toolless_fallback:
            # Only cloud lanes advertise tool support. Losing them should
            # cost the turn its tools, not its answer.
            tools_offered = False
            try:
                result = _call(None)
            except Exception as inner:
                if not _is_exhausted(inner):
                    raise
                result = None
        if result is None:
            tools_offered = False
            result = _wake_local_and_retry(None)
        if result is None:
            raise NoLaneAvailable(str(exc)) from exc

    return Completion(
        text=str(getattr(result, "text", "") or ""),
        tool_calls=_normalise_tool_calls(getattr(result, "tool_calls", None)),
        provider=str(getattr(result, "provider", "") or ""),
        model=str(getattr(result, "model", "") or ""),
        latency_ms=round((time.perf_counter() - started) * 1000.0, 1),
        rotations=list(getattr(result, "rotations", None) or []),
        tools_offered=tools_offered,
    )


def status() -> Dict[str, Any]:
    """Lane health and remaining budget, for the identity tool and the HUD."""
    if config.flag("v3_lanes_enabled", True):
        try:
            return lanes.status()
        except Exception as exc:
            return {"configured": False, "error": f"{type(exc).__name__}: {exc}"}
    try:
        ensure_configured()
        return _pool_module().status()
    except Exception as exc:
        return {"configured": False, "error": f"{type(exc).__name__}: {exc}"}


def reset_for_tests() -> None:
    global _configured
    _configured = False
