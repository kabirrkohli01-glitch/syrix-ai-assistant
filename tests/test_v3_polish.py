"""The one post-processing pass, and the line it must not cross.

Every case here is either something that was actually shown to the user and
should not have been, or something that must survive untouched because it is
the answer rather than plumbing around it.
"""
from __future__ import annotations

import pytest

from syrix_v3 import polish


@pytest.mark.parametrize("dirty,clean_contains,gone", [
    # Seen live: a model footnoting in the dialect it was trained to. The
    # number is the evidence number it was given, so it is kept as a chip.
    ("Bitcoin is at $81,072 【1†L1-L3】 and rising【2†L4】.",
     "Bitcoin is at $81,072 [1] and rising.[2]", "【"),
    # The same dialect naming no numbered source carries nothing to keep.
    ("It rose 3% today【turn0search3】.", "It rose 3% today.", "【"),
    ("The figure is 4.2% [citation:3].", "The figure is 4.2%.", "citation:"),
    ("It closed higher[^2] yesterday.", "It closed higher yesterday.", "[^2]"),
    # Chat-template control tokens that escaped the transport.
    ("Done.<|im_end|>", "Done.", "im_end"),
    # Tool markup written into prose. The separator after the tag name is
    # not always a space — an equals sign is a real dialect and slipped past
    # a pattern that only allowed whitespace.
    ("<function=search>query</function> here", "query here", "<function"),
    ("<tool_call>{}</tool_call>ok", "ok", "tool_call"),
])
def test_machine_plumbing_is_removed(dirty, clean_contains, gone):
    out, removed = polish.clean(dirty)
    assert clean_contains in out
    assert gone not in out
    assert removed, "something was removed but it was not reported"


@pytest.mark.parametrize("text", [
    # A view is the answer. The runtime being replaced deleted sentences
    # naming an action and a direction, so it could read every filing a
    # company had made and then remove the one sentence that took a view.
    "Yes — I would buy it here, with a stop under 78,000.",
    "No. The setup is broken and I would stay out.",
    "I prefer spaces, and I would not switch.",
    # Length is the answer's business.
    "Paris.",
    "x" * 20000,
    # Ordinary prose that happens to contain bracket-like punctuation.
    "The array is written a[0] and the note is (see below).",
    "Use the formula E = mc^2 [derived from special relativity].",
])
def test_what_the_answer_says_is_never_touched(text):
    out, removed = polish.clean(text)
    assert out == text.strip()
    assert removed == []


def test_empty_input_is_handled():
    assert polish.clean("") == ("", [])
    assert polish.clean(None) == ("", [])


@pytest.mark.parametrize("written,spoken_has,spoken_lacks", [
    ("## Heading\ntext", "Heading", "#"),
    ("- **Bold** point", "Bold point", "*"),
    ("See https://example.com/page for more.", "the link on screen", "https://"),
    ("```py\nprint(1)\n```", "code omitted", "print(1)"),
    ("Read [the docs](https://x.test) now.", "the docs", "https://"),
    ("| a | b |\n| 1 | 2 |", "", "|"),
])
def test_a_spoken_answer_is_written_to_be_heard(written, spoken_has, spoken_lacks):
    spoken = polish.for_speech(written)
    if spoken_has:
        assert spoken_has in spoken
    assert spoken_lacks not in spoken


def test_speech_keeps_the_substance():
    written = (
        "Bitcoin is trading around **$81,000**.\n\n"
        "- CoinGecko has it at $81,144\n"
        "- CoinMarketCap has it at $81,055\n"
    )
    spoken = polish.for_speech(written)
    assert "81,144" in spoken and "81,055" in spoken
    assert "**" not in spoken


def test_a_search_preamble_is_removed_but_the_answer_is_not():
    """Measured live: an answer opened "Based on the sources gathered:" AFTER
    the check had already asked for it to be rewritten — the rewrite came back
    opening the same way. It carries no information, so it is cut rather than
    sent back a second time."""
    body, removed = polish.clean(
        "Based on the sources gathered:\n\n- **Oxford** is 1st with 98.2.")
    assert body.startswith("- **Oxford**")
    assert "a search preamble" in removed


