"""What the model actually gets to read from a page that was opened.

The rule under test is that an excerpt is chosen for relevance, not taken from
the top of the page. Head-truncation assumes the answer is in the opening
paragraphs, and a league table is the case where it reliably is not: the rows
sit below navigation, methodology and boilerplate. Measured on a live turn,
a top-ten question opened seven pages and reported of four of them that the
list "wasn't in the text I could read".
"""
from __future__ import annotations

from syrix_v3.research import pack
from syrix_v3.research.gather import Evidence, Source


_BOILERPLATE = (
    "Skip to main content. Cookies on this site. We use cookies to improve "
    "your experience. Accept all. Manage preferences. About us. Contact. "
    "Advertise with us. Our methodology explained in detail below. "
) * 12

_ROWS = (
    "1. University of Oxford 100.0\n"
    "2. University of Cambridge 99.2\n"
    "3. Imperial College London 97.8\n"
    "4. London School of Economics 96.4\n"
    "5. University of St Andrews 95.1\n"
    "6. University College London 94.7\n"
    "7. University of Edinburgh 93.9\n"
    "8. University of Warwick 92.6\n"
    "9. University of Manchester 91.8\n"
    "10. University of Bristol 90.5\n"
)


def test_the_ranked_rows_survive_when_the_page_opens_with_boilerplate():
    body = _BOILERPLATE + _ROWS + "Footer. Terms. Privacy."
    excerpt = pack._excerpt(body, 900, "top 10 universities in the uk ranking")
    assert "University of Oxford" in excerpt
    assert "10. University of Bristol" in excerpt


def test_an_excerpt_taken_from_further_down_says_so():
    body = _BOILERPLATE + _ROWS
    excerpt = pack._excerpt(body, 900, "top 10 universities in the uk ranking")
    assert excerpt.startswith("…")


def test_a_page_whose_answer_is_at_the_top_is_not_moved():
    """The lead paragraph must still win when nothing below beats it."""
    body = ("Nvidia closed at $184.20 on 19 September 2026, up 1.2% on the "
            "day. ") + ("Unrelated archive material. " * 200)
    excerpt = pack._excerpt(body, 300, "nvidia share price today")
    assert excerpt.startswith("Nvidia closed at $184.20")


def test_a_short_page_is_returned_whole():
    assert pack._excerpt("A short page.", 900, "anything") == "A short page."


def test_the_pack_uses_the_relevant_window_for_an_opened_source():
    """End to end through build(), not just the helper."""
    source = Source(url="https://example.com/table", title="UK league table")
    source.domain = "example.com"
    source.body = _BOILERPLATE + _ROWS
    source.retrieved_at = "2026-09-20T10:00:00"
    evidence = Evidence(queries=["top 10 universities in the uk ranking"])
    evidence.sources = [source]

    block = pack.build(evidence, max_chars=4000)
    assert "University of Oxford" in block
    assert "10. University of Bristol" in block
