"""Brain pool provider registry — every free lane SYRIX can think with.

Declarative: each provider entry lists its OpenAI-compatible base URL, the
env var holding its key, the models it serves per tier, and its free-tier
caps. A provider with no key simply reports unavailable — adding a key to
.env activates it on next boot with zero code changes (the owner can drop in
CEREBRAS_API_KEY / GITHUB_TOKEN later).

Caps are conservative published free-tier limits (June 2026). The ledger
treats them as budgets to rotate BEFORE hitting, not hard truths — a 429
still triggers a cooldown as backstop.

Model IDs drift; ``sync_models()`` validates the configured IDs against each
provider's live ``/models`` endpoint (cached 6 h under
%LOCALAPPDATA%\\SYRIX\\cache) and drops IDs the provider no longer serves.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional

import syrix_paths

# Pool tiers (model_router's "fast"/"standard"/"strong"/"deep" map onto these).
T0_LOCAL = "T0_LOCAL"
T1_FAST = "T1_FAST"
T2_BALANCED = "T2_BALANCED"
T3_STRONG = "T3_STRONG"

ALL_TIERS = (T0_LOCAL, T1_FAST, T2_BALANCED, T3_STRONG)

LEGACY_TIER_MAP = {
    "fast": T1_FAST,
    "standard": T2_BALANCED,
    "default": T2_BALANCED,
    "strong": T3_STRONG,
    "deep": T3_STRONG,
    "local": T0_LOCAL,
}


@dataclass
class Caps:
    """Free-tier budget. 0 means unlimited/unknown (no pre-emptive gate)."""

    rpm: int = 0
    rpd: int = 0
    tpm: int = 0
    tpd: int = 0
    scope: str = "model"  # "model" = per-model budget; "provider" = shared account budget


@dataclass
class ProviderSpec:
    name: str
    base_url: str
    key_env: str  # empty = no key needed (local)
    models: Dict[str, List[str]] = field(default_factory=dict)  # tier -> ordered model ids
    caps: Dict[str, Caps] = field(default_factory=dict)  # model id or "*" -> Caps
    supports_tools: bool = False
    supports_stream: bool = True
    extra_headers: Dict[str, str] = field(default_factory=dict)
    timeout_fast_s: float = 20.0
    timeout_strong_s: float = 60.0
    enabled: bool = True

    def api_key(self) -> str:
        if not self.key_env:
            return ""
        return str(os.environ.get(self.key_env) or "").strip()

    def available(self) -> bool:
        if not self.enabled:
            return False
        if self.key_env and not self.api_key():
            return False
        return True

    def caps_for(self, model: str) -> Caps:
        return self.caps.get(model) or self.caps.get("*") or Caps()

    def ledger_key(self, model: str) -> str:
        cap = self.caps_for(model)
        return self.name if cap.scope == "provider" else f"{self.name}:{model}"


def _dedupe(models: List[str]) -> List[str]:
    """Order-preserving dedupe — fallback lists may repeat the primary."""
    seen: set = set()
    out: List[str] = []
    for m in models:
        m = str(m or "").strip()
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def frontier_model_for(cfg: Dict[str, Any]) -> str:
    """Configured frontier model id, or "" when the frontier lane is off.

    2026-07-19 (audit P0): the pool's ceiling was gpt-oss-120b. When
    ``frontier_brain_enabled`` is true and an OpenRouter key exists, a
    frontier-class model (default Claude Sonnet 5, verified live on this
    account) is prepended to the OpenRouter T2/T3 lanes with its own budget.
    Kill switch: frontier_brain_enabled=false restores the free-only lane
    byte-identically. Any frontier-lane failure walks down to groq/cerebras
    exactly like every other lane failure — the upgrade can only ADD quality.
    """
    if not bool(cfg.get("frontier_brain_enabled", False)):
        return ""
    if not str(os.environ.get("OPENROUTER_API_KEY") or "").strip():
        return ""
    return str(cfg.get("frontier_brain_model")
               or "anthropic/claude-sonnet-5").strip()


def _openrouter_spec(cfg: Dict[str, Any]) -> ProviderSpec:
    # The OpenRouter FREE models (nemotron-3) are OFF by default (2026-07-23).
    # Measured live, that free endpoint answers a Spark turn in 9-37s (vs
    # ~0.5s on Groq/Cerebras), is rate-limited to ~50 requests/day, and — per
    # its own free-tier terms — LOGS/uses submitted prompts to improve the
    # provider's products. All three are wrong for a fast, privacy-focused local
    # assistant, and letting it into Spark's fast tiers was the dominant source
    # of Spark latency variance. Groq + Cerebras cover the fast lanes and carry
    # no such data-logging warning. Opt back in with
    # brain_pool_openrouter_free_enabled=true if you accept the latency/privacy
    # trade (e.g. to give Zenith a 550B lane). The frontier lane (paid, below)
    # is independent of this flag and unaffected.
    _free_on = bool(cfg.get("brain_pool_openrouter_free_enabled", False))
    t1 = ["nvidia/nemotron-3-super-120b-a12b:free"] if _free_on else []
    t2 = ["nvidia/nemotron-3-ultra-550b-a55b:free"] if _free_on else []
    t3 = ["nvidia/nemotron-3-ultra-550b-a55b:free"] if _free_on else []
    # Free models share ONE account-wide budget: 20 RPM, 50 RPD
    # (1000 RPD only after a $10 top-up — assume the worst).
    caps: Dict[str, Caps] = {"*": Caps(rpm=20, rpd=50, scope="provider")}
    frontier = frontier_model_for(cfg)
    if frontier:
        t2 = _dedupe([frontier] + t2)
        t3 = _dedupe([frontier] + t3)
        # Own per-model budget (scope="model") so the frontier lane neither
        # consumes nor is throttled by the shared free-pool allowance, and a
        # runaway day is bounded by rpd/tpd regardless of prompt volume.
        caps[frontier] = Caps(
            rpm=int(cfg.get("frontier_brain_rpm", 10) or 10),
            rpd=int(cfg.get("frontier_brain_rpd", 150) or 150),
            tpm=int(cfg.get("frontier_brain_tpm", 30000) or 30000),
            tpd=int(cfg.get("frontier_brain_tpd", 300000) or 300000),
            scope="model",
        )
    return ProviderSpec(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        key_env="OPENROUTER_API_KEY",
        models={T1_FAST: t1, T2_BALANCED: t2, T3_STRONG: t3},
        caps=caps,
        supports_tools=False,  # free pool tool support is inconsistent — don't rely on it
        supports_stream=True,
        extra_headers={
            "HTTP-Referer": "https://syrix.local",
            "X-Title": "SYRIX",
        },
    )


# Second accounts' keys. Each becomes a backup lane placed right after its
# primary. The lane only exists when the key is set, and the pool ranks it just
# below its primary, so it serves while the primary is rate-limited or cooling.
_BACKUP_KEYS = {"GROQ_API_KEY": "GROQ_API_KEY_2", "OPENROUTER_API_KEY": "OPENROUTER_API_KEY_2"}
BACKUP_SUFFIX = "-2"


def _with_backup_keys(specs: List[ProviderSpec]) -> List[ProviderSpec]:
    out: List[ProviderSpec] = []
    for spec in specs:
        out.append(spec)
        backup_env = _BACKUP_KEYS.get(spec.key_env)
        if backup_env:
            out.append(replace(
                spec,
                name=spec.name + BACKUP_SUFFIX,
                key_env=backup_env,
                models={tier: list(models) for tier, models in spec.models.items()},
                caps=dict(spec.caps),
                extra_headers=dict(spec.extra_headers),
            ))
    return out


def _default_registry(cfg: Dict[str, Any]) -> List[ProviderSpec]:
    groq_strong = str(cfg.get("groq_model") or "llama-3.3-70b-versatile").strip()
    groq_fast = (
        str(cfg.get("groq_model_fast") or "").strip()
        or str(cfg.get("groq_fast_model") or "").strip()
        or "llama-3.1-8b-instant"
    )
    # 2026-07-07 brain upgrade: gpt-oss-20b/120b became the Groq primaries;
    # the previous llama primaries stay in-lane as ordered fallbacks so a
    # missing/limited primary walks down INSIDE groq before leaving the
    # provider. Config-overridable, never removed.
    groq_fast_fb = str(cfg.get("groq_model_fast_fallback") or "llama-3.1-8b-instant").strip()
    groq_strong_fb = str(cfg.get("groq_model_strong_fallback") or "llama-3.3-70b-versatile").strip()
    ollama_model = str(cfg.get("ollama_model") or "qwen2.5:7b-instruct").strip()
    ollama_host = str(cfg.get("ollama_host") or "http://127.0.0.1:11434").rstrip("/")

    return _with_backup_keys([
        ProviderSpec(
            name="groq",
            base_url=str(cfg.get("groq_api_base") or "https://api.groq.com/openai/v1").rstrip("/"),
            key_env="GROQ_API_KEY",
            models={
                T1_FAST: _dedupe([groq_fast, groq_fast_fb]),
                T2_BALANCED: _dedupe([groq_strong, groq_fast, groq_strong_fb]),
                T3_STRONG: _dedupe([groq_strong, groq_strong_fb]),
            },
            caps={
                groq_strong: Caps(rpm=30, rpd=1000, tpm=12000, tpd=100000),
                groq_fast: Caps(rpm=30, rpd=14400, tpm=6000, tpd=500000),
                groq_strong_fb: Caps(rpm=30, rpd=1000, tpm=12000, tpd=100000),
                groq_fast_fb: Caps(rpm=30, rpd=14400, tpm=6000, tpd=500000),
                "*": Caps(rpm=30, rpd=1000, tpm=6000),
            },
            supports_tools=True,
            supports_stream=True,
        ),
        ProviderSpec(
            name="cerebras",
            base_url="https://api.cerebras.ai/v1",
            key_env="CEREBRAS_API_KEY",
            # Live catalog June 2026 (verified via /models): gpt-oss-120b +
            # zai-glm-4.7, both served at ~2000 tok/s — even the strong lane
            # answers in well under a second.
            #
            # gemma-4-31b appended 2026-07-27, LAST in every tier so the happy
            # path is unchanged. Cerebras budgets are per-model (scope="model",
            # 1M tokens/day each), and the third model on this key was simply
            # never used — measured live at 0.27s, clean output. On the day the
            # other two lanes exhaust their tpd (which is what collapsed
            # Supercomputer to local qwen2.5:7b), this is a third full day's
            # budget instead of a 7B model. It is only ever reached once the
            # models above it are spent or cooling.
            # 2026-09-20: THIS PROVIDER IS CURRENTLY DEAD, and the budgets below
            # are fiction until it is not. Every model on this key answers
            # HTTP 402 "Payment required to access this resource", and
            # zai-glm-4.7 is additionally archived (404). The free allowance
            # that made Cerebras the reason Spark is fast — roughly two
            # thousand tokens a second — is no longer on this key.
            #
            # The spec is kept rather than deleted so the lane returns the
            # moment credit does, and because a 402 costs one ~300ms rejection
            # and a cooldown rather than a timeout. But nothing downstream may
            # ASSUME this lane: Spark's speed and quality now have to come
            # from Groq, which is what the budget table was retuned for.
            models={
                T1_FAST: ["gpt-oss-120b"],
                T2_BALANCED: ["gpt-oss-120b", "qwen-3.8-27b"],
                T3_STRONG: ["gpt-oss-120b", "qwen-3.8-27b"],
            },
            caps={"*": Caps(rpm=30, rpd=14400, tpm=60000, tpd=1000000)},
            supports_tools=True,
            supports_stream=True,
        ),
        _openrouter_spec(cfg),
        ProviderSpec(
            name="github_models",
            base_url="https://models.github.ai/inference",
            key_env="GITHUB_TOKEN",
            models={
                T1_FAST: ["openai/gpt-4o-mini"],
                T2_BALANCED: ["openai/gpt-4o-mini"],
                T3_STRONG: ["openai/gpt-4o"],
            },
            caps={"*": Caps(rpm=10, rpd=50, scope="provider")},
            supports_tools=True,
            supports_stream=True,
            # Same rationale as nvidia_nim: bound a slow fallback lane's stall
            # rather than inherit the 60s strong default.
            timeout_fast_s=15.0,
            timeout_strong_s=22.0,
        ),
        ProviderSpec(
            name="nvidia_nim",
            base_url="https://integrate.api.nvidia.com/v1",
            key_env="NVIDIA_API_KEY",
            # 2026-07-27: the single configured model, meta/llama-3.3-70b-instruct,
            # was measured returning http_503 or timing out at 30s on almost every
            # attempt — and because NIM has NO token cap (only rpd=2000 shared),
            # it is the lane the pool leans on hardest once Groq/Cerebras exhaust
            # their daily token budgets. A dead primary there is what turned "the
            # free pool is slow" into "the free pool is gone": every rotation paid
            # 15-60s before falling through to local qwen2.5:7b.
            #
            # Measured twice, minutes apart, on this key:
            #   nvidia/nemotron-3-super-120b-a12b   1.1s / 3.8s   OK  OK
            #   openai/gpt-oss-120b                 4.3s / 30s    OK  timeout
            #   meta/llama-3.3-70b-instruct         30s  / 503    FAIL FAIL
            # So the consistent one leads and the other two stay as in-provider
            # fallbacks. Nothing is removed — this is an ordering change plus two
            # extra lanes on a provider that was effectively down.
            # 2026-09-20, re-measured on this key: the two fallbacks are gone.
            # meta/llama-3.3-70b-instruct returns HTTP 410 Gone, and NIM no
            # longer serves openai/gpt-oss-120b at all — it is absent from
            # /models, so sync_models dropped both on every boot and the tier
            # was silently one model deep. Several catalogue entries that DO
            # appear in /models have no inference endpoint behind them
            # (kimi-k2.6, nemotron-nano-3-30b-a3b and
            # llama-3.1-nemotron-ultra-253b-v1 all answer 404), and three more
            # (kimi-k3, deepseek-v4-flash-0731, nemotron-3.5-lightning-30b-a3b)
            # hit a 35s read timeout, so none of them is a fallback worth
            # paying a rotation for. Listing a model that cannot answer is
            # worse than a short list: it costs the turn a timeout each.
            models={
                T1_FAST: ["nvidia/nemotron-3-super-120b-a12b"],
                T2_BALANCED: ["nvidia/nemotron-3-super-120b-a12b"],
                T3_STRONG: ["nvidia/nemotron-3-super-120b-a12b"],
            },
            # Trial credits, not a published RPM table — modest budget so the
            # lane helps without burning credits on chitchat.
            caps={"*": Caps(rpm=20, rpd=2000, scope="provider")},
            supports_tools=True,
            supports_stream=True,
            # 2026-07-20 (latency audit): the pool's own client used the 20/60s
            # defaults, so a strong-tier NIM FALLBACK could stall a live turn
            # ~49s (measured) before walking down — a slow path my earlier
            # syrix_host_nvidia_nim=18s cut never reached (that governs only the
            # legacy router). Healthy NIM answers are p50 2.0s / p90 5.7s, so
            # 15/22s keeps every real answer while bounding the stall.
            timeout_fast_s=15.0,
            timeout_strong_s=22.0,
        ),
        # ── the two biggest free tiers on the market, dormant until keyed ──
        #
        # Added 2026-07-27 in answer to "make it feel unlimited". Both are
        # genuinely free, both are OpenAI-compatible, and both report
        # unavailable until a key is in .env — so this costs nothing today and
        # is a large amount of headroom the moment the user spends two minutes
        # creating one. They sit BELOW groq/cerebras/nim, so they change no
        # happy path; they are what answers when those are spent.
        # Gemini was a lane here and was removed on 2026-09-20 at the
        # owner's instruction: no Google in SYRIX. Nothing replaces it —
        # Cerebras, Groq and NVIDIA carry the cloud tiers and the local
        # model is the floor beneath them.
        ProviderSpec(
            name="mistral",
            base_url="https://api.mistral.ai/v1",
            key_env="MISTRAL_API_KEY",
            models={
                T1_FAST: ["mistral-small-latest"],
                T2_BALANCED: ["mistral-small-latest", "mistral-medium-latest"],
                T3_STRONG: ["mistral-medium-latest", "mistral-small-latest"],
            },
            # The free "Experiment" plan is rate-limited per second rather than
            # per day, with a very large monthly allowance.
            caps={"*": Caps(rpm=60, rpd=20000, tpm=400000)},
            supports_tools=True,
            supports_stream=True,
            timeout_fast_s=20.0,
            timeout_strong_s=45.0,
        ),
        ProviderSpec(
            name="ollama",
            base_url=f"{ollama_host}/v1",
            key_env="",  # local, no key
            models={
                T0_LOCAL: [ollama_model],
                T1_FAST: [ollama_model],
                T2_BALANCED: [ollama_model],
                T3_STRONG: [ollama_model],
            },
            caps={},  # unlimited
            supports_tools=False,
            supports_stream=True,
            timeout_fast_s=45.0,
            timeout_strong_s=90.0,
        ),
    ])


class ProviderRegistry:
    def __init__(self, cfg: Optional[Dict[str, Any]] = None, log_fn: Optional[Callable[[str], None]] = None):
        self._cfg = dict(cfg or {})
        self._log = log_fn or (lambda m: None)
        self.providers: Dict[str, ProviderSpec] = {p.name: p for p in _default_registry(self._cfg)}
        self._apply_config_overrides()

    def _apply_config_overrides(self) -> None:
        """config.json knobs: brain_pool_disabled_providers, brain_pool_models override."""
        disabled = self._cfg.get("brain_pool_disabled_providers") or []
        if isinstance(disabled, list):
            for name in disabled:
                spec = self.providers.get(str(name).strip().lower())
                if spec:
                    spec.enabled = False
        overrides = self._cfg.get("brain_pool_models") or {}
        if isinstance(overrides, dict):
            for pname, tiers in overrides.items():
                spec = self.providers.get(str(pname).strip().lower())
                if spec and isinstance(tiers, dict):
                    for tier, models in tiers.items():
                        if tier in ALL_TIERS and isinstance(models, list) and models:
                            spec.models[tier] = [str(m) for m in models]

    def available(self) -> List[ProviderSpec]:
        return [p for p in self.providers.values() if p.available()]

    def candidates(self, tier: str) -> List[tuple]:
        """Ordered (provider, model) pairs serving ``tier``. Ollama always last."""
        out: List[tuple] = []
        tail: List[tuple] = []
        for spec in self.providers.values():
            if not spec.available():
                continue
            for model in spec.models.get(tier, []):
                pair = (spec, model)
                (tail if spec.name == "ollama" else out).append(pair)
        return out + tail

    # ------------------------------------------------------------ model sync
    def sync_models(self, http_get: Callable[..., Any], max_age_s: float = 6 * 3600.0) -> None:
        """Validate configured model IDs against each provider's live /models.

        Network failures are non-fatal: defaults stay. A model missing from
        the live list is dropped (unless that would empty the tier).
        """
        for spec in self.available():
            if spec.name == "ollama":
                continue  # local list churns with pulls; trust config
            live = self._live_model_ids(spec, http_get, max_age_s)
            if not live:
                continue
            for tier, models in list(spec.models.items()):
                kept = [m for m in models if m in live]
                if kept and kept != models:
                    dropped = [m for m in models if m not in live]
                    self._log(f"[BRAIN_POOL] model_sync provider={spec.name} tier={tier} dropped={dropped}")
                    spec.models[tier] = kept
                elif not kept:
                    self._log(
                        f"[BRAIN_POOL] model_sync provider={spec.name} tier={tier} "
                        f"none_of_defaults_listed keep_defaults=true"
                    )

    def _live_model_ids(self, spec: ProviderSpec, http_get: Callable[..., Any], max_age_s: float) -> set:
        cache_path = os.path.join(syrix_paths.cache_dir(), f"brain_models_{spec.name}.json")
        try:
            st = os.stat(cache_path)
            if (time.time() - st.st_mtime) < max_age_s:
                with open(cache_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                return set(data.get("ids") or [])
        except Exception:
            pass
        ids: set = set()
        try:
            headers = {"Authorization": f"Bearer {spec.api_key()}"}
            headers.update(spec.extra_headers)
            resp = http_get(f"{spec.base_url}/models", headers=headers, timeout=10)
            if resp.status_code == 200:
                payload = resp.json()
                for entry in payload.get("data") or []:
                    mid = str(entry.get("id") or "").strip()
                    if mid:
                        ids.add(mid)
        except Exception as exc:
            self._log(f"[BRAIN_POOL] model_sync_fetch_failed provider={spec.name} err={type(exc).__name__}")
            return set()
        if ids:
            try:
                with open(cache_path, "w", encoding="utf-8") as fh:
                    json.dump({"ts": time.time(), "ids": sorted(ids)}, fh)
            except Exception:
                pass
        return ids


# Models that burn output budget on hidden reasoning before any visible text.
# With a tiny max_tokens they return EMPTY content — give them room to think.
REASONING_MODEL_HINTS = (
    "gpt-oss",
    "glm-4.7",
    "deepseek-r1",
    "qwen3",
    "thinking",
    "reasoning",
    "nemotron-3",
)
REASONING_MIN_TOKENS = 600


def is_reasoning_model(model: str) -> bool:
    low = str(model or "").lower()
    return any(h in low for h in REASONING_MODEL_HINTS)


def normalize_tier(tier: Optional[str]) -> str:
    t = str(tier or "").strip()
    if t in ALL_TIERS:
        return t
    return LEGACY_TIER_MAP.get(t.lower(), T2_BALANCED)
