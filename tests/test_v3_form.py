"""An answer takes the form it was told to take."""
from __future__ import annotations

import pytest

from syrix_v3 import check, form
from syrix_v3.media import images


@pytest.mark.parametrize("question,answer", [
    ("what is the capital of france, answer in one word", "Paris is the capital of France, since 987."),
    ("is the sky blue? just yes or no", "The sky appears blue because of Rayleigh scattering."),
    ("compare python and rust in a table", "Python is dynamic and Rust is compiled."),
    ("give me the pros of solar in bullet points", "Solar is renewable and cheap to run."),
    ("explain gravity in under 20 words", " ".join(["word"] * 40)),
    ("summarise the french revolution in one sentence", "It began in 1789. It ended the monarchy. It changed Europe."),
    ("what's 17*23, just the number", "Multiplying seventeen by twenty-three gives three hundred and ninety-one in total here."),
])
def test_a_reply_in_the_wrong_form_is_sent_back(question, answer):
    assert form.problems(question, answer)
    assert not check.run(question, answer).ok


@pytest.mark.parametrize("question,answer", [
    ("what is the capital of france, answer in one word", "Paris"),
    ("is the sky blue? just yes or no", "Yes."),
    ("compare python and rust in a table", "| | Python | Rust |\n|---|---|---|\n| Typing | dynamic | static |"),
    ("give me the pros of solar in bullet points", "- Renewable\n- Cheap to run"),
    ("explain gravity in under 20 words", "Mass attracts mass; the more mass and the closer it is, the stronger the pull."),
    ("what's 17*23, just the number", "391"),
    ("how does photosynthesis work", "Plants turn light, water and CO2 into sugar and oxygen."),
])
def test_a_reply_in_the_right_form_is_left_alone(question, answer):
    assert form.problems(question, answer) == []


def test_the_requested_form_is_restated_just_before_the_question():
    assert "one word" in (form.brief("capital of peru? one word answer") or "")
    assert form.brief("how does photosynthesis work") is None


@pytest.mark.parametrize("question,count", [
    ("show me pictures of lisbon", 8),
    ("things to do in lisbon, no images", 0),
    ("who is jensen huang", 3),
    ("what happened in the news today", 0),
    ("what is the capital of france", 0),
    ("best beaches in portugal", 4),
    ("who is jensen huang, one word", 0),
])
def test_pictures_only_where_they_earn_their_place(question, count):
    assert images.wanted(question) == count
