"""Provider-independent model layer for Supercomputer.

Three roles, chosen independently:

    plan    fast general reasoning and tool planning
    code    writing and editing code
    vision  reading a page that genuinely cannot be understood from its DOM

Nothing is hard-coded to a vendor. Each role resolves at call time from config,
so the models can be changed from settings later without touching this file's
callers. The default for ``plan`` is the existing brain pool (Groq / Cerebras /
OpenRouter / GitHub Models / NIM / Ollama, already quota-managed and free), so
Supercomputer inherits SYRIX's provider health handling rather than duplicating
it.

**VRAM is the binding constraint** on this box: 8 GB, shared with whatever else
is resident. So local models are used one at a time — loading a new role
explicitly unloads the previous one. Ollama is reached through syrix_host_brain's
existing lifecycle helpers, which never adopt or kill a daemon the user started
themselves.

Never raises: every entry point returns ``(text, provider)`` with empty text on
failure, so a dead model degrades into an honest "I could not" rather than an
exception mid-task.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

import syrix_obs as obs

ROLE_PLAN = "plan"
ROLE_CODE = "code"
ROLE_VISION = "vision"
ROLES = (ROLE_PLAN, ROLE_CODE, ROLE_VISION)

# Config keys per role. Empty string means "use the brain pool" for plan, and
# "unavailable" for the local-only roles.
_ROLE_KEYS = {
    ROLE_PLAN: "supercomputer_model_plan",
    ROLE_CODE: "supercomputer_model_code",
    ROLE_VISION: "supercomputer_model_vision",
}

_LOCK = threading.RLock()
# The local model currently held resident, so we can unload before loading.
_RESIDENT: str = ""

# ── reasoning depth ────────────────────────────────────────────────────────
# Supercomputer's in-mode selector. It is NOT the normal HUD's model switcher
# and shares nothing with it — Spark/Zenith/Auto out there are unchanged. Here
# it chooses how hard the agent thinks per step: a fast tier keeps a long
# browsing loop responsive, a strong one is worth it for analysis and code.
DEPTH_FAST = "spark"
DEPTH_BALANCED = "auto"
DEPTH_DEEP = "zenith"
DEPTHS = (DEPTH_FAST, DEPTH_BALANCED, DEPTH_DEEP)

_DEPTH_TIER = {
    DEPTH_FAST: "T1_FAST",
    DEPTH_BALANCED: "T2_BALANCED",
    DEPTH_DEEP: "T3_STRONG",
}
# Deep by default, since 2026-07-26.
#
# The fast tier was tried as the default first, because a browsing task is a
# dozen-plus model calls and the tier multiplies. It was measurably worse at the
# job: on a three-way GPU price comparison the fast model re-ran the same search
# twelve times instead of opening a result and reading it, hit the step ceiling
# and produced nothing. A task that fails is not faster than one that takes
# longer. Balanced replaced it and was better.
#
# Deep replaces balanced because the tiers do not mean what the names suggest on
# THIS pool. T2's first lane is Groq gpt-oss-120b, capped at 12,000 tokens per
# minute — the exact ceiling that produced the "model stopped responding" and
# "lost the tool format" failures, since an agent step re-sends its transcript.
# T3's first lane is Cerebras zai-glm-4.7: a stronger model, 60,000 tokens per
# minute, served at ~2000 tok/s. It is both smarter AND faster here, and free.
# So "deep" is not a considered trade on this box; it is strictly better.
#
# Overridable per session from the mode switcher, and per install with
# ``supercomputer_default_depth``.
_DEFAULT_DEPTH = DEPTH_DEEP
_depth: str = _DEFAULT_DEPTH


# ── per-step grade ─────────────────────────────────────────────────────────
# Not every step in a task is the same job. Deciding which of forty links to
# open is real reasoning; re-reading the page after a scroll is not. Grading the
# step lets the routine majority run on the fast lane and keeps the strong lane
# for the moves that decide whether the task succeeds — which is cheaper on quota
# AND faster end to end than putting everything through one tier.
#
# The grade only ever LOWERS the tier below the chosen depth, never raises it
# above. The user picking "Deep" must not be quietly downgraded on a step this table
# happens to consider routine, so DEPTH_DEEP + GRADE_HARD is still T3 and
# DEPTH_FAST stays T1 throughout.
GRADE_ROUTINE = "routine"
GRADE_NORMAL = "normal"
GRADE_HARD = "hard"
GRADES = (GRADE_ROUTINE, GRADE_NORMAL, GRADE_HARD)

_TIER_ORDER = ("T1_FAST", "T2_BALANCED", "T3_STRONG")
_GRADE_CEILING = {
    GRADE_ROUTINE: "T1_FAST",
    GRADE_NORMAL: "T2_BALANCED",
    GRADE_HARD: "T3_STRONG",
}


def depth() -> str:
    with _LOCK:
        return _depth


def set_depth(value: str) -> str:
    """Pick the reasoning depth for this session. Unknown values are ignored."""
    global _depth
    v = str(value or "").strip().lower()
    if v in DEPTHS:
        with _LOCK:
            _depth = v
    return depth()


def reset_depth(config: Optional[Dict[str, Any]] = None) -> str:
    """Back to the configured default. Called on mode exit."""
    global _depth
    configured = str((config or {}).get("supercomputer_default_depth", "") or "").strip().lower()
    with _LOCK:
        _depth = configured if configured in DEPTHS else _DEFAULT_DEPTH
    return depth()


def tier_for_depth() -> str:
    return _DEPTH_TIER.get(depth(), _DEPTH_TIER[_DEFAULT_DEPTH])


def multibrain_enabled(config: Optional[Dict[str, Any]] = None) -> bool:
    return bool((config or {}).get("supercomputer_multibrain_enabled", True))


# Fraction of each lane's DAILY free budget this mode leaves for normal chat.
#
# The pool is shared. A long agentic task is twenty to sixty calls of several
# thousand tokens each, so without this one afternoon of Supercomputer spends
# the day's free tokens and the user's ordinary chat drops to a local 7B — which is
# the mode making the rest of SYRIX worse, and that is not a trade he agreed
# to. Normal chat passes no reserve and can still use every last token; this
# mode stops with a quarter of the day left.
def budget_reserve(config: Optional[Dict[str, Any]] = None) -> float:
    try:
        value = float((config or {}).get("supercomputer_budget_reserve", 0.25))
    except (TypeError, ValueError):
        return 0.25
    return min(max(value, 0.0), 0.9)


def _tier_has_remote_lane(tier: str, est_tokens: int = 4200,
                          config: Optional[Dict[str, Any]] = None) -> bool:
    """Does ``tier`` still have a non-local lane with budget? Unknown → True.

    Asked with the same reserve the calls themselves use, so the mode's view of
    "is there a lane" matches what it will actually be given.
    """
    try:
        from syrix_core_v2 import brain_pool as _bp

        return bool(_bp.has_remote_lane(tier, est_tokens, budget_reserve(config)))
    except Exception as exc:
        obs.report("supercomputer.models.lane_probe", exc, level="debug", tier=str(tier))
        return True


def tier_for(grade: str = GRADE_NORMAL,
             config: Optional[Dict[str, Any]] = None) -> str:
    """The tier one step should run on: the depth, capped by the step's grade.

    **Lowering is only an optimisation while the lower tier still has a real
    model behind it.** Measured on this pool 2026-07-27: T1_FAST's remote lanes
    were all spent (Cerebras gpt-oss-120b out of tokens-per-day, Groq's fast
    models blocked on a 6k tokens-per-minute cap that one 4.8k-token agent step
    nearly fills), so every step graded "routine" fell straight through to local
    qwen2.5:7b — 10-18s per call against Cerebras' 1-3s, and a 7B model picking
    the next of forty possible actions. The grade table was making the mode
    slower AND worse at exactly the steps it meant to make cheap.

    So the ceiling now has to be earned: a lower tier is used only if it can
    actually be served remotely. If it cannot, the step stays on the tier the user
    selected, which by construction is the one with the surviving lanes. This
    can only ever move a step UP relative to the old behaviour, and never above
    the chosen depth.
    """
    chosen = tier_for_depth()
    if not multibrain_enabled(config):
        return chosen
    ceiling = _GRADE_CEILING.get(str(grade or GRADE_NORMAL), "T2_BALANCED")
    try:
        lowered = _TIER_ORDER[min(_TIER_ORDER.index(chosen), _TIER_ORDER.index(ceiling))]
    except ValueError:
        return chosen
    if lowered == chosen:
        return chosen
    if not bool((config or {}).get("supercomputer_lane_aware_tier_enabled", True)):
        return lowered
    if _tier_has_remote_lane(lowered, config=config):
        return lowered
    obs.report("supercomputer.models.tier_held",
               RuntimeError(f"{lowered} has no remote lane; holding {chosen}"),
               level="debug", grade=str(grade), tier=chosen)
    return chosen


def _cfg(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return config or {}


# ── call accounting ────────────────────────────────────────────────────────
# Per-task tally of model calls by call site, so "why did that take four
# minutes" and "why is the day's free budget gone" have an answer that names
# the caller instead of a wall of identical pool lines.
_CALLS_LOCK = threading.RLock()
_calls: Dict[str, int] = {}


def _count_call(purpose: str) -> None:
    with _CALLS_LOCK:
        _calls[purpose] = _calls.get(purpose, 0) + 1


def call_counts() -> Dict[str, int]:
    with _CALLS_LOCK:
        return dict(_calls)


def reset_call_counts() -> Dict[str, int]:
    """Take and clear the tally — called at task start and read at task end."""
    global _calls
    with _CALLS_LOCK:
        prev, _calls = dict(_calls), {}
    return prev


def model_for(role: str, config: Optional[Dict[str, Any]] = None) -> str:
    """The configured model id for a role ("" = brain pool / unset)."""
    key = _ROLE_KEYS.get(str(role or ""), "")
    return str(_cfg(config).get(key, "") or "").strip() if key else ""


def ollama_host(config: Optional[Dict[str, Any]] = None) -> str:
    return str(_cfg(config).get("ollama_host", "http://127.0.0.1:11434") or "").rstrip("/")


# ── local model residency ──────────────────────────────────────────────────
def resident() -> str:
    with _LOCK:
        return _RESIDENT


def unload_local(config: Optional[Dict[str, Any]] = None) -> bool:
    """Release whatever local model Supercomputer loaded. Idempotent."""
    global _RESIDENT
    with _LOCK:
        model = _RESIDENT
        _RESIDENT = ""
    if not model:
        return False
    return _ollama_unload(model, config)


def _ollama_unload(model: str, config: Optional[Dict[str, Any]] = None) -> bool:
    """Ask Ollama to drop a model from VRAM (keep_alive: 0)."""
    body = json.dumps({"model": model, "keep_alive": 0}).encode("utf-8")
    req = urllib.request.Request(
        f"{ollama_host(config)}/api/generate",
        data=body, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        return True
    except Exception as exc:
        obs.report("supercomputer.models.unload", exc, model=model, level="debug")
        return False


def _make_resident(model: str, config: Optional[Dict[str, Any]] = None) -> None:
    """Ensure ``model`` is the only Supercomputer-loaded local model.

    8 GB will not hold two 7B models plus a vision model, so a role switch
    unloads before it loads. That costs a few seconds on switch and buys never
    hitting an out-of-memory stall mid-task.
    """
    global _RESIDENT
    with _LOCK:
        prev = _RESIDENT
        if prev == model:
            return
        _RESIDENT = model
    if prev and prev != model:
        _ollama_unload(prev, config)


def ensure_ollama(config: Optional[Dict[str, Any]] = None) -> bool:
    """Start Ollama only if it is not already up. Never adopts a user daemon."""
    try:
        import syrix_host_brain

        alive = getattr(syrix_host_brain, "ollama_alive", None)
        if callable(alive) and alive(ollama_host(config)):
            return True
        start = getattr(syrix_host_brain, "start_ollama_service_windows", None)
        if callable(start):
            start()
            return bool(callable(alive) and alive(ollama_host(config)))
    except Exception as exc:
        obs.report("supercomputer.models.ensure_ollama", exc)
    return False


def available_models(config: Optional[Dict[str, Any]] = None) -> List[str]:
    """Model ids Ollama currently has pulled. Empty when it is not running."""
    try:
        with urllib.request.urlopen(f"{ollama_host(config)}/api/tags", timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return sorted(str(m.get("name") or "") for m in (data.get("models") or []) if m.get("name"))
    except Exception as exc:
        obs.report("supercomputer.models.available", exc, level="debug")
        return []


# ── the call surface ───────────────────────────────────────────────────────
def chat(
    role: str,
    messages: List[Dict[str, str]],
    *,
    config: Optional[Dict[str, Any]] = None,
    request_id: str = "",
    max_tokens: int = 900,
    timeout_s: float = 45.0,
    routing_text: str = "",
    grade: str = GRADE_NORMAL,
    purpose: str = "",
) -> Tuple[str, str]:
    """Run a turn for one role. Returns ``(text, provider)``; "" text = failed.

    ``grade`` says how hard this particular step is (see :func:`tier_for`). It
    only affects the pool lanes; a role pinned to a specific local model uses
    that model whatever the grade, because there is nothing to choose between.

    ``purpose`` names the call site in the log. Model calls per task is the
    number that decides both how fast the mode feels and how long the free
    budgets last, and it was previously impossible to attribute from the pool's
    own logging — every line looked the same whether it came from the planner,
    a step, the critic or the final write-up.
    """
    cfg = _cfg(config)
    model = model_for(role, cfg)
    if purpose:
        _count_call(purpose)

    if not model:
        # No explicit model: plan goes to the shared pool, the others are only
        # meaningful as local models, so an unset one is simply unavailable.
        if role == ROLE_PLAN:
            return _pool_chat(messages, cfg=cfg, request_id=request_id,
                              max_tokens=max_tokens, timeout_s=timeout_s,
                              routing_text=routing_text, grade=grade)
        return "", ""

    if model.startswith("pool:"):
        return _pool_chat(messages, cfg=cfg, request_id=request_id,
                          max_tokens=max_tokens, timeout_s=timeout_s,
                          routing_text=routing_text, grade=grade)

    return _ollama_chat(model, messages, cfg=cfg, timeout_s=timeout_s,
                        max_tokens=max_tokens)


def vision(
    prompt: str,
    png_base64: str,
    *,
    config: Optional[Dict[str, Any]] = None,
    timeout_s: float = 90.0,
) -> Tuple[str, str]:
    """Describe a screenshot. Used only when the DOM genuinely cannot answer."""
    cfg = _cfg(config)
    model = model_for(ROLE_VISION, cfg)
    if not model:
        return "", ""
    _make_resident(model, cfg)
    payload = {
        "model": model,
        "prompt": str(prompt or ""),
        "images": [str(png_base64 or "")],
        "stream": False,
    }
    return _ollama_post(payload, cfg=cfg, timeout_s=timeout_s, model=model)


# ── pacing the free pool ───────────────────────────────────────────────────
# An agent step is one pool call, and on a healthy lane it returns in ~300ms —
# so the loop can issue thirty calls a minute without pausing. Every free lane
# has a per-minute token cap (Groq strong: 12k, Cerebras: 60k) and an agent
# transcript is ~4k tokens a call, so an unpaced loop exhausts every strong lane
# inside a handful of steps. What happens then is not a slowdown, it is a
# quality collapse: the pool rotates down to local qwen2.5:7b, and a 7B model
# driving a browser agent produces exactly the failures the user reported — reading
# the same page five times, never following the link, "I could not confirm it".
#
# So the mode paces itself, adaptively: no delay while the strong lanes are
# answering, a growing gap as soon as they start failing or handing off to a
# local model, decaying back to zero once they recover. Waiting two seconds for
# a good model beats answering instantly with a bad one, and it is FASTER in
# wall-clock terms than the recovery steps a weak answer costs.
_PACE_LOCK = threading.RLock()
_pace_last_ts: float = 0.0
_pace_gap_s: float = 0.0
_PACE_CEILING_S = 8.0
_PACE_FLOOR_S = 1.5
# Providers that cannot be trusted with an agent decision. Not a judgement on
# the models themselves — a 7B local model is a fine fallback for a chat reply,
# and a bad one for choosing the next of forty possible actions.
_WEAK_PROVIDERS = ("ollama", "local")


def _pace_wait(cfg: Dict[str, Any]) -> None:
    if not bool(cfg.get("supercomputer_pool_pacing_enabled", True)):
        return
    with _PACE_LOCK:
        gap = _pace_gap_s
        wait = gap - (time.time() - _pace_last_ts)
    if wait > 0:
        time.sleep(min(wait, _PACE_CEILING_S))


def _pace_note(provider: str, ok: bool) -> None:
    """Widen the gap when the pool is struggling, close it when it is not."""
    global _pace_last_ts, _pace_gap_s
    weak = (not ok) or any(str(provider or "").lower().startswith(p)
                           for p in _WEAK_PROVIDERS)
    with _PACE_LOCK:
        _pace_last_ts = time.time()
        if weak:
            _pace_gap_s = min(_PACE_CEILING_S, max(_PACE_FLOOR_S, _pace_gap_s * 1.7))
        else:
            _pace_gap_s = 0.0 if _pace_gap_s <= _PACE_FLOOR_S else _pace_gap_s * 0.7


def _reset_pacing() -> None:
    global _pace_last_ts, _pace_gap_s
    with _PACE_LOCK:
        _pace_last_ts = 0.0
        _pace_gap_s = 0.0


def is_weak_provider(provider: str) -> bool:
    """True for a lane that must not be trusted with an agent decision.

    Not a judgement on the model. A 7B local model is a perfectly good fallback
    for a chat reply and a bad one for choosing the next of forty possible
    actions on somebody's signed-in account.
    """
    low = str(provider or "").lower()
    return bool(low) and any(low.startswith(p) for p in _WEAK_PROVIDERS)


def pool_transport(tier, messages, *, max_tokens=900, timeout_s=45.0, **fallback_kw):
    """One pool call: v3's lanes, the transport normal chat uses.

    Returns ``(text, provider)``; ``("", "")`` when no lane answered, which
    every caller already treats as a failed step. v3's lanes race and rotate
    on their own and bring up the local model only when nothing else can
    answer, so the whole step budget is theirs. The v2 engine router remains
    only for a v3 that cannot be imported, so a broken v3 costs the mode its
    quality, not its function. Tests replace this one function to script the
    model.
    """
    try:
        from syrix_v3 import gateway as _v3_gateway
    except Exception as exc:
        obs.report("supercomputer.models.v3_import", exc)
        try:
            from syrix_core_v2 import engine_brains
        except Exception as exc2:
            obs.report("supercomputer.models.pool_import", exc2)
            return "", ""
        return engine_brains.chat(tier, messages, max_tokens=max_tokens,
                                  timeout_s=timeout_s, **fallback_kw)
    try:
        done = _v3_gateway.complete(
            list(messages),
            tier=str(tier or "T2_BALANCED"),
            max_tokens=int(max_tokens),
            timeout_s=float(timeout_s),
            allow_local_fallback=True,
        )
    except _v3_gateway.NoLaneAvailable:
        return "", ""
    return str(done.text or ""), str(done.provider or "")


def _pool_chat(messages, *, cfg, request_id, max_tokens, timeout_s, routing_text,
               grade=GRADE_NORMAL):
    # One lane attempt is bounded well below the step budget on the v2
    # fallback. Handing a dead provider the whole 60s
    # ``supercomputer_step_timeout_s`` — which is what happened before
    # 2026-07-27 — meant a single unhealthy lane cost the step a full minute
    # instead of rotating to a healthy one in a few seconds. Measured: NVIDIA
    # NIM burning 60s on ProviderTimeout, twice, inside one task.
    lane_s = float(cfg.get("supercomputer_lane_timeout_s", 18.0) or 18.0)

    def _call():
        _pace_wait(cfg)
        tier = tier_for(grade, cfg)
        # The cap exists to stop a sick REMOTE lane hogging the walk-down. When
        # no remote lane is left, the only lane is local — and a 7B model
        # genuinely needs tens of seconds for a long agent step (18.3s measured),
        # so capping it there would turn a slow answer into no answer.
        lane_cap = (min(lane_s, float(timeout_s))
                    if _tier_has_remote_lane(tier, config=cfg) else None)
        text, provider = pool_transport(
            tier, messages, max_tokens=max_tokens, timeout_s=timeout_s,
            cfg=cfg, request_id=request_id, routing_text=routing_text,
            lane_timeout_s=lane_cap, budget_reserve=budget_reserve(cfg),
        )
        _pace_note(provider, bool(text))
        return text, provider

    text, provider = _call()
    # The decisions that set a task's direction — the first move, a recovery
    # after something failed, writing the final answer — are worth one wait for
    # a real model. Once. If the pool is still degraded, a weak answer beats no
    # answer and the run carries on.
    if (grade == GRADE_HARD and text
            and any(str(provider or "").lower().startswith(p) for p in _WEAK_PROVIDERS)
            and bool(cfg.get("supercomputer_pool_pacing_enabled", True))):
        obs.report("supercomputer.models.weak_lane_retry",
                   RuntimeError(f"hard step answered by {provider}; waiting for a strong lane"),
                   level="debug", provider=str(provider))
        # Wait the current adaptive gap, not always the ceiling. A pool that is
        # only briefly busy recovers in a second or two, and sleeping eight
        # seconds regardless was adding most of a minute to an ordinary task
        # for no gain.
        with _PACE_LOCK:
            wait_s = max(_PACE_FLOOR_S, _pace_gap_s)
        time.sleep(min(wait_s, _PACE_CEILING_S))
        retry_text, retry_provider = _call()
        if retry_text and not any(str(retry_provider or "").lower().startswith(p)
                                  for p in _WEAK_PROVIDERS):
            return retry_text, retry_provider
    return text, provider


# Local models that reason before answering. Ollama puts their reasoning in a
# separate `thinking` field and returns `content` EMPTY, so a step asking one of
# these for a tool call gets nothing back and the whole step reads as a dead
# lane. Measured on qwen3:8b, 2026-07-28: 17.4s / 0 chars by default, 7.9s and
# correct JSON with thinking off. This lane is the local floor — it exists to be
# quick and literal, and a reasoning pass is not what it is for.
_REASONING_FAMILIES = ("qwen3", "deepseek-r1", "magistral", "phi4-reasoning",
                       "exaone-deep", "smallthinker", "gpt-oss")

_RE_THINK = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


def _is_reasoning_model(model: str) -> bool:
    name = str(model or "").strip().lower()
    return bool(name) and any(fam in name for fam in _REASONING_FAMILIES)


def _strip_thinking(text: str) -> str:
    t = str(text or "")
    if "<think>" in t.lower():
        t = _RE_THINK.sub("", t)
    return t.strip()


def _ollama_chat(model, messages, *, cfg, timeout_s, max_tokens):
    if not ensure_ollama(cfg):
        return "", ""
    _make_resident(model, cfg)
    payload = {
        "model": model,
        "messages": [{"role": str(m.get("role") or "user"),
                      "content": str(m.get("content") or "")} for m in messages],
        "stream": False,
        "options": {"num_predict": int(max_tokens)},
    }
    if _is_reasoning_model(model):
        payload["think"] = False

    def _post(pl):
        req = urllib.request.Request(
            f"{ollama_host(cfg)}/api/chat",
            data=json.dumps(pl).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=max(5.0, float(timeout_s))) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        try:
            data = _post(payload)
        except urllib.error.HTTPError:
            if "think" not in payload:
                raise
            # An Ollama too old to know the parameter. Ask again without it and
            # rely on the stripping below.
            payload.pop("think", None)
            data = _post(payload)
        msg = data.get("message") or {}
        text = _strip_thinking(msg.get("content"))
        if not text and str(msg.get("thinking") or "").strip() and "think" not in payload:
            # A reasoning model not matched by name: it thought, and said
            # nothing. One retry with thinking off beats reporting a dead lane.
            payload["think"] = False
            text = _strip_thinking((_post(payload).get("message") or {}).get("content"))
        return text, f"ollama:{model}"
    except Exception as exc:
        obs.report("supercomputer.models.ollama_chat", exc, model=model)
        return "", ""


def _ollama_post(payload, *, cfg, timeout_s, model):
    if not ensure_ollama(cfg):
        return "", ""
    if _is_reasoning_model(model) and "think" not in payload:
        payload = dict(payload)
        payload["think"] = False
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{ollama_host(cfg)}/api/generate",
        data=body, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=max(5.0, float(timeout_s))) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return _strip_thinking(data.get("response")), f"ollama:{model}"
    except Exception as exc:
        obs.report("supercomputer.models.ollama_generate", exc, model=model)
        return "", ""


def snapshot(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Settings-facing view: what each role is set to, and what is loadable."""
    cfg = _cfg(config)
    return {
        "roles": {r: model_for(r, cfg) or ("pool" if r == ROLE_PLAN else "") for r in ROLES},
        "resident": resident(),
        "available": available_models(cfg),
        "ollama_host": ollama_host(cfg),
        "depth": depth(),
        "depths": list(DEPTHS),
        "multibrain": multibrain_enabled(cfg),
        "tiers": {g: tier_for(g, cfg) for g in GRADES},
    }


def _reset_for_tests() -> None:
    global _RESIDENT, _depth
    with _LOCK:
        _RESIDENT = ""
        _depth = _DEFAULT_DEPTH
    _reset_pacing()


__all__ = [
    "ROLE_PLAN", "ROLE_CODE", "ROLE_VISION", "ROLES",
    "DEPTH_FAST", "DEPTH_BALANCED", "DEPTH_DEEP", "DEPTHS",
    "GRADE_ROUTINE", "GRADE_NORMAL", "GRADE_HARD", "GRADES",
    "depth", "set_depth", "reset_depth", "tier_for_depth", "tier_for",
    "multibrain_enabled",
    "chat", "vision", "model_for", "available_models", "ensure_ollama",
    "unload_local", "resident", "snapshot",
]