def test_a_real_reference_to_his_material_is_left_alone():
    body, removed = polish.clean(
        "Based on your CV, I would lead with SYRIX rather than the diploma.")
    assert body.startswith("Based on your CV")
    assert removed == []


def test_a_preamble_with_nothing_after_it_is_left_for_the_check():
    body, _ = polish.clean("Based on the sources gathered.")
    assert body == "Based on the sources gathered."


def test_a_tool_name_used_as_a_heading_is_removed():
    body, removed = polish.clean(
        "Runtime_status: Search is operational and I can reach the open web.")
    assert body.startswith("Search is operational")
    assert "a tool-name prefix" in removed


_LISBON = (
    "Top things to do in Lisbon\n\n"
    "1. **Alfama** - wander the lanes and hear fado in the taverns.\n"
    "2. **Belem Tower and Jeronimos Monastery** - two UNESCO sites in Manueline stone.\n"
    "3. **Tram 28** - a scenic climb through Graca, Baixa and Estrela.\n\n"
    "**Day trips** - Sintra's palaces, Cascais and the Setubal wine region."
)


def test_a_closing_note_about_the_search_is_removed():
    body, removed = polish.clean(
        _LISBON + "\n\n*Note: Specific rankings from the source could not be retrieved "
        "due to access restrictions, so the list reflects the usual highlights.*")
    assert body.endswith("Setubal wine region.")
    assert "a sentence about the search" in removed


def test_a_closing_note_about_the_subject_is_kept():
    note = "*Note: opening hours change with the season, so check before you go.*"
    body, removed = polish.clean(_LISBON + "\n\n" + note)
    assert body.endswith(note)
    assert removed == []


def test_an_answer_that_is_only_the_note_is_left_for_the_check():
    text = "Intro.\n\nNote: the page could not be opened, so I cannot say."
    body, _ = polish.clean(text)
    assert body == text


def test_a_final_sentence_about_the_search_and_the_training_year_is_removed():
    """Measured live: a correct game-mechanics answer ended with a sentence
    saying no sources had opened, that it relied on prior knowledge, and that
    "post-2024" changes would need verifying. The check asked for a rewrite,
    the rewrite kept the sentence, and the original shipped with it."""
    answer = (
        "Siege machines carry your Clan Castle troops and release them where "
        "they stop, so what they hold is whatever is donated to the Clan Castle. "
        "Build them in the Workshop and pick one in the army screen before you "
        "attack. Since no sources were opened in full, this answer relies on "
        "established game mechanics from prior knowledge, and any recent changes "
        "post-2024 would require verification through updated patch notes."
    )
    body, removed = polish.clean(answer)
    assert body.endswith("before you attack.")
    assert "a sentence about the search" in removed


def test_a_leading_clause_about_the_search_goes_and_its_content_stays():
    answer = _LISBON + (
        "\n\n**Cultural experiences** \u2013 While the list from Trip.com could not be "
        "opened, typical picks include a **Fado dinner** and a **tile museum visit**.")
    body, removed = polish.clean(answer)
    assert "could not be opened" not in body
    assert "**Cultural experiences** \u2013 typical picks include a **Fado dinner**" in body
    assert "a clause about the search" in removed


def test_content_that_mentions_opening_is_not_narration():
    for text in (
        "The museum was opened in 1884 and is free on Sundays. It holds 30,000 "
        "tiles, the largest collection in Portugal and one of the best in Europe "
        "for anyone who loves ceramics and the history of design.",
        "The tomb could not be opened until 1922, when Howard Carter broke the "
        "seal. Inside were more than five thousand objects, including the gold "
        "mask, chariots and the king's own linen, now shown in Giza.",
    ):
        assert polish.clean(text) == (text, [])


# ── answer form: labels, closing offers, citation placement ─────────────────
_BODY = ("Nvidia designs graphics processors and the software around them, and most "
         "of its revenue now comes from data-centre systems sold to cloud providers.")


def test_a_short_answer_label_goes_and_the_answer_stays_word_for_word():
    out, removed = polish.clean("**Short answer:** " + _BODY)
    assert out == _BODY
    assert "an answer label" in removed


