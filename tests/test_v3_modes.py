"""Modes stay in their lane, and Auto decides once.

The defect these exist to prevent is specific and was measured. When the
old retrieval gate marked a turn deep, the deep engine ran in-band after
the fast one had already answered and whichever finished first was
shipped. A fast turn paid a deep turn's latency, a deep turn could be
beaten to the screen by a shallow answer, and one two-subject comparison
went through thirteen model calls.
"""
from __future__ import annotations

import pytest

from syrix_v3 import budget, context, router
from syrix_v3.budget import AUTO, SPARK, ZENITH


# ── the wire contract ─────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("[[SYRIX_MODEL:zenith]] research this", ZENITH),
    ("[[SYRIX_MODEL:auto]] what is it", AUTO),
    ("[[SYRIX_MODEL:supercomputer]] do a thing", SPARK),  # not v3's to run
    ("no marker at all", SPARK),
    ("", SPARK),
])
def test_the_marker_decides_the_mode(text, expected):
    """Absence of a marker IS the Spark wire format, not a missing value."""
    assert budget.normalise(context.read_marker(text)) == expected


def test_the_marker_never_reaches_the_model():
    assert context.strip_markers("[[SYRIX_MODEL:zenith]] what is bitcoin") \
        == "what is bitcoin"
    assert context.strip_markers("[[SYRIX_MODE:deep_research]] [[SYRIX_MODEL:auto]] hi") \
        == "hi"


# ── budgets are the only difference between modes ─────────────────────────

def test_spark_is_bounded_and_zenith_is_not():
    spark, zenith = budget.for_mode(SPARK), budget.for_mode(ZENITH)
    assert spark.deadline_s > 0, "Spark without a deadline is not Spark"
    assert zenith.deadline_s == 0, "a deep turn that gives up halfway is worth less than none"
    assert spark.max_rounds < zenith.max_rounds
    assert spark.max_sources < zenith.max_sources


def test_only_zenith_may_split_work():
    assert budget.for_mode(SPARK).sub_agents is False
    assert budget.for_mode(ZENITH).sub_agents is True


def test_spark_keeps_time_back_for_the_answer():
    """Gathering that spends the last second leaves nothing for the one
    step that cannot be skipped."""
    spark = budget.for_mode(SPARK)
    assert 0 < spark.answer_reserve_s < spark.deadline_s
    assert spark.prefetch_s <= spark.deadline_s - spark.answer_reserve_s + 0.5


def test_no_budget_caps_the_length_of_an_answer():
    """A budget bounds effort, never words.

    ``max_tokens`` is a reply reservation, not a target, and it has to clear
    the longest answer a mode actually writes rather than being as large as
    possible. 1,600 tokens is roughly 6,400 characters; the longest Spark
    answer measured was about 2,600. Going higher does not lengthen an
    answer — it spends prompt budget the conversation needs, and the
    affordability test below says what it costs.
    """
    for mode in (SPARK, ZENITH):
        plan = budget.for_mode(mode)
        fields = plan.__dataclass_fields__
        assert not any("max_chars" in f or "max_words" in f for f in fields)
        assert plan.max_tokens >= 1600


# The free lanes' per-minute token budgets, and the pool's own pricing, from
# syrix_core_v2.brain_pool.ledger: a request costs `prompt_chars / 3.5 +
# max_tokens`, and a lane is refused unless its cap covers that with a 10%
# margin held back.
_SAFETY_MARGIN = 0.10
_SMALLEST_WORKING_TPM = 6000     # groq's cheap lanes
_MAIN_TPM = 12000                # groq gpt-oss-120b


def _request_price(plan) -> float:
    return plan.prompt_chars / 3.5 + plan.max_tokens


@pytest.mark.parametrize("effort", [budget.LIGHT, budget.NORMAL, budget.HARD])
def test_a_spark_turn_is_affordable_on_the_free_lanes(effort):
    """Spark has to fit the lanes it claims to run on.

    This is the regression that made v3 slow and weak at once. At 14,000
    prompt characters and a 4,096-token reply reservation a request priced out
    at 8,096 — above the 5,400 a 6,000-tpm lane will allow, and good for only
    one call on a 12,000-tpm lane, so the second call of any turn rotated out
    of Groq entirely. With Cerebras returning 402 the turn landed on the local
    7B model: six of eight measured live turns answered there, in 17-44s, and
    the compound-interest one was wrong by £164.

    Hard effort was worst of all, because it scaled the reservation to 6,144 —
    so the questions that most needed a good model were the likeliest to get
    the worst one.
    """
    plan = budget.for_mode(SPARK, effort)
    price = _request_price(plan)
    assert price <= _SMALLEST_WORKING_TPM * (1 - _SAFETY_MARGIN), (
        f"{effort} prices at {price:.0f} tokens, locking out the cheap lanes")
    assert price * 2 <= _MAIN_TPM * (1 - _SAFETY_MARGIN), (
        f"{effort} cannot make two calls a minute on the main lane")


