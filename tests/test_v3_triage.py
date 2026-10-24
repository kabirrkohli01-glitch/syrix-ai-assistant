"""What gets looked up, and what does not.

The rule under test is that gathering is the default. A model cannot detect
that it is guessing, so the decision cannot rest on whether a question
*looks* current — asked to compare two AI models it had never heard of,
SYRIX matched no keyword, searched nothing, and invented the comparison
down to the per-token pricing.
"""
from __future__ import annotations

import pytest

from syrix_v3.research import triage


@pytest.mark.parametrize("question", [
    # Recorded from the live session: fabricated in full, never searched.
    "can u compare chat gpt astra vs claude fable 5.1",
    "compare the latest nvidia and amd flagship gpus",
    "which is better, rust or go, for a backend",
    # Obviously current.
    "what is bitcoin worth right now",
    "whats the latest news on IREN stock and why did it go up",
    "who is the ceo of openai right now",
    "what is the weather in london",
    # Not obviously anything, and therefore still worth checking.
    "what did the supreme court decide about section 230",
    "how much does a tesla model 3 cost",
    "is the northern line running",
    "tell me about the company monzo",
])
def test_these_are_looked_up(question):
    need = triage.assess(question)
    assert need.wanted, f"{question!r} would have been answered from memory"
    assert need.queries, "wanted evidence but produced no query to search for"


@pytest.mark.parametrize("question", [
    "hey",
    "thanks bro",
    "what is 4250 at 18 percent compounded for 3 years",
    "how many seconds are in a fortnight",
    "who made you",
    "what model are you running",
    "are you jarvis",
    "write me a python function that reverses a linked list",
    "create an html file that draws a tree",
    "remember that i take my coffee black",
    "what did we talk about in this chat",
    # Questions about SYRIX itself. Nothing on the open web knows anything
    # about this machine, so retrieval cannot improve one of these answers —
    # it can only replace it. Every one of these was searched for in the live
    # session of 2026-09-20: asked which core it was running, SYRIX returned
    # articles about AI safety discourse, and asked why it had failed to
    # answer something on the first attempt it cited a piece on
    # search-engine visibility in AI overviews.
    "are u running on core v3",
    "so tell me about yourself",
    "what engine are you",
    "so why could not u answer this the first try why after forcing you",
    "so what would u like to see kabirr improve in u tell us about ur dreams",
    "why dont u just edit urself and fix these things",
    "close yourself",
    "so as an ai u are saying u dont have the power to even search the web",
    "whats your context limit",
])
def test_these_are_not(question):
    need = triage.assess(question)
    assert not need.wanted, f"{question!r} would have searched for no reason: {need.reason}"


@pytest.mark.parametrize("question", [
    # Owning the thing a question is about does not make the answer local.
    # "my" used to exempt a question from gathering outright, so this one was
    # answered with generic advice about how to assess university chances.
    "can u see my proflio and tell me what unis i can get in",
    "is my iphone 13 worth upgrading to an 18",
    "what are my chances at qmul with these grades",
])
def test_a_question_about_his_things_still_looks_outward(question):
    need = triage.assess(question)
    assert need.wanted, f"{question!r} was treated as answerable from memory"


@pytest.mark.parametrize("question", [
    # The impersonal "you". Widening the self-question exemption is only safe
    # while it can tell these apart: exempting a question about current
    # tooling from gathering is the original defect pointing the other way.
    "how do you make sourdough bread",
    "how do you install the latest cuda toolkit",
    "how did you get from london to paris before the tunnel",
    "you cant fix a leaking tap without a wrench right",
    # A view about the world is not a question about SYRIX, and the view has
    # to be formed against evidence.
    "whats your take on the iphone 18",
    "whats your view on tesla stock right now",
    "tell me about your competitors in the assistant space",
])
def test_the_impersonal_you_is_not_a_question_about_syrix(question):
    need = triage.assess(question)
    assert need.wanted, (
        f"{question!r} was read as a question about SYRIX itself: {need.reason}")


def test_a_code_request_naming_something_current_still_looks():
    """Writing code is exempt; writing code against a live API is not."""
    need = triage.assess("write a script using the latest openai api version")
    assert need.wanted


def test_the_question_itself_is_the_first_query():
    need = triage.assess("what is bitcoin worth right now")
    assert need.queries[0] == "what is bitcoin worth right now"


def test_a_time_bound_question_asks_for_recency():
    assert triage.assess("what is bitcoin worth right now").recency_days <= 7
    assert triage.assess("tell me about the company monzo").recency_days == 0


def test_queries_are_distinct_and_bounded():
    need = triage.assess("what is the latest news on nvidia earnings", max_queries=3)
    assert 1 <= len(need.queries) <= 3
    assert len(set(q.lower() for q in need.queries)) == len(need.queries)


def test_an_empty_question_does_nothing():
    assert triage.assess("").wanted is False
    assert triage.assess("   ").wanted is False


