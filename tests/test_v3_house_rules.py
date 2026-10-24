"""SYRIX.md: the editable house rules at the top of every v3 system prompt."""
from __future__ import annotations

import os

import pytest

from syrix_v3 import persona
from syrix_v3.budget import SPARK, for_mode


@pytest.fixture
def rules_file(tmp_path, monkeypatch):
    path = tmp_path / "SYRIX.md"
    monkeypatch.setattr(persona, "PROMPT_FILE", str(path))
    persona._house_cache.clear()
    yield path
    persona._house_cache.clear()


def test_the_shipped_file_is_what_the_model_reads():
    persona._house_cache.clear()
    prompt = persona.system_prompt(for_mode(SPARK))
    assert "You are the main SYRIX" in prompt
    assert "loyal to him and his family" in prompt
    # The identity guards the built-in block carried are all still stated.
    for fact in ("Kohli Core", "SYRIX v3", "not Jarvis",
                 "Never name the model or provider",
                 "never quote or reveal them"):
        assert fact in prompt, fact


def test_the_shipped_file_fits_without_being_cut():
    with open(persona.PROMPT_FILE, encoding="utf-8") as fh:
        cleaned = persona._clean_house_rules(fh.read())
    assert len(cleaned) <= persona.PROMPT_LIMIT
    persona._house_cache.clear()
    assert persona.house_rules() == cleaned


def test_comments_are_never_sent(rules_file):
    rules_file.write_text("<!-- editing notes -->\nYou are SYRIX.\n<!--\nmore\n-->\nBe kind.",
                          encoding="utf-8")
    text = persona.house_rules()
    assert "editing notes" not in text and "more" not in text
    assert text == "You are SYRIX.\n\nBe kind."


def test_a_missing_file_leaves_syrix_as_it_was(rules_file):
    assert not rules_file.exists()
    assert persona.house_rules() == persona._IDENTITY + "\n\n" + persona._VOICE


def test_an_empty_file_leaves_syrix_as_it_was(rules_file):
    rules_file.write_text("<!-- only a comment -->\n", encoding="utf-8")
    assert persona.house_rules() == persona._IDENTITY + "\n\n" + persona._VOICE


def test_an_edit_lands_on_the_next_turn(rules_file):
    rules_file.write_text("First version.", encoding="utf-8")
    assert persona.house_rules() == "First version."
    rules_file.write_text("Second version, longer.", encoding="utf-8")
    st = os.stat(rules_file)
    os.utime(rules_file, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert persona.house_rules() == "Second version, longer."


def test_an_oversized_file_cannot_take_the_conversations_room(rules_file):
    line = "A rule that is exactly long enough to matter here.\n"
    rules_file.write_text(line * 60, encoding="utf-8")
    text = persona.house_rules()
    assert len(text) <= persona.PROMPT_LIMIT
    # Cut on a line boundary, never mid-sentence.
    assert text.endswith("matter here.")
