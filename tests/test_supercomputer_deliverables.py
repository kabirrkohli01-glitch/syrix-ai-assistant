"""Coverage gate — does the answer contain every part the request asked for?

The failure under test: a seven-part research request answered with three
well-written paragraphs covering three of them, which reads as complete and
passes every honesty check because nothing in it is false.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from syrix_supercomputer import deliverables  # noqa: E402


COMPANY_GOAL = (
    "Research this company and tell me what it does, how it makes money, "
    "who its customers are, who the current CEO is, its main competitors, "
    "its business model and its biggest risks."
)


class TestRequestedSections:
    def test_compound_request_is_split_into_parts(self):
        parts = deliverables.requested_sections(COMPANY_GOAL)
        assert len(parts) >= 5
        joined = " | ".join(parts).lower()
        for subject in ("money", "customers", "ceo", "competitors", "risks"):
            assert subject in joined

    def test_a_simple_question_has_no_parts(self):
        # Nothing to check against — and inventing parts here would push back
        # on a perfectly good one-line answer.
        assert deliverables.requested_sections("What is the price of Apple stock?") == []
        assert deliverables.requested_sections("Read this page.") == []

    def test_no_anchor_verb_means_no_list(self):
        assert deliverables.requested_sections("I went to the shop, then home.") == []

    def test_a_spaced_hyphen_list_keeps_its_first_item(self):
        # What actually gets typed. Requiring a true em dash meant the anchor
        # path ran instead, and "…assistant - latency" was dropped as a long
        # clause — silently losing the first thing asked for.
        goal = ("Compare the best voice APIs for a real-time assistant - latency, "
                "voice quality, pricing and free tier - and recommend one.")
        parts = deliverables.requested_sections(goal)
        assert "latency" in [p.lower() for p in parts]
        assert len(parts) == 4

    def test_hyphenated_words_survive_the_split(self):
        parts = deliverables.requested_sections(
            "Tell me about the real-time and low-latency options, pricing and support.")
        joined = " ".join(parts)
        assert "real-time" in joined and "low-latency" in joined

    def test_long_clauses_are_not_sections(self):
        goal = ("Tell me whether this claim is true by checking the pricing page "
                "and the official documentation and then reading the terms")
        # Every part here is a clause, not a subject.
        assert deliverables.requested_sections(goal) == []


class TestMissingSections:
    def test_answer_covering_three_of_seven_is_caught(self):
        answer = ("Cekura builds voice AI testing tools. It sells subscriptions "
                  "to developer teams, and its pricing starts at $99 a month.")
        missing = deliverables.missing_sections(COMPANY_GOAL, answer)
        joined = " | ".join(missing).lower()
        assert "ceo" in joined
        assert "competitors" in joined
        assert "risks" in joined

    def test_full_answer_passes(self):
        answer = (
            "It builds voice AI testing tools. It makes money from subscriptions. "
            "Its customers are developer teams. The CEO is Jane Roe. Its main "
            "competitors are Vapi and Retell. The business model is seat-based "
            "SaaS. The biggest risks are platform dependency and churn.")
        assert deliverables.missing_sections(COMPANY_GOAL, answer) == []

    def test_equivalent_wording_counts_as_covered(self):
        # "chief executive" answers a request for the CEO; "rivals" answers
        # competitors. Matching must not require the questioner's noun.
        answer = (
            "It builds testing tools. It monetises through subscriptions. Its "
            "clients are developer teams. The chief executive is Jane Roe. Its "
            "closest rivals are Vapi and Retell. Revenue is seat-based. The main "
            "threat is platform dependency.")
        assert deliverables.missing_sections(COMPANY_GOAL, answer) == []

    def test_empty_answer_is_not_reported_as_a_gap(self):
        # An empty answer is caught by the emptiness check, not this one —
        # reporting seven missing sections there would bury the real problem.
        assert deliverables.missing_sections(COMPANY_GOAL, "") == []


class TestArtefacts:
    def test_pdf_request_needs_a_pdf(self):
        goal = "Research this business and create a professional PDF covering its products and pricing."
        assert deliverables.requested_artefacts(goal) == ["pdf"]
        assert deliverables.missing_artefacts(goal, []) == ["pdf"]
        assert deliverables.missing_artefacts(goal, ["brief.pdf"]) == []

    def test_reading_an_existing_pdf_asks_for_no_file(self):
        goal = "Read the file vlc_note.pdf in my Downloads and tell me what it says."
        assert deliverables.requested_artefacts(goal) == []
        assert deliverables.missing_artefacts(goal, []) == []

    def test_spreadsheet_is_satisfied_by_xlsx_or_csv(self):
        goal = "Build me a spreadsheet of the ten stocks."
        assert deliverables.missing_artefacts(goal, ["stocks.csv"]) == []
        assert deliverables.missing_artefacts(goal, ["stocks.xlsx"]) == []
        assert deliverables.missing_artefacts(goal, ["stocks.pdf"]) == ["spreadsheet"]

    def test_generic_document_word_does_not_double_count_a_named_format(self):
        goal = "Create a PDF document summarising it."
        assert deliverables.requested_artefacts(goal) == ["pdf"]


class TestMessages:
    def test_pushback_names_every_missing_part(self):
        gap = {"sections": ["who the current CEO is", "its main competitors"],
               "artefacts": ["pdf"]}
        msg = deliverables.pushback(gap)
        assert "CEO" in msg and "competitors" in msg and "pdf" in msg
        # It must accept an honest gap rather than demand the impossible.
        assert "cannot be established" in msg

    def test_pushback_is_empty_when_nothing_is_missing(self):
        assert deliverables.pushback({"sections": [], "artefacts": []}) == ""

    def test_gap_note_labels_the_shortfall(self):
        note = deliverables.gap_note({"sections": ["its biggest risks"], "artefacts": []})
        assert note.startswith("**Not established:**")
        assert "risks" in note

    def test_gap_note_is_empty_when_complete(self):
        assert deliverables.gap_note({"sections": [], "artefacts": []}) == ""


class TestShortfall:
    def test_shortfall_combines_both(self):
        goal = ("Research this business and create a PDF covering its products, "
                "its leadership and its competitors.")
        gap = deliverables.shortfall(goal, "It sells widgets.", [])
        assert gap["artefacts"] == ["pdf"]
        assert gap["sections"]

    def test_never_raises_on_junk(self):
        for goal in (None, "", 12345, "?" * 500):
            gap = deliverables.shortfall(goal, "x", [])
            assert isinstance(gap["sections"], list)
            assert isinstance(gap["artefacts"], list)


@pytest.mark.parametrize("goal,answer,expect_gap", [
    # The live Test 5 failure: six requested things, none delivered.
    ("Compare the best voice APIs for a real-time assistant - latency, voice "
     "quality, pricing and free tier - and recommend one.",
     "I could not retrieve the article, so the comparison is unavailable.",
     True),
    ("Compare the best voice APIs for a real-time assistant - latency, voice "
     "quality, pricing and free tier - and recommend one.",
     "ElevenLabs wins. Latency is 75ms, voice quality is the most natural of "
     "the three, pricing starts at $5/month and the free tier gives 10k "
     "characters. I recommend it.",
     False),
])
def test_live_failures(goal, answer, expect_gap):
    gap = deliverables.shortfall(goal, answer, [])
    assert bool(gap["sections"] or gap["artefacts"]) is expect_gap


class TestTrivialPlanGate:
    """Short is not the same as simple.

    The two-step "read the page, answer" shortcut keyed on word count alone, so
    a four-part comparison in 21 words got no plan at all and then wandered for
    four minutes with nothing to fall back on.
    """

    def test_a_short_compound_request_is_not_trivial(self):
        goal = ("Compare the best voice APIs for a real-time assistant - latency, "
                "voice quality, pricing and free tier - and recommend one.")
        assert len(goal.split()) <= 30
        assert len(deliverables.requested_sections(goal)) >= 2

    def test_a_genuinely_simple_request_still_is(self):
        for goal in ("Read this page and tell me what it says.",
                     "What is the price of Apple stock?",
                     "Summarise this article."):
            assert len(deliverables.requested_sections(goal)) < 2