def test_a_zenith_turn_is_affordable_on_the_strong_lane():
    """Deep mode has to reach a cloud lane, or it is only a slower Spark.

    At 40,000 prompt characters and an 8,192-token reply reservation a Zenith
    request priced at 19,620 tokens. No Groq lane accepts that at any hour of
    any day: the 12,000-tpm lane allows 10,800 and the 6,000-tpm one 5,400,
    which the reservation exceeded on its own. With Cerebras answering 402
    that left a single usable provider, and an 8,192-token generation cannot
    finish inside that provider's stall ceiling — so every measured deep turn
    fell to the local 7B, taking 48-68s and producing one model call, no tool
    rounds and no sub-agents.

    Zenith is allowed to be expensive per turn; it is not allowed to be
    unaffordable per CALL, because a lane refuses on the price of one request.
    Depth comes from rounds, sources, sub-agents and an unbounded deadline.
    """
    plan = budget.for_mode(ZENITH)
    price = _request_price(plan)
    assert price <= _MAIN_TPM * (1 - _SAFETY_MARGIN), (
        f"zenith prices at {price:.0f} tokens; the strong lane allows "
        f"{_MAIN_TPM * (1 - _SAFETY_MARGIN):.0f}")


def test_zenith_still_outweighs_spark_where_depth_comes_from():
    """Making Zenith affordable must not quietly turn it into Spark."""
    spark = budget.for_mode(SPARK, budget.NORMAL)
    zenith = budget.for_mode(ZENITH)
    assert zenith.max_rounds > spark.max_rounds
    assert zenith.max_sources > spark.max_sources
    assert zenith.max_queries > spark.max_queries
    assert zenith.prompt_chars > spark.prompt_chars
    assert zenith.sub_agents and not spark.sub_agents
    assert zenith.deadline_s == 0.0  # no ceiling


def test_hard_effort_buys_time_and_sources_not_a_bigger_reservation():
    base = budget.for_mode(SPARK, budget.NORMAL)
    hard = budget.for_mode(SPARK, budget.HARD)
    assert hard.deadline_s > base.deadline_s
    assert hard.max_rounds > base.max_rounds
    assert hard.max_sources > base.max_sources
    # The one thing it must NOT scale: see the affordability test above.
    assert hard.max_tokens == base.max_tokens


@pytest.mark.parametrize("question", [
    "top 10 unis in the uk",
    "what are the 10 biggest companies in the world right now",
    "best 20 laptops",
    "top ten uk universities",
])
def test_a_ranked_list_is_never_the_lightest_turn(question):
    """Question length is the wrong proxy for this one shape.

    "top 10 unis in the uk" is six words, so it scored as the lightest turn
    available — 0.7x the deadline and 0.6x the sources — while the answer it
    asks for is ten separately-evidenced entries. Measured on that budget the
    turn found twelve pages, opened two before the gathering window closed,
    and filled the remaining eight ranks from memory.
    """
    decision = router.decide(question)
    assert decision.effort == budget.HARD, (
        f"{question!r} routed as {decision.effort}; a list of this size "
        "cannot be covered by the pages one light turn opens")


@pytest.mark.parametrize("question,expected", [
    ("top 3 podcasts about cycling", budget.NORMAL),  # short list, not breadth
    ("what is the capital of france", budget.LIGHT),  # unchanged
    ("whats the time", budget.LIGHT),
])
def test_the_list_signal_does_not_swallow_everything_else(question, expected):
    assert router.decide(question).effort == expected


def test_asking_for_auto_s_budget_is_an_error():
    """Auto is a decision that produces a budget, not a budget."""
    with pytest.raises(ValueError):
        budget.for_mode(AUTO)


# ── the router decides once, and explains itself ──────────────────────────

@pytest.mark.parametrize("question", [
    "what is the capital of france",
    "whats bitcoin at",
    "who wrote nineteen eighty-four",
    "quick — is the market open",
    "just say yes or no, would you buy btc",
    "thanks",
])
def test_ordinary_questions_stay_fast(question):
    assert router.decide(question).mode == SPARK


