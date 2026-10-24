"""Questions about the owner are answered from what SYRIX knows, not the web."""
import pytest

from syrix_v3 import owner


@pytest.mark.parametrize("text", [
    "who is kabirr kohli",
    "tell me about kabirr",
    "what games does kabirr play",
    "is kabirr into trading",
    "who is kabir kohly",
    "what do you know about me",
    "who am i",
])
def test_questions_about_him_are_recognised(text):
    assert owner.asks(text)


@pytest.mark.parametrize("text", [
    "who made you",                         # the creator path answers this
    "search online for kabirr kohli",        # explicitly asks for the web
    "Explain kabirr-kohli.html.",            # a file, not the person
    "open kabirr kohli portfolio site",      # a visit, not a question
    "kohli core status",                     # the core, not the person
    "whats the price of nvda",
])
def test_other_questions_are_left_alone(text):
    assert not owner.asks(text)


def test_the_note_carries_the_profile_and_forbids_guessing():
    note = owner.note("## Who he is\n- He likes stocks.")
    assert "He likes stocks." in note
    assert "never invent" in note
    assert "web" in note


def test_the_profile_file_is_read_without_its_editing_notes():
    text = owner.profile()
    if not text:
        pytest.skip("knowledge/owner_profile.md is personal and not in the repo")
    assert "Kabirr Kohli" in text
    assert "Edit it freely" not in text


def test_a_follow_up_about_him_after_an_answer_about_him():
    last = "I was designed and built by Kabirr Kohli, who crafted me personally."
    assert owner.asks("can u tell me more about him pls", last_reply=last)
    assert not owner.asks("tell me more about him", last_reply="Elon Musk runs Tesla.")


@pytest.mark.parametrize("text", ["how are you", "how are u bro?", "hows it going", "whats up", "you good?"])
def test_small_talk_is_conversation_not_a_search(text):
    from syrix_v3.research import triage
    assert not triage.assess(text, last_reply="Hey!").wanted


def test_his_name_is_never_searched_unless_the_web_is_asked_for():
    kept, held = owner.withhold(["kabirr kohli", "nvda price", "kabirr kohli vantage"], "what would kabirr think of nvda")
    assert kept == ["nvda price"] and len(held) == 2
    assert owner.withhold(["kabirr kohli"], "search online for kabirr kohli") == (["kabirr kohli"], [])


@pytest.mark.parametrize("text", ["who is virat kohli", "kabir singh movie", "Explain kabirr-kohli.html"])
def test_other_people_and_files_are_not_him(text):
    assert not owner.mentions(text)
