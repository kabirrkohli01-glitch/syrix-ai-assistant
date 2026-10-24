"""Conversation is answered as conversation, and questions are left alone.

Measured live: "thx bro apprecate it", sent after a good answer, was rewritten
into a question about what "thx" means, searched, and answered with a usage
note on the abbreviation. The negative cases matter as much as the positive
ones: every question below must still reach triage exactly as before.
"""
from __future__ import annotations

import pytest

from syrix_v3.research import rewrite, social, triage


@pytest.mark.parametrize("text", [
    "thx bro apprecate it", "thanks", "ty", "thank you so much bro", "ok cool", "nice one",
    "love it", "good job", "that worked thanks", "perfect", "appriciate it man", "cheers mate",
    "no worries", "lol", "gn bro", "you are the best", "makes sense", "awsome thx",
    "well done syrix", "thanks bro that helped a lot", "good night bye",
])
def test_conversation_is_recognised(text):
    assert social.is_social(text)


@pytest.mark.parametrize("text", [
    "thanks, now do the same for paris", "ok what about paris", "cool how does it work?",
    "yes", "yeah do it", "sure", "thanks can you explain transformers",
    "nice what is the weather", "ok so what should i buy", "great britain population",
    "perfect squares under 100", "thank you notes for teachers ideas",
    "love island cast 2026", "cool facts about space", "nice restaurants in london",
])
def test_a_question_is_not_conversation(text):
    assert not social.is_social(text)


@pytest.mark.parametrize("text", [
    "make it shorter", "explain more", "can u explain that simpler", "give me an example",
    "put it in a table", "what do you mean by that", "continue", "go on", "elaborate",
    "summarise it", "in simpler words", "say that again", "ok make it more professional",
    "rewrite it", "translate that to spanish", "explain it like im 5",
])
def test_reworking_the_last_answer_is_recognised(text):
    assert social.reworks_last_answer(text)


@pytest.mark.parametrize("text", [
    "explain quantum computing", "give me an example of a haiku about rain in winter",
    "make a website for my bakery", "summarise the news today",
    "continue the story of harry potter", "what do you mean when you say beta in finance",
    "more news on bitcoin", "tell me more about the roman empire", "fix my cv",
])
def test_a_new_subject_is_not_a_rework(text):
    assert not social.reworks_last_answer(text)


def test_thanks_is_neither_searched_nor_rewritten():
    need = triage.assess("thx bro apprecate it", last_reply="Canberra is the capital.")
    assert not need.wanted and need.reason == triage.SOCIAL_REASON
    assert not rewrite.needed("thx bro apprecate it", has_history=True,
                              last_reply="Canberra is the capital.")


def test_ok_after_a_question_is_a_reply_not_small_talk():
    """"ok" after "want me to draft it?" means draft it."""
    need = triage.assess("ok", last_reply="I can draft the cover letter. Want me to draft it?")
    assert need.reason != triage.SOCIAL_REASON
    assert rewrite.needed("ok", has_history=True, last_reply="Want me to draft it?")


def test_a_rework_answers_from_the_thread():
    need = triage.assess("make it shorter", last_reply="A long answer about GPUs.")
    assert not need.wanted and need.reason == triage.THREAD_REASON
    assert not rewrite.needed("make it shorter", has_history=True, last_reply="A long answer.")


def test_a_rework_with_no_thread_is_an_ordinary_message():
    # Nothing above to rework: triage decides as it always did.
    assert triage.assess("continue").reason != triage.THREAD_REASON