@pytest.mark.parametrize("question", [
    "do a deep dive on where the ai chip market stands and who is winning",
    "research whether a level 3 apprenticeship or a degree is the better "
    "route into software in the uk, and what the pay looks like after five years",
    "write me a report on the uk housing market: prices, rates, and what "
    "happens if the base rate moves again",
    "why did iren move so much this week, what caused it, and what does it "
    "mean for the rest of the sector",
])
def test_questions_that_deserve_depth_get_it(question):
    decision = router.decide(question)
    assert decision.mode == ZENITH, decision.why()
    assert decision.reasons, "went deep without being able to say why"


def test_asking_for_it_quickly_overrides_everything():
    """An explicit instruction about how long to take wins."""
    decision = router.decide(
        "quick answer only — do a deep dive comparison of rust and go "
        "with pros and cons and a full breakdown")
    assert decision.mode == SPARK
    assert "quickly" in decision.why()


def test_one_signal_alone_is_not_enough():
    """A question can say "compare" and still be a one-liner."""
    assert router.decide("compare rust and go").mode == SPARK


def test_the_decision_is_explainable():
    decision = router.decide("do a thorough investigation of the ai chip market")
    assert decision.score > 0
    assert "depth" in decision.why()


def test_an_empty_question_does_not_go_deep():
    assert router.decide("").mode == SPARK


# ── nothing runs another mode's work ──────────────────────────────────────

def test_no_module_in_v3_reaches_for_the_old_deep_engine():
    """The in-band double-run is gone because the thing it ran is not here."""
    import ast
    import os

    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "syrix_v3")
    offenders = []
    for base, _dirs, files in os.walk(root):
        if "__pycache__" in base:
            continue
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(base, name)
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
            for node in ast.walk(tree):
                target = ""
                if isinstance(node, ast.Import):
                    target = " ".join(a.name for a in node.names)
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    target = node.module or ""
                if "zenith" in target or "spark_engine" in target:
                    offenders.append(f"{name}: {target}")
    assert offenders == [], f"v3 reaches for the replaced engines: {offenders}"


def test_a_spark_turn_makes_one_model_call_when_nothing_needs_a_tool(monkeypatch):
    """The whole latency story of the baseline was 4.65 calls per turn."""
    from syrix_v3 import gateway, loop

    calls = []

    def _once(messages, **kwargs):
        calls.append(kwargs.get("tier"))
        return gateway.Completion(text="an answer", provider="fake")

    monkeypatch.setattr(gateway, "complete", _once)
    result = loop.run([{"role": "user", "content": "q"}],
                      budget=budget.for_mode(SPARK), request_id="m1")

    assert result.model_calls == 1
    assert calls == [budget.for_mode(SPARK).tier]


def test_spark_thinks_on_a_capable_tier():
    """Fast must not mean small. Speed comes from the hardware."""
    assert budget.for_mode(SPARK).tier != "T1_FAST"


# ── effort: fast when it can be, deep when it needs to be ─────────────────

@pytest.mark.parametrize("question", [
    "hey",
    "thanks bro",
    "what is the capital of france",
    "whats bitcoin at right now",
    "just say yes or no, would you buy btc",
])
def test_easy_questions_are_not_made_to_wait(question):
    decision = router.decide(question)
    plan = budget.for_mode(decision.mode, decision.effort)
    assert decision.mode == SPARK
    assert decision.effort == budget.LIGHT
    base = budget.for_mode(SPARK)
    assert plan.deadline_s < base.deadline_s
    assert plan.max_rounds < base.max_rounds
    # Fewer rounds than normal, but never so few that the tools are withdrawn
    # before the first call. At one round the loop marks the round final and
    # offers no tools at all, so a light turn whose prefetch came back with
    # nothing but titles could not go and open one — and it answered by
    # describing the search results instead. The second round costs nothing
    # unless the model actually asks for a tool.
    assert plan.max_rounds >= 2


@pytest.mark.parametrize("question", [
    "compare rust and go for a backend service and tell me which you would pick",
    "why is my python script leaking memory when i use multiprocessing with a shared queue",
    "whats the latest news on IREN and why did it move",
])
def test_hard_questions_stay_with_spark_and_get_room(question):
    """Spark is the fast EXPERT. Hard is not the same as "hand it over" —
    Zenith is for what Spark cannot finish, not for what is merely hard."""
    decision = router.decide(question)
    plan = budget.for_mode(decision.mode, decision.effort)
    assert decision.mode == SPARK, decision.why()
    assert decision.effort == budget.HARD
    base = budget.for_mode(SPARK)
    assert plan.deadline_s > base.deadline_s
    assert plan.max_rounds > base.max_rounds
    assert plan.max_sources > base.max_sources


