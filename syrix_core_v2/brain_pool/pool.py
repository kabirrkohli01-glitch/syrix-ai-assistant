"""The brain pool — one entry point, six free lanes, zero rate-limit deaths.

``chat(tier, messages)`` walks the (provider, model) candidates for the tier:
  1. skip lanes in cooldown (429 → Retry-After or exponential backoff)
  2. skip lanes without ledger headroom (rotate BEFORE the 429, 10% margin)
  3. rank the rest by health score (EWMA success × latency)
  4. call; on failure decay health, cool down, try the next lane
Ollama is always the floor; if literally everything fails, PoolExhausted is
raised so the caller's legacy chain can produce its usual gentle message.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import syrix_obs
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple

from . import client as _client
from . import providers as _providers
from . import quality as _quality
from .cache import ReplyCache
from .ledger import QuotaLedger, estimate_tokens
from .privacy import payload_contains_secret, provider_is_loopback
from .providers import (
    BACKUP_SUFFIX,
    T0_LOCAL,
    T1_FAST,
    T2_BALANCED,
    T3_STRONG,
    ProviderRegistry,
    ProviderSpec,
    normalize_tier,
)

_429_BACKOFF_S = [30.0, 60.0, 120.0, 240.0, 480.0]  # per consecutive 429
_FAIL_BACKOFF_S = [10.0, 20.0, 45.0, 90.0]  # 5xx / timeout / network
_MODEL_UNAVAILABLE_BACKOFF_S = 24.0 * 60.0 * 60.0  # 404/410 model id retired
_HEALTH_ALPHA = 0.3
# Reply reservation a provider's own timeout is assumed to cover. Requests at
# or below this size are the ordinary case the per-provider ceilings were
# measured against; only the excess buys extra wall clock.
_TIMEOUT_FREE_TOKENS = 2000


class PoolExhausted(RuntimeError):
    """Every candidate lane failed or was unavailable."""


@dataclass
class PoolResult:
    text: str = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    latency_ms: float = 0.0
    from_cache: bool = False
    rotations: List[str] = field(default_factory=list)


@dataclass
class _LaneHealth:
    ok_ewma: float = 1.0
    latency_ewma_s: float = 1.0
    consecutive_429: int = 0
    consecutive_fail: int = 0
    cooldown_until: float = 0.0
    cooldown_reason: str = ""
    last_ok_ts: float = 0.0
    calls: int = 0
    failures: int = 0

    def score(self) -> float:
        return self.ok_ewma / (1.0 + self.latency_ewma_s / 10.0)


class BrainPool:
    def __init__(
        self,
        cfg: Optional[Dict[str, Any]] = None,
        log_fn: Optional[Callable[[str], None]] = None,
    ):
        self._cfg = dict(cfg or {})
        self._log = log_fn or (lambda m: None)
        self.registry = ProviderRegistry(self._cfg, self._log)
        # Frontier quality lane (2026-07-19): "" when disabled/keyless.
        self._frontier_model = _providers.frontier_model_for(self._cfg)
        self.ledger = QuotaLedger()
        self.cache = ReplyCache(ttl_s=float(self._cfg.get("brain_pool_cache_ttl_s", 600.0)))
        self._health: Dict[str, _LaneHealth] = {}
        self._lock = threading.Lock()
        self._models_synced = False
        self._on_provider_used: Optional[Callable[[str, str], None]] = None
        self._on_fallback_notice: Optional[Callable[[Dict[str, Any]], None]] = None

    # ------------------------------------------------------------- wiring
    def set_provider_used_callback(self, cb: Callable[[str, str], None]) -> None:
        """cb(provider, model) — lets syrix_host_brain keep CURRENT_BRAIN accurate."""
        self._on_provider_used = cb

    def set_fallback_notice_callback(self, cb: Callable[[Dict[str, Any]], None]) -> None:
        """cb(info) — fired once per request that was served by a fallback lane
        (primary skipped for budget/cooldown, or failed mid-request and the
        pool rotated). info = {tier, provider, model, skipped, rotations};
        `skipped` entries are {provider, model, reason} dicts. Used for the
        Dynamic Island fallback toast (2026-07-08)."""
        self._on_fallback_notice = cb

    def _notify_fallback(
        self,
        tier: str,
        provider: str,
        model: str,
        skipped_info: List[Dict[str, str]],
        rotations: List[Dict[str, str]],
    ) -> None:
        if not self._on_fallback_notice or not (skipped_info or rotations):
            return
        try:
            self._on_fallback_notice({
                "tier": tier,
                "provider": provider,
                "model": model,
                "skipped": list(skipped_info),
                "rotations": list(rotations),
            })
        except Exception as exc:
            syrix_obs.report("brain_pool.fallback_notice", exc, level="debug")

    def sync_models_async(self) -> None:
        def _run() -> None:
            try:
                import requests

                self.registry.sync_models(requests.get)
            except Exception as exc:
                self._log(f"[BRAIN_POOL] model_sync_failed err={type(exc).__name__}")

        if not self._models_synced:
            self._models_synced = True
            threading.Thread(target=_run, daemon=True, name="BrainPoolModelSync").start()

    # ------------------------------------------------------------- health
    def _lane(self, key: str) -> _LaneHealth:
        with self._lock:
            lane = self._health.get(key)
            if lane is None:
                lane = _LaneHealth()
                self._health[key] = lane
            return lane

    def clear_cooldown(self, provider: str) -> int:
        """Forget a provider's recorded failures. Returns lanes cleared.

        For the one case where the caller knows something the health record
        does not: the local lane fails while its server is down, is cooled
        down for it, and is then still skipped after the server has been
        started — so bringing it up achieves nothing and the turn reports
        no lane available with a working model sitting right there.
        """
        prefix = str(provider or "").strip().lower()
        if not prefix:
            return 0
        cleared = 0
        with self._lock:
            for key, lane in self._health.items():
                if key == prefix or key.startswith(prefix + ":"):
                    lane.cooldown_until = 0.0
                    lane.cooldown_reason = ""
                    lane.consecutive_fail = 0
                    lane.consecutive_429 = 0
                    lane.ok_ewma = max(lane.ok_ewma, 0.5)
                    cleared += 1
        return cleared

    def _record_ok(self, key: str, latency_s: float) -> None:
        lane = self._lane(key)
        with self._lock:
            lane.ok_ewma = (1 - _HEALTH_ALPHA) * lane.ok_ewma + _HEALTH_ALPHA * 1.0
            lane.latency_ewma_s = (1 - _HEALTH_ALPHA) * lane.latency_ewma_s + _HEALTH_ALPHA * latency_s
            lane.consecutive_429 = 0
            lane.consecutive_fail = 0
            lane.cooldown_until = 0.0
            lane.cooldown_reason = ""
            lane.last_ok_ts = time.time()
            lane.calls += 1

    def _record_fail(self, key: str, *, is_429: bool,
                     retry_after_s: Optional[float], http_status: int = 0) -> None:
        lane = self._lane(key)
        with self._lock:
            lane.ok_ewma = (1 - _HEALTH_ALPHA) * lane.ok_ewma
            lane.calls += 1
            lane.failures += 1
            now = time.monotonic()
            # 404/410: the model id is retired. 401/403: the key is rejected —
            # a dead or unentitled key. 402: the key has no credit left. All of
            # these are PERMANENT for this run, so the normal 10-90s backoff
            # just re-probes a lane that cannot work. Seen with a stale
            # GEMINI_API_KEY returning "your project has been denied access"
            # every ninety seconds forever.
            #
            # 402 added 2026-09-20. Cerebras answers "Payment required to
            # access this resource" on every model, and a payment state does
            # not change inside a session any more than a rejected key does —
            # but at ninety-second backoff it sat at the FRONT of the rotation
            # and charged roughly 300ms of dead round trip to most turns.
            if int(http_status or 0) in (401, 402, 403, 404, 410):
                lane.consecutive_fail += 1
                lane.cooldown_until = now + _MODEL_UNAVAILABLE_BACKOFF_S
                lane.cooldown_reason = (
                    f"key_rejected_http_{int(http_status)}"
                    if int(http_status) in (401, 402, 403)
                    else f"model_unavailable_http_{int(http_status)}")
            elif is_429:
                lane.consecutive_429 += 1
                idx = min(lane.consecutive_429 - 1, len(_429_BACKOFF_S) - 1)
                wait = retry_after_s if (retry_after_s and retry_after_s > 0) else _429_BACKOFF_S[idx]
                lane.cooldown_until = now + wait
                lane.cooldown_reason = "rate_limited"
            else:
                lane.consecutive_fail += 1
                idx = min(lane.consecutive_fail - 1, len(_FAIL_BACKOFF_S) - 1)
                lane.cooldown_until = now + _FAIL_BACKOFF_S[idx]
                lane.cooldown_reason = "error"

    # ------------------------------------------------------------- candidates
    def _ordered_candidates(
        self,
        tier: str,
        *,
        est_tokens: int,
        need_tools: bool,
        need_stream: bool,
        pinned_provider: str = "",
        reserve: float = 0.0,
    ) -> Tuple[List[Tuple[ProviderSpec, str, str]], List[Dict[str, str]]]:
        """(usable lanes best first, skipped-lane info for the fallback toast)."""
        now = time.monotonic()
        usable: List[Tuple[float, ProviderSpec, str, str]] = []
        skipped: List[str] = []
        skipped_info: List[Dict[str, str]] = []
        for spec, model in self.registry.candidates(tier):
            if need_tools and not spec.supports_tools:
                continue
            if need_stream and not spec.supports_stream:
                continue
            key = spec.ledger_key(model)
            lane = self._lane(key)
            if lane.cooldown_until > now:
                skipped.append(f"{key}:cooldown({lane.cooldown_reason})")
                skipped_info.append({"provider": spec.name, "model": model, "reason": f"cooldown:{lane.cooldown_reason}"})
                continue
            ok, why = self.ledger.headroom(key, spec.caps_for(model), est_tokens,
                                           reserve=reserve)
            if not ok and spec.name != "ollama":
                skipped.append(f"{key}:budget({why})")
                skipped_info.append({"provider": spec.name, "model": model, "reason": f"budget:{why}"})
                continue
            score = lane.score()
            # Tier preference: the first-listed model for a tier is the QUALITY
            # pick — a few hundred ms of latency jitter must not demote the 70B
            # below the 8B. Bonus stays well under one health failure (0.3).
            try:
                pos = spec.models.get(tier, []).index(model)
            except ValueError:
                pos = 9
            score += max(0.0, 0.12 - 0.06 * pos)
            # Frontier quality pick (2026-07-19 audit P0): the designated
            # frontier lane must beat every healthy fast lane on QUALITY, not
            # latency (base score maxes at ~1.0, so +5 is decisive), while a
            # cooldown/budget skip above still removes it entirely — outages
            # walk down to the fast lanes exactly as before.
            if (tier in (T2_BALANCED, T3_STRONG)
                    and spec.name == "openrouter"
                    and model == self._frontier_model):
                score += 5.0
            if pinned_provider and spec.name == pinned_provider:
                score += 100.0  # user pin wins, everything else stays as recovery
            if spec.name == "ollama":
                score -= 50.0  # always the floor, never beats a healthy cloud lane
            if spec.name.endswith(BACKUP_SUFFIX):
                score -= 0.3  # second-account key: serves when its primary is limited
            usable.append((score, spec, model, key))
        if skipped:
            self._log(f"[BRAIN_POOL] lanes_skipped tier={tier} skipped={','.join(skipped[:6])}")
        usable.sort(key=lambda item: item[0], reverse=True)
        return [(spec, model, key) for _, spec, model, key in usable], skipped_info

    def _timeouts_for(self, spec: ProviderSpec, tier: str) -> float:
        return spec.timeout_fast_s if tier in (T1_FAST, T0_LOCAL) else spec.timeout_strong_s

    def _lane_extra_body(self, spec: ProviderSpec,
                         model: str) -> Optional[Dict[str, Any]]:
        """Per-lane request fields, currently only "do not think out loud".

        A local reasoning model on Ollama's OpenAI-compatible endpoint returns
        its reasoning in ``message.reasoning`` and leaves ``content`` EMPTY, so
        the lane looks dead rather than slow. Measured on qwen3:8b, 2026-07-28:
        8.1s and 0 chars plain, 2.4s and a correct answer with reasoning off.

        Scoped to the ollama lane on purpose — ``reasoning_effort: "none"`` is
        not universally accepted, and a cloud provider that rejects it would
        turn a working lane into an HTTP 400.
        """
        if spec.name != "ollama" or not _providers.is_reasoning_model(model):
            return None
        if not bool(self._cfg.get("brain_pool_local_no_reasoning", True)):
            return None
        return {"reasoning_effort": "none"}

    def _lane_timeout(self, spec: ProviderSpec, tier: str,
                      requested_s: Optional[float],
                      max_tokens: int = 0) -> float:
        timeout = float(requested_s or self._timeouts_for(spec, tier))
        # A per-provider ceiling is a statement about how long that lane may
        # STALL, but it was being applied to requests of wildly different
        # sizes: the same 22s bound governed a 1,600-token reply and an
        # 8,192-token one. The large request cannot finish inside it on any
        # lane, so the ceiling stopped bounding a stall and started
        # guaranteeing a failure — measured, every deep-mode turn timed out on
        # the one provider with no token cap and fell to the local model.
        #
        # The floors below are deliberately generous: this value bounds a
        # HUNG lane, and a caller working to a deadline passes its own
        # remaining time, which still wins via the min() at both call sites.
        # Local generation is slower per token and already has its own clamp.
        tokens = max(0, int(max_tokens or 0))
        if tokens > _TIMEOUT_FREE_TOKENS and spec.name != "ollama":
            seconds_per_token = 1.0 / float(
                self._cfg.get("brain_pool_min_tokens_per_s", 90.0) or 90.0)
            timeout = max(timeout,
                          (tokens - _TIMEOUT_FREE_TOKENS) * seconds_per_token)
        if spec.name == "ollama":
            # A cloud-fast policy is commonly 4s, but the local floor needs a
            # little longer to load/generate on consumer hardware.  Clamping
            # only the local lane preserves cloud latency while allowing the
            # final offline fallback to serve instead of being pre-doomed.
            timeout = max(
                timeout,
                float(self._cfg.get("brain_pool_ollama_min_timeout_s", 12.0)),
            )
        return timeout

    # ------------------------------------------------------------- main entry
    def chat(
        self,
        tier: str,
        messages: List[Dict[str, Any]],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.55,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        timeout_s: Optional[float] = None,
        overall_timeout_s: Optional[float] = None,
        use_cache: bool = True,
        pinned_provider: str = "",
        reserve: float = 0.0,
        max_prompt_chars: Optional[int] = None,
    ) -> PoolResult:
        """``reserve`` holds back that fraction of each lane's DAILY budget for
        callers who pass none. Supercomputer passes one so a long agentic task
        cannot spend the whole day's free tokens and leave ordinary chat on a
        local 7B; normal chat passes nothing and can use every last token.

        ``max_prompt_chars`` overrides the default history trim. The default
        suits a fast lane's per-minute token budget; a caller assembling a
        large evidence pack for a strong lane needs a larger one, and silently
        trimming it to the fast-lane figure discards evidence that was fetched
        on purpose. Omitted, behaviour is unchanged."""
        secret_bearing = payload_contains_secret(messages, tools)
        if secret_bearing:
            # Never persist a reply that could echo supplied credentials.
            use_cache = False
        tier = normalize_tier(tier)
        trimmed = (
            _quality.budget_messages(messages, max_prompt_chars=int(max_prompt_chars))
            if max_prompt_chars
            else _quality.budget_messages(messages)
        )
        msgs = _quality.ensure_persona(trimmed)
        est = estimate_tokens(msgs, max_tokens)

        cache_key = ""
        if use_cache and not tools:
            cache_key = ReplyCache.key_for(tier, msgs)
            hit = self.cache.get(cache_key)
            if hit:
                self._log(f"[BRAIN_POOL] cache_hit tier={tier} chars={len(hit)}")
                return PoolResult(text=hit, provider="cache", model="", from_cache=True)

        lanes, skipped_info = self._ordered_candidates(
            tier, est_tokens=est, need_tools=bool(tools), need_stream=False,
            pinned_provider=str(pinned_provider or "").strip().lower(),
            reserve=float(reserve or 0.0),
        )
        if secret_bearing:
            # Content policy is stronger than tier configuration and provider
            # pins. Even a misconfigured cloud lane in T0_LOCAL is excluded.
            lanes = [
                lane for lane in lanes
                if lane[0].name == "ollama"
                and provider_is_loopback(lane[0].base_url)
            ]
        if not lanes:
            raise PoolExhausted(f"no usable lanes for tier={tier} (tools={bool(tools)})")

        overall_deadline = (
            time.monotonic() + max(0.001, float(overall_timeout_s))
            if overall_timeout_s is not None else None)
        rotations: List[str] = []
        rotation_info: List[Dict[str, str]] = []
        last_err: Optional[Exception] = None
        for spec, model, key in lanes:
            remaining = ((overall_deadline - time.monotonic())
                         if overall_deadline is not None else None)
            # Break only once the shared deadline has actually elapsed. The
            # per-lane timeout below is floored at 1ms, so as long as ANY
            # budget remains we still attempt one clamped lane rather than
            # returning zero attempts (a shared deadline must not prevent every
            # lane from running — it bounds total time, it does not skip the
            # first try).
            if remaining is not None and remaining <= 0.0:
                break
            t0 = time.perf_counter()
            # Reasoning models spend output budget thinking before any visible
            # text — a 60-token cap returns empty content. Raise the floor.
            lane_max_tokens = (
                max(int(max_tokens), _providers.REASONING_MIN_TOKENS)
                if _providers.is_reasoning_model(model)
                else int(max_tokens)
            )
            try:
                lane_timeout = self._lane_timeout(spec, tier, timeout_s,
                                                  lane_max_tokens)
                if remaining is not None:
                    lane_timeout = max(0.001, min(lane_timeout, remaining))
                result = _client.chat(
                    base_url=spec.base_url,
                    api_key=spec.api_key(),
                    model=model,
                    messages=msgs,
                    temperature=temperature,
                    max_tokens=lane_max_tokens,
                    timeout_s=lane_timeout,
                    tools=tools,
                    tool_choice=tool_choice,
                    extra_headers=spec.extra_headers,
                    extra_body=self._lane_extra_body(spec, model),
                )
                latency_s = time.perf_counter() - t0
                self._record_ok(key, latency_s)
                self.ledger.record(key, result.usage_tokens or est)
                self._log(
                    f"[BRAIN_POOL] ok provider={spec.name} model={model} tier={tier} "
                    f"latency_ms={latency_s * 1000.0:.0f} tokens={result.usage_tokens or est} "
                    f"rotations={len(rotations)}"
                )
                if self._on_provider_used:
                    try:
                        self._on_provider_used(spec.name, model)
                    except Exception as exc:
                        syrix_obs.report("brain_pool.provider_used_cb", exc, level="debug")
                if cache_key and result.text:
                    self.cache.put(cache_key, result.text, provider=spec.name, model=model)
                self._notify_fallback(tier, spec.name, model, skipped_info, rotation_info)
                return PoolResult(
                    text=result.text,
                    tool_calls=result.tool_calls,
                    provider=spec.name,
                    model=model,
                    latency_ms=latency_s * 1000.0,
                    rotations=rotations,
                )
            except _client.ProviderHTTPError as exc:
                last_err = exc
                is_429 = exc.status == 429
                self._record_fail(key, is_429=is_429,
                                  retry_after_s=exc.retry_after_s,
                                  http_status=exc.status)
                rotations.append(f"{key}:http_{exc.status}")
                rotation_info.append({"provider": spec.name, "model": model, "reason": f"http_{exc.status}"})
                self._log(
                    f"[BRAIN_POOL] lane_failed provider={spec.name} model={model} "
                    f"err=http_{exc.status} retry_after={exc.retry_after_s or '-'} rotating=true"
                )
            except (_client.ProviderTimeout, _client.ProviderNetworkError, _client.EmptyReply) as exc:
                last_err = exc
                self._record_fail(key, is_429=False, retry_after_s=None)
                rotations.append(f"{key}:{type(exc).__name__}")
                rotation_info.append({"provider": spec.name, "model": model, "reason": type(exc).__name__})
                self._log(
                    f"[BRAIN_POOL] lane_failed provider={spec.name} model={model} "
                    f"err={type(exc).__name__} rotating=true"
                )
            except Exception as exc:  # unexpected — treat like a lane failure, keep rotating
                last_err = exc
                self._record_fail(key, is_429=False, retry_after_s=None)
                rotations.append(f"{key}:{type(exc).__name__}")
                rotation_info.append({"provider": spec.name, "model": model, "reason": type(exc).__name__})
                self._log(
                    f"[BRAIN_POOL] lane_failed_unexpected provider={spec.name} model={model} "
                    f"err={type(exc).__name__}"
                )
        raise PoolExhausted(
            f"all available lanes failed for tier={tier}: "
            f"{type(last_err).__name__ if last_err else 'unknown'}"
        )

    # ------------------------------------------------------------- streaming
    def stream(
        self,
        tier: str,
        messages: List[Dict[str, Any]],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.55,
        timeout_s: Optional[float] = None,
        overall_timeout_s: Optional[float] = None,
        pinned_provider: str = "",
        on_provider_used: Optional[Callable[[str, str], None]] = None,
    ) -> Generator[str, None, None]:
        """Yield deltas from the best healthy lane. On a mid-stream failure the
        caller falls back to blocking chat() — partial text is its problem."""
        secret_bearing = payload_contains_secret(messages)
        tier = normalize_tier(tier)
        msgs = _quality.ensure_persona(_quality.budget_messages(messages))
        est = estimate_tokens(msgs, max_tokens)
        lanes, skipped_info = self._ordered_candidates(
            tier, est_tokens=est, need_tools=False, need_stream=True,
            pinned_provider=str(pinned_provider or "").strip().lower(),
        )
        if secret_bearing:
            lanes = [
                lane for lane in lanes
                if lane[0].name == "ollama"
                and provider_is_loopback(lane[0].base_url)
            ]
        if not lanes:
            raise PoolExhausted(f"no streamable lanes for tier={tier}")
        spec, model, key = lanes[0]
        # Streaming picks one lane up front — if better-ranked lanes were
        # skipped (budget/cooldown) this turn is already running on a fallback.
        self._notify_fallback(tier, spec.name, model, skipped_info, [])
        lane_max_tokens = (
            max(int(max_tokens), _providers.REASONING_MIN_TOKENS)
            if _providers.is_reasoning_model(model)
            else int(max_tokens)
        )
        lane_timeout = self._lane_timeout(spec, tier, timeout_s, lane_max_tokens)
        if overall_timeout_s is not None:
            lane_timeout = max(
                0.001,
                min(lane_timeout, float(overall_timeout_s)),
            )
        t0 = time.perf_counter()
        try:
            received = 0
            for delta in _client.stream(
                base_url=spec.base_url,
                api_key=spec.api_key(),
                model=model,
                messages=msgs,
                temperature=temperature,
                max_tokens=lane_max_tokens,
                timeout_s=lane_timeout,
                extra_headers=spec.extra_headers,
                extra_body=self._lane_extra_body(spec, model),
            ):
                received += len(delta)
                yield delta
            latency_s = time.perf_counter() - t0
            self._record_ok(key, latency_s)
            self.ledger.record(key, est)
            if self._on_provider_used:
                try:
                    self._on_provider_used(spec.name, model)
                except Exception as exc:
                    syrix_obs.report("brain_pool.provider_used_cb", exc, level="debug")
            if on_provider_used:
                try:
                    on_provider_used(spec.name, model)
                except Exception as exc:
                    syrix_obs.report("brain_pool.stream_used_cb", exc, level="debug")
            self._log(
                f"[BRAIN_POOL] stream_ok provider={spec.name} model={model} tier={tier} "
                f"chars={received} latency_ms={latency_s * 1000.0:.0f}"
            )
        except _client.ProviderHTTPError as exc:
            self._record_fail(key, is_429=(exc.status == 429),
                              retry_after_s=exc.retry_after_s,
                              http_status=exc.status)
            raise
        except Exception:
            self._record_fail(key, is_429=False, retry_after_s=None)
            raise

    # ------------------------------------------------------------- status
    def has_remote_lane(self, tier: str, est_tokens: int = 4000,
                        reserve: float = 0.0) -> bool:
        """Is there a NON-local lane this tier could actually use right now?

        Read-only and silent — it makes no call, records nothing and does not
        log a ``lanes_skipped`` line (it is a question, not an attempt). Added
        2026-07-27 for Supercomputer: the agent lowers its tier for routine
        steps, and on this pool T1_FAST regularly has every remote lane spent,
        so "cheaper" quietly meant "local 7B" — 10-18s per call and the wrong
        next action. Asking first lets the caller stay on a tier that still has
        a real model behind it.
        """
        now = time.monotonic()
        try:
            for spec, model in self.registry.candidates(normalize_tier(tier)):
                if spec.name == "ollama":
                    continue
                key = spec.ledger_key(model)
                if self._lane(key).cooldown_until > now:
                    continue
                ok, _why = self.ledger.headroom(key, spec.caps_for(model),
                                                int(est_tokens), reserve=float(reserve or 0.0))
                if ok:
                    return True
        except Exception as exc:
            syrix_obs.report("brain_pool.has_remote_lane", exc, level="debug", tier=str(tier))
            return True  # unknown → do not let a probe failure change routing
        return False

    def status(self) -> Dict[str, Any]:
        now = time.monotonic()
        providers: List[Dict[str, Any]] = []
        for spec in self.registry.providers.values():
            models_flat = sorted({m for ms in spec.models.values() for m in ms})
            lanes: List[Dict[str, Any]] = []
            for model in models_flat:
                key = spec.ledger_key(model)
                lane = self._lane(key)
                entry: Dict[str, Any] = {
                    "model": model,
                    "score": round(lane.score(), 3),
                    "ok_ewma": round(lane.ok_ewma, 3),
                    "calls": lane.calls,
                    "failures": lane.failures,
                }
                if lane.cooldown_until > now:
                    entry["cooldown_s"] = round(lane.cooldown_until - now, 1)
                    entry["cooldown_reason"] = lane.cooldown_reason
                entry.update(self.ledger.snapshot(key, spec.caps_for(model)))
                lanes.append(entry)
            providers.append(
                {
                    "name": spec.name,
                    "available": spec.available(),
                    "key_env": spec.key_env,
                    "key_present": bool(spec.api_key()) if spec.key_env else True,
                    "supports_tools": spec.supports_tools,
                    "lanes": lanes,
                }
            )
        return {"ts": time.time(), "providers": providers}
