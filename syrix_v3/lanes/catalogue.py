"""Which models v3 can think on, and what each one was measured doing.

Data only: the lane catalogue, the order each role tries lanes in, and the
small types every other part of ``lanes`` shares. Changing which model
answers is an edit to this file and nothing else.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

FAST = "fast"
ANSWER = "answer"
DEEP = "deep"
ROLES = (FAST, ANSWER, DEEP)

# The tiers v3's callers already speak, mapped onto roles. Kept so the loop,
# compaction, rewriting and sub-agents did not have to change what they ask
# for when the transport underneath them did.
TIER_ROLE = {
    "T0_LOCAL": ANSWER,
    "T1_FAST": FAST,
    "T2_BALANCED": ANSWER,
    "T3_STRONG": DEEP,
}

LOCAL_KEY = "local"


@dataclass(frozen=True)
class Lane:
    """One model at one provider, with its measured behaviour."""

    key: str
    provider: str
    model: str
    base_url: str
    key_env: str
    tools: bool
    # Published per-model allowances. 0 = none published.
    tpm: int = 0
    rpd: int = 0
    tpd: int = 0
    # Seconds without answer text before the next lane is started beside
    # this one. Set a little above the measured time to first token.
    hedge_s: float = 3.0
    # Seconds the connection may sit silent before it is abandoned.
    stall_s: float = 25.0
    # Request fields, per role, that set how much the model thinks.
    thinking: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # Some providers mishandle ``stream_options``; only send it where it works.
    usage_in_stream: bool = False
    local: bool = False
    extra_headers: Dict[str, str] = field(default_factory=dict)
    # Whether asking for "medium"/"high" reasoning switches this model's
    # thinking on. True only where thinking is fast enough to be worth it:
    # Super thinks in about six seconds, Ultra in about ninety.
    thinks_on_request: bool = False


_GROQ = "https://api.groq.com/openai/v1"
_NVIDIA = "https://integrate.api.nvidia.com/v1"
_OPENROUTER = "https://openrouter.ai/api/v1"


def _gpt_oss(fast: str, answer: str, deep: str) -> Dict[str, Dict[str, Any]]:
    return {
        FAST: {"reasoning_effort": fast},
        ANSWER: {"reasoning_effort": answer},
        DEEP: {"reasoning_effort": deep},
    }


def _nemotron(fast: bool, answer: bool, deep: bool) -> Dict[str, Dict[str, Any]]:
    def kw(on: bool) -> Dict[str, Any]:
        return {"chat_template_kwargs": {"enable_thinking": bool(on)}}
    return {FAST: kw(fast), ANSWER: kw(answer), DEEP: kw(deep)}


# Measured on this account, 2026-09-23, with a 2,750-token evidence prompt and
# a native tool-call probe. Numbers are what the lane did, not what its
# provider advertises:
#
#   groq  gpt-oss-120b  (low)      first token 0.6s, total 1.5s, tools yes
#   groq  gpt-oss-20b   (low)      first token 0.5s, total 0.9s, tools yes
#   nvidia nemotron-3-super (off)  total 1.3s streamed, tools NO (HTTP 500)
#   nvidia nemotron-3-ultra (off)  first token 3.7s, total 22s, tools yes
#   nvidia nemotron-3-ultra (on)   first token 92s — never used thinking on
#   nvidia glm-5.3                 first token 8.3s, total 23.5s, tools yes
#   openrouter free pool           50 requests a day on this account
#
# Groq's gpt-oss allowance is 8,000 tokens a minute and 1,000 requests a day
# per model, with max_tokens charged at admission. NVIDIA publishes a
# per-key request rate and no token allowance.
#
# Measured dead on the same day and deliberately absent: cerebras (HTTP 402 on
# every model), nvidia deepseek-v4.1-flash, kimi-k3, glm-5.3-flash and
# mistral-nemotron (read timeouts at 45-60s), nvidia kimi-k2.6,
# mistral-large-2 and palmyra (HTTP 404 on inference while listed in
# /models), groq qwen3.8-27b (rejects a 1,200-token reply as too large).
CATALOGUE: Dict[str, Lane] = {
    lane.key: lane for lane in (
        Lane(
            key="groq:gpt-oss-120b", provider="groq", model="openai/gpt-oss-120b",
            base_url=_GROQ, key_env="GROQ_API_KEY", tools=True,
            tpm=8000, rpd=1000, tpd=200000, hedge_s=2.5, stall_s=20.0,
            thinking=_gpt_oss("low", "low", "medium"), usage_in_stream=True,
        ),
        Lane(
            key="nvidia:nemotron-3-super", provider="nvidia",
            model="nvidia/nemotron-3-super-120b-a12b",
            base_url=_NVIDIA, key_env="NVIDIA_API_KEY", tools=False,
            hedge_s=3.5, stall_s=25.0,
            thinking=_nemotron(False, False, True), thinks_on_request=True,
        ),
        Lane(
            key="groq:gpt-oss-20b", provider="groq", model="openai/gpt-oss-20b",
            base_url=_GROQ, key_env="GROQ_API_KEY", tools=True,
            tpm=8000, rpd=1000, tpd=200000, hedge_s=2.5, stall_s=20.0,
            thinking=_gpt_oss("low", "low", "medium"), usage_in_stream=True,
        ),
        Lane(
            key="nvidia:nemotron-3-ultra", provider="nvidia",
            model="nvidia/nemotron-3-ultra-550b-a55b",
            base_url=_NVIDIA, key_env="NVIDIA_API_KEY", tools=True,
            hedge_s=8.0, stall_s=40.0,
            thinking=_nemotron(False, False, False),
        ),
        Lane(
            key="nvidia:glm-5.3", provider="nvidia", model="z-ai/glm-5.3",
            base_url=_NVIDIA, key_env="NVIDIA_API_KEY", tools=True,
            hedge_s=14.0, stall_s=45.0,
        ),
        Lane(
            key="openrouter:nemotron-3-ultra-free", provider="openrouter",
            model="nvidia/nemotron-3-ultra-550b-a55b:free",
            base_url=_OPENROUTER, key_env="OPENROUTER_API_KEY", tools=False,
            rpd=50, hedge_s=10.0, stall_s=45.0,
            extra_headers={"HTTP-Referer": "https://syrix.local", "X-Title": "SYRIX"},
        ),
    )
}

# The order lanes are tried in, per role. Quality first; the hedge timers are
# what keep a slow first choice from costing the turn its speed.
ORDER: Dict[str, List[str]] = {
    FAST: ["groq:gpt-oss-20b", "nvidia:nemotron-3-super", "groq:gpt-oss-120b",
           "nvidia:nemotron-3-ultra"],
    # GLM-5.3 ahead of Ultra: re-measured at 3.6s with working tool calls,
    # where Ultra takes 22s and so held the whole answer budget whenever the
    # two lanes ahead of it were out.
    ANSWER: ["groq:gpt-oss-120b", "nvidia:nemotron-3-super", "groq:gpt-oss-20b",
             "nvidia:glm-5.3", "nvidia:nemotron-3-ultra", "openrouter:nemotron-3-ultra-free"],
    DEEP: ["groq:gpt-oss-120b", "nvidia:nemotron-3-ultra", "nvidia:glm-5.3",
           "nvidia:nemotron-3-super", "groq:gpt-oss-20b",
           "openrouter:nemotron-3-ultra-free"],
}

# How long a role will wait for its best lane's minute to refill rather than
# settle for the next one. A second is cheaper than a weaker answer; ten are
# not, for a quick question.
_MAX_WAIT_S = {FAST: 0.0, ANSWER: 1.5, DEEP: 8.0}

# Default ceiling for one call when the caller gives none.
_DEFAULT_TIMEOUT_S = {FAST: 20.0, ANSWER: 60.0, DEEP: 180.0}

# Characters per token for admission arithmetic. Deliberately a little
# pessimistic for English prose so a request is never admitted into a minute
# that cannot hold it.


class NoLaneAvailable(RuntimeError):
    """No lane could answer: every one is spent, cooling, dead or absent."""


@dataclass
class Result:
    """One completed call."""

    text: str = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    # The answer exactly as streamed, before trimming — a continuation must
    # join at the real token boundary, which trimming would move.
    raw_text: str = ""
    lane: str = ""
    provider: str = ""
    model: str = ""
    finish_reason: str = ""
    latency_ms: float = 0.0
    first_token_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    rotations: List[str] = field(default_factory=list)
    tools_used: bool = False


class LaneError(RuntimeError):
    def __init__(self, reason: str, *, status: int = 0, retry_after_s: float = 0.0,
                 too_large: bool = False, tools_broken: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.retry_after_s = retry_after_s
        self.too_large = too_large
        self.tools_broken = tools_broken