def test_light_effort_never_switches_gathering_off():
    """"What is bitcoin at" is a trivial question that still needs a live
    number. Zeroing the gathering window answered it from training."""
    plan = budget.for_mode(SPARK, budget.LIGHT)
    assert plan.prefetch_s > 0


def test_effort_never_makes_spark_less_capable_than_its_base():
    """The ceiling moves with effort; the floor does not."""
    base = budget.for_mode(SPARK)
    for effort in (budget.LIGHT, budget.NORMAL, budget.HARD):
        plan = budget.for_mode(SPARK, effort)
        assert plan.tier == base.tier, "a cheaper tier would be a worse model"
        assert plan.max_tokens >= base.max_tokens
        assert plan.prompt_chars == base.prompt_chars


def test_harder_means_more_rounds_not_fewer():
    """The scale field meant two things and the hard tier read as an
    absolute 2, giving the hardest questions fewer rounds than ordinary
    ones."""
    light = budget.for_mode(SPARK, budget.LIGHT).max_rounds
    normal = budget.for_mode(SPARK, budget.NORMAL).max_rounds
    hard = budget.for_mode(SPARK, budget.HARD).max_rounds
    assert light < normal < hard


def test_spark_can_search_read_and_then_answer():
    """Two rounds allowed search-then-answer and nothing else, so a Spark
    turn could never open what it found before answering."""
    assert budget.for_mode(SPARK).max_rounds >= 3


def test_zenith_does_not_scale():
    """It is already "as long as it needs"."""
    for effort in (budget.LIGHT, budget.NORMAL, budget.HARD):
        plan = budget.for_mode(ZENITH, effort)
        assert plan.deadline_s == 0
        assert plan.max_rounds == budget.for_mode(ZENITH).max_rounds


@pytest.mark.parametrize("question,why", [
    ("write me a report on the uk housing market: prices, rates, and what "
     "happens if the base rate moves again", "asked for depth outright"),
    ("why did iren move so much this week, what caused it, and what does it "
     "mean for the rest of the sector", "three separate investigations"),
])
def test_the_two_structural_hand_overs(question, why):
    """A score alone cannot separate these — the Spark cases land at 1.0-1.1
    and these at 1.2-1.4, inside the noise of a hand-weighted sum. What
    distinguishes them is not difficulty but whether one investigation can
    finish the job."""
    assert router.decide(question).mode == ZENITH, why


def test_the_two_modes_approach_the_work_differently():
    """Zenith is not Spark with more time."""
    from syrix_v3 import persona
    spark = persona.system_prompt(budget.for_mode(SPARK))
    zenith = persona.system_prompt(budget.for_mode(ZENITH))
    assert "fast expert" in spark
    assert "deep expert" in zenith
    assert "assumptions" in zenith and "assumptions" not in spark
    # Neither is told to be brief or to pad.
    for prompt in (spark, zenith):
        assert "1-3 sentences" not in prompt
        assert "word count" not in prompt or "not in the word count" in prompt


def test_the_conversation_fits_alongside_the_persona():
    """The prompt budget has to leave room for the thread, not just the rules.

    `brain_pool.quality.budget_messages` fits a request by keeping every system
    message and dropping the OLDEST ordinary turns. So when the persona grew to
    9,196 characters against an 11,500 budget, the thing dropped was the letter
    the user had pasted three messages earlier — and SYRIX answered "I don't see a
    cover letter or any previous messages from you". The persona is a fixed
    cost on every turn; it may not be most of the prompt.
    """
    from syrix_v3 import persona
    plan = budget.for_mode(SPARK)
    rules = persona.system_prompt(plan, tool_names=[
        "calculate", "document", "look", "memory", "read", "runtime_status",
        "search", "self_knowledge"])
    room = plan.prompt_chars - len(rules)
    # Enough for a pasted document and a full answer about it, plus a follow-up.
    assert room >= 5000, (
        f"only {room} characters left for the conversation after a "
        f"{len(rules)}-character persona")
    assert len(rules) < plan.prompt_chars * 0.6


def test_several_parts_about_one_subject_stay_one_investigation():
    """Show it, say why it is good, say how to build it: one subject, one
    search. Sent to the deep engine it took over a minute on free lanes."""
    from syrix_v3 import router
    decision = router.decide(
        "can u show me iamges of the best th 12 base and tell me why is ti good "
        "and how to make it with iamge guide me")
    assert decision.mode == "spark"
    assert decision.effort == "hard"