def test_a_label_is_kept_when_it_is_nearly_the_whole_reply():
    assert polish.clean("Short answer: yes.")[0] == "Short answer: yes."


def test_a_closing_paragraph_of_offers_is_removed():
    out, removed = polish.clean(_BODY + "\n\nIf you want, I can pull their latest earnings too. "
                                "Let me know if that helps.")
    assert out == _BODY
    assert "a closing offer" in removed


@pytest.mark.parametrize("ending", [
    # A question that is the substance, not an offer.
    "Would a 12-month or a 24-month horizon change your view?",
    # A list is structure, never removed as an offer.
    "- If you want the cheapest option, pick the 4060.",
])
def test_a_closing_paragraph_that_is_content_stays(ending):
    text = _BODY + "\n\n" + ending
    assert polish.clean(text)[0] == text


def test_citations_follow_the_punctuation_they_support():
    out, _ = polish.clean("It was founded in 1993 [1]. It is the largest chipmaker [2][3], "
                          "by value.")
    assert out == "It was founded in 1993.[1] It is the largest chipmaker,[2][3] by value."


def test_an_index_in_prose_is_not_a_citation():
    assert polish.clean("Take arr[2]. It is the third item.")[0] == \
        "Take arr[2]. It is the third item."


def test_a_citation_alone_on_a_line_joins_the_sentence_above():
    out, _ = polish.clean(_BODY + "\n[1]\n\nMore follows here.")
    assert out.startswith(_BODY + "[1]\n\nMore follows")


def test_a_citation_under_a_table_is_left_where_it_is():
    table = "| a | b |\n|---|---|\n| 1 | 2 |\n[1]"
    assert polish.clean(table)[0] == table


def test_a_visit_preamble_goes_but_naming_the_site_stays():
    out, _ = polish.clean("Based on the King's College London website content provided:\n\n"
                          "King's asks for A*AA including Mathematics.[1]")
    assert out == "King's asks for A*AA including Mathematics.[1]"
    kept = "Based on the NVIDIA website, NVIDIA was founded in 1993.[1]"
    assert polish.clean(kept)[0] == kept


def test_the_only_source_cited_on_every_line_is_cited_once():
    out, removed = polish.clean("* **linux** – maintained. [1]\n* **GuitarPedal** – a hobby. [1]\n"
                                "* **uemacs** – an editor. [1]\n\nA busy year overall.")
    assert out.count("[1]") == 1 and out.startswith("* **linux** – maintained.[1]")
    assert any("repeats of the only source" in r for r in removed)


def test_citations_to_different_sources_are_all_kept():
    out, _ = polish.clean("A is true [1]. B is true [2]. C holds too [1].")
    assert out == "A is true.[1] B is true.[2] C holds too.[1]"


def test_a_no_break_space_before_a_citation_is_tidied_too():
    # Measured live: U+202F between the stop and the bracket.
    assert polish.clean("It is backed by many honours.\u202f[2]")[0] == \
        "It is backed by many honours.[2]"


# ── measured on the form pass of 2026-09-24 ────────────────────────────────
def test_inline_code_is_never_rewritten():
    """The space-before-punctuation rule joined a command to its argument:
    `python -m venv .venv` came out as `python -m venv.venv`."""
    for line in ("3. Run `python -m venv .venv` to create it.",
                 "Activate it with `source .venv/Scripts/activate` and carry on.",
                 "Use `a , b` exactly as shown."):
        assert polish.clean(line)[0] == line


def test_a_citation_after_bold_or_code_moves_too():
    assert polish.clean("It uses **cross-attention** [1].")[0] == "It uses **cross-attention**.[1]"
    assert polish.clean("Run `deactivate` [2].")[0] == "Run `deactivate`.[2]"


@pytest.mark.parametrize("offer", [
    "Would you like to know more about any specific feature or aspect of these devices?",
    "Is there anything else you need?",
])
def test_more_closing_offers_are_removed(offer):
    body = ("Given the current evidence, the iPhone 17 Pro is the better camera for "
            "anyone who shoots video, and the Pixel 10 Pro for stills.[2]")
    assert polish.clean(body + "\n\n" + offer)[0] == body
