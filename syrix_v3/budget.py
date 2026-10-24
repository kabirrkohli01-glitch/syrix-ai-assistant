"""The whole difference between Spark, Zenith and Auto.

Three modes, one loop. What separates them is how long they may take, how many
tool rounds they get, how much evidence they gather and which provider tier
they think on — not which code path they run. Keeping that difference in one
table is what stops a mode quietly doing another mode's work, which is the
defect this replaces: the previous runtime ran a deep pass in-band after the
fast one had already answered and shipped whichever finished first, so a fast
turn paid for a deep turn's latency and the user got neither reliably.

Nothing here caps the length of an answer. A budget bounds effort, never
words: a turn ends when the model is finished or the deadline expires, and an
expired deadline still ships the best answer available.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict

SPARK = "spark"
ZENITH = "zenith"
AUTO = "auto"

MODES = (SPARK, ZENITH, AUTO)

# brain_pool tiers.
#
# Spark is on T2_BALANCED, not T1_FAST, and that is deliberate. Speed here
# comes from the hardware and from doing everything at once, not from a
# smaller model: Cerebras serves gpt-oss-120b at roughly two thousand tokens
# a second with a million tokens a day, so the balanced tier is both the
# faster lane and the better one. T1_FAST would drop a Groq turn onto an 8B
# model with a 6,000-token minute budget, which is where "fast" starts
# meaning "worse" — the thing Spark is explicitly not allowed to be.
_T2_BALANCED = "T2_BALANCED"
_T3_STRONG = "T3_STRONG"


@dataclass(frozen=True)
class Budget:
    """What one mode is allowed to spend."""

    mode: str
    tier: str
    # Wall clock for the whole turn. 0 means no ceiling. On expiry the loop
    # stops gathering and answers from what it already has; it never returns
    # an apology in place of an answer.
    deadline_s: float
    # Held back from the deadline for the model call that writes the answer.
    # Without it, gathering spends the whole budget and the turn overruns on
    # the one step that cannot be skipped: tools stop at the gathering
    # deadline, the loop runs to the real one.
    answer_reserve_s: float
    # How long evidence may be gathered BEFORE the first model call. This is
    # where a fast turn's breadth comes from: searching and reading start at
    # t=0 rather than after a round trip spent deciding to.
    prefetch_s: float
    # Tool rounds before the model must write the answer.
    max_rounds: int
    # Search queries fired in one fan-out, and page bodies read from the
    # results. Both are concurrency ceilings, not sequential counts.
    max_queries: int
    max_sources: int
    # Per-call reply ceiling handed to the provider. High enough that the
    # model decides where an answer ends; the transport handles a provider
    # that stops early by continuing it.
    max_tokens: int
    # History and evidence handed to the lane. Fast lanes have small
    # per-minute token budgets; a strong lane can carry a real dossier.
    prompt_chars: int
    # Whether a question that splits into independent parts may be farmed out.
    sub_agents: bool
    temperature: float

    # How hard this particular question looked. Only Spark varies; see
    # `for_mode`.
    effort: str = "normal"
    # How hard a reasoning model thinks, when the turn needs more than its
    # effort implies: "" follows the effort, "high" is for a problem to work
    # out, where thinking time is what a reasoning model's accuracy is bought
    # with.
    reasoning: str = ""

    def with_deadline(self, seconds: float) -> "Budget":
        return replace(self, deadline_s=float(seconds))


_BUDGETS: Dict[str, Budget] = {
    # Fast because it runs everything at once and stops waiting, not because
    # it thinks less or says less. Spark is the fast EXPERT: it is expected
    # to handle hard questions, and it is given room to.
    SPARK: Budget(
        mode=SPARK,
        tier=_T2_BALANCED,
        # Measured, not chosen: the lanes return twenty sources in about 1.5s
        # and opening five of them takes about 2s more, which leaves room for
        # a model call on a lane that serves thousands of tokens a second.
        # A question needing no evidence skips all of that and lands in well
        # under a second.
        deadline_s=8.0,
        answer_reserve_s=2.5,
        prefetch_s=4.0,
        # Three, not two. Two allowed search-then-answer and nothing else, so
        # a Spark turn could never search, open what it found, and then
        # answer — the one shape most research questions need. Capping it
        # there was making Spark worse at hard questions on purpose, which
        # is the opposite of what a fast expert is for.
        max_rounds=3,
        max_queries=6,
        max_sources=20,
        # These two are ONE number as far as a free lane is concerned, and
        # getting that wrong made v3 slow and weak at the same time. The pool
        # prices a request at `prompt_chars / 3.5 + max_tokens` and refuses any
        # lane whose per-minute token budget cannot cover it, holding back a
        # 10% safety margin. Groq's lanes cap at 6,000 and 12,000 tokens a
        # minute, so the real ceilings are 5,400 and 10,800.
        #
        # At 14,000 chars and 4,096 reply tokens a request was priced at 8,096:
        # unaffordable on the 6,000-tpm lanes entirely, and good for only ONE
        # call on the 12,000 ones. The second call of any turn therefore found
        # every Groq lane unaffordable and rotated out of the provider — and
        # with Cerebras answering 402, it landed on the local 7B model.
        # Measured live: six of eight turns answered on ollama, taking 17-44s,
        # and the compound-interest question came back wrong by £164.
        #
        # At 11,500 and 2,048 the price is 5,333 — inside every working lane,
        # twice each. Ten affordable calls a minute instead of one.
        # 13,000 and 1,600 prices a request at 5,314 — still inside every
        # working lane twice over (see the affordability test), while leaving
        # real room for the conversation. At 11,500 the persona and the evidence
        # were most of the prompt, and `budget_messages` fits a request by
        # keeping every system message and dropping the OLDEST ordinary turns:
        # so the thing that got dropped was the letter the user had pasted three
        # messages earlier, and SYRIX told the user "I don't see a cover letter or
        # any previous messages from you". 1,600 reply tokens is about 6,400
        # characters; the longest answer measured was 2,600.
        max_tokens=1600,
        prompt_chars=13000,
        sub_agents=False,
        temperature=0.5,
    ),
    # No ceiling. Zenith is chosen when the answer matters more than the wait,
    # and a deep turn that gives up halfway is worth less than no deep turn.
    ZENITH: Budget(
        mode=ZENITH,
        tier=_T3_STRONG,
        deadline_s=0.0,
        answer_reserve_s=0.0,
        # Zenith prefetches too, so its first round already has a footing,
        # and then keeps its tools to go wherever the answer leads.
        prefetch_s=25.0,
        max_rounds=12,
        max_queries=12,
        max_sources=60,
        # Zenith was priced out of every cloud lane it had, and the reason is
        # the one already written down for HARD effort above: a reply
        # RESERVATION is charged in full against a per-minute token budget
        # whether or not it is used. At 40,000 chars and 8,192 tokens a request
        # priced at 19,620, which no Groq lane can accept at any hour of any
        # day — the 12,000-tpm lane tops out at 10,800 and the 6,000-tpm one at
        # 5,400, below the reservation on its own. With Cerebras answering 402
        # that left exactly one usable lane, and an 8,192-token generation
        # cannot finish inside that lane's stall ceiling, so every deep turn
        # measured fell through to the local 7B: 48-68s, one model call, no
        # tools, no sub-agents. Deep mode was running as a slower Spark.
        #
        # 28,000 and 2,048 price at 10,048, inside the strong lane's ceiling
        # with room to spare, and 2,048 tokens is about 8,000 characters
        # against a longest-measured deep answer of 4,679. Depth here is
        # bought with rounds, sources, sub-agents and an unbounded deadline —
        # all untouched — never with a larger reservation for one reply.
        max_tokens=2048,
        prompt_chars=28000,
        sub_agents=True,
        temperature=0.4,
    ),
}


# How much effort a question looked like it needed. This is the whole of
# "fast when it can be fast, deep when it needs to be deep", and it is a
# scale applied to ONE budget rather than a second mode: same loop, same
# tier, same tools. Nothing here makes Spark less capable — the ceiling
# moves, the floor does not.
LIGHT = "light"
NORMAL = "normal"
HARD = "hard"
EFFORTS = (LIGHT, NORMAL, HARD)

# Multipliers on Spark's base, except `rounds`, which is an ABSOLUTE count.
# It was a multiplier and that was a bug: the hard tier read 2, which is an
# integer, and set two rounds rather than doubling three — so the hardest
# questions got FEWER rounds than ordinary ones. A field that means two
# things depending on its value is a field that will be read wrong.
_EFFORT_SCALE = {
    # A lookup or a chat turn: nothing to work out, and sub-second when
    # there is nothing to look up.
    #
    # Gathering is NOT scaled down here, and that is the correction. A turn
    # that needs no evidence pays nothing for this window — triage returns
    # early and prefetch never runs — so scaling it only ever shortened the
    # turns that do gather, where opening a page costs what it costs however
    # simple the question is. At 0.6 the window was about a second of
    # searching and a second of reading, and `rounds: 1` withdrew the tools
    # before the first call, so a light turn that found nothing but titles
    # had no way to go and open one. Asked about a small company it duly
    # replied that the search had returned only titles and none of the pages
    # had been opened — which is a description of the search, not an answer.
    #
    # Two rounds, not one: the second is spent only if the model actually
    # asks for a tool, so a light turn that needs nothing still ends in one
    # call. The deadline is a ceiling, not a cost.
    LIGHT: {"deadline": 0.7, "prefetch": 1.0, "rounds": 2,
            "sources": 0.6, "tokens": 1.0},
    NORMAL: {"deadline": 1.0, "prefetch": 1.0, "rounds": 3,
             "sources": 1.0, "tokens": 1.0},
    # Still Spark, still one lane, still the same loop — just not rushed.
    # Six rounds is room to search, open what it found, follow one thread
    # further and then answer. A hard question answered properly in
    # eighteen seconds beats a shallow one in six, and it is still nothing
    # like Zenith.
    # `tokens` is deliberately 1.0. Scaling the REPLY RESERVATION up by half
    # on the hardest questions priced them out of every free lane at once —
    # 6,144 reply tokens on top of the prompt came to 10,144, over the 10,800
    # ceiling as soon as a turn needed a second call — so the questions that
    # most needed a good model were the ones guaranteed to fall through to the
    # local 7B. Hard effort buys TIME, ROUNDS and SOURCES, which is what a
    # hard question actually needs; it never bought better answers by
    # reserving room for longer ones.
    HARD: {"deadline": 2.2, "prefetch": 1.6, "rounds": 6,
           "sources": 1.5, "tokens": 1.0},
}


def for_mode(mode: str, effort: str = NORMAL) -> Budget:
    """The budget for a resolved mode, scaled by how hard the question looked.

    Auto is not a budget — it is a decision that produces one — so asking for
    it is a programming error rather than a default worth guessing at.

    Only Spark scales. Zenith is already "take as long as it needs", and
    scaling something with no deadline down would just be Spark with extra
    steps.
    """
    key = str(mode or "").strip().lower()
    if key == AUTO:
        raise ValueError("auto must be resolved to spark or zenith before a budget is taken")
    base = _BUDGETS.get(key, _BUDGETS[SPARK])
    if key != SPARK or effort == NORMAL or effort not in EFFORTS:
        return base if effort == NORMAL else replace(base, effort=NORMAL)

    scale = _EFFORT_SCALE[effort]
    return replace(
        base,
        effort=effort,
        deadline_s=round(base.deadline_s * scale["deadline"], 1),
        prefetch_s=round(base.prefetch_s * scale["prefetch"], 1),
        max_rounds=int(scale["rounds"]),
        max_sources=int(base.max_sources * scale["sources"]),
        max_tokens=int(base.max_tokens * scale["tokens"]),
    )


def normalise(mode: str) -> str:
    """Map anything the wire can carry onto a known mode, defaulting to Spark.

    Spark is the default because the wire says so: the HUD sends no model
    marker at all for a Spark turn, so an absent or unrecognised mode is a
    Spark turn.
    """
    key = str(mode or "").strip().lower()
    return key if key in MODES else SPARK