_LETTER = (
    "hi do u thing this is a good cover letter for me Final version Dear "
    "Admissions Tutor, I am applying for the BSc Computer Science because I "
    "want to develop the mathematical foundations needed to build reliable "
    "intelligent software. I am currently completing a Level 3 Diploma at "
    "my local college. Alongside my studies I have built a local AI "
    "assistant runtime. I am also retaking one exam this year. "
    "Yours faithfully, A. Candidate"
)


@pytest.mark.parametrize("question", [
    # A follow-up leaning on the thread. Searched verbatim in a live session,
    # an opinion question about a pronoun came back about an unrelated
    # musician; the clarifying follow-up searched again and returned poetry
    # and song lyrics.
    "so with the info u have got what do u thing about him and do u thing "
    "there is a change he will get in",
    "i mean the cover lettet u just read bro kabirr",
    "bro read this chat i just send it omggg",
    "what do you think about her",
    # Material the user pasted: the web was searched for the entire letter, and the
    # answer was SEO advice about how to write cover letters.
    _LETTER,
])
def test_the_subject_is_already_in_front_of_it(question):
    need = triage.assess(question)
    assert not need.wanted, (
        f"searched the web for something already in the prompt: {need.reason}")


def test_pasted_content_still_searches_when_he_asks_it_to():
    need = triage.assess(_LETTER + " also compare it to the current ucas guidance")
    assert need.wanted


def test_a_word_inside_pasted_material_is_not_an_instruction():
    """The user's letter contained "Final version" and "I am currently completing", so
    the ordinary volatile/time check matched "version" and "currently" and
    searched for the letter anyway. Only an explicit ask reaches the web."""
    need = triage.assess(_LETTER)
    assert not need.wanted
    assert "pasted" in need.reason


@pytest.mark.parametrize("question", [
    "can u tell me the top 10 uk unis ranking",
    "whats the best uk uni for computer science",
    "best laptop for coding",
])
def test_a_ranking_is_treated_as_dated(question):
    """Asked for the top ten UK universities it matched no time word, so every
    page ranked on keywords alone and the answer came from 2018 tables — in
    2026, with "these might have changed by 2024" attached."""
    need = triage.assess(question)
    assert need.wanted
    assert 0 < need.recency_days <= 365, need.reason


@pytest.mark.parametrize("question", [
    "you dont have internet access do you",
    "you cant read my files right",
    "u cant see my documents can u",
    "since you cant search the web, just guess",
    "you have no memory of our last chat do you",
])
def test_a_false_capability_premise_is_answered_from_the_runtime(question):
    """Telling SYRIX what it cannot do is a question about SYRIX.

    The object guard treated "you cant read my files right" as an instruction
    because it names a thing, so the capability block was never supplied and
    the local lane replied "Correct, I cannot read or access any of your
    personal files" — false, the documents tool is loaded. A premise about
    the build has to be checked against the build.
    """
    need = triage.assess(question)
    assert not need.wanted
    assert need.reason == triage.SELF_REASON, need.reason


@pytest.mark.parametrize("question", [
    "can you read my cv and tell me what to fix",
    "open the attached pdf and summarise it",
])
def test_an_instruction_about_a_file_is_still_an_instruction(question):
    """The premise rule must not swallow a genuine request to go and look."""
    need = triage.assess(question)
    assert need.reason != triage.SELF_REASON, need.reason


@pytest.mark.parametrize("question", [
    "How many positive integers less than 1000 are divisible by 7 but not by 11?",
    "What is the sum of all positive divisors of 360?",
    "A shop sells pencils at 3 for £1.20. How much do 17 pencils cost?",
    "Alice has 3 brothers. Each of her brothers has 2 sisters. How many sisters does Alice have?",
    "If today is Wednesday, what day of the week will it be 100 days from now?",
    "A snail climbs 3 metres up a 10-metre well each day and slips back 2 metres each night. On which day does it reach the top?",
])
def test_a_problem_to_work_out_is_not_searched(question):
    """Searched, one of these copied a number off a page: 115 for 130."""
    need = triage.assess(question)
    assert not need.wanted and need.reason == triage.PROBLEM_REASON


@pytest.mark.parametrize("question", [
    "how much does the iPhone 17 cost",
    "iphone 17 vs galaxy s26",
    "how many people live in london",
    "what is the population of tokyo in 2025",
])
def test_lookups_with_numbers_are_still_searched(question):
    assert triage.assess(question).wanted


@pytest.mark.parametrize("question", [
    "which laptop should i buy for computer science uni under 1000 pounds",
    "should i buy an iphone 17 or wait",
    "best headphones for gaming",
])
def test_a_buying_decision_is_searched_as_current(question):
    """Undated, a laptop question was answered with a 2021 model in 2026."""
    need = triage.assess(question)
    assert need.wanted and need.recency_days == 180
