"""Comparing the answer to the question.

Every rule here exists because the failure it catches was shipped. The
tests that matter most are the negative ones: this must not fire on a good
answer, because a check that fires often becomes a second model call on
every turn and then it is the latency problem it was meant to avoid.
"""
from __future__ import annotations

import pytest

from syrix_v3 import check


def _problems(question, answer, **kw):
    return check.run(question, answer, **kw).problems


# ── failures actually shipped to the user ──────────────────────────────────

def test_an_answer_that_echoes_the_question():
    """Live: "just say yes or no would u buy btc right now" was answered
    with "Yes or no would u buy btc right now"."""
    problems = _problems("just say yes or no would u buy btc right now",
                         "Yes or no would u buy btc right now")
    assert any("repeats the question" in p for p in problems)


def test_a_headline_instead_of_the_news():
    """Live: asked what was going on with Bitcoin, it replied with a title."""
    problems = _problems(
        "whats going on with bitcoin today",
        "Kevin O'Leary Names the One Thing Standing Between Bitcoin and $1 Million")
    assert any("headline" in p for p in problems)


def test_a_yes_or_no_question_without_a_yes_or_no():
    problems = _problems("just say yes or no, would you buy btc right now",
                         "Bitcoin is volatile and depends on your risk appetite.")
    assert any("yes or no" in p for p in problems)


def test_refusing_to_commit_while_using_both_words():
    """Live, after the first fix: the reply contained "yes" and "no" inside a
    sentence explaining that the sources gave neither, so a check that merely
    looked for the words passed it. The commitment has to lead."""
    problems = _problems(
        "just say yes or no, would you buy btc right now",
        "The sources consulted provide information on how to buy Bitcoin and "
        "general cautions about risk, but none give a direct recommendation "
        "of “yes” or “no” to purchasing Bitcoin right now.")
    assert any("does not start with one" in p for p in problems)


def test_a_price_question_with_no_figure():
    problems = _problems("what is nvidia trading at right now",
                         "Nvidia shares moved on the session.")
    assert any("figure" in p for p in problems)


def test_a_view_that_deflects():
    problems = _problems("do you think index funds beat stock picking",
                         "As an AI I don't have personal opinions on investing.")
    assert any("deflects" in p for p in problems)




def test_figures_with_no_citation_when_sources_were_gathered():
    problems = _problems("what is bitcoin worth", "Bitcoin is at $81,072.",
                         had_evidence=True, cited=0)
    assert any("never says where" in p for p in problems)


def test_attribution_is_read_from_the_prose_not_the_tool_count():
    """Evidence is normally prefetched, so a careful answer can attribute
    everything while having called no tool at all. Keying this off the tool
    count fired on nearly every research turn and cost a repair round each
    time."""
    verdict = check.run(
        "what is bitcoin worth",
        "Bitcoin is at $81,072, per CoinGecko's page a minute ago.",
        had_evidence=True, cited=0)
    assert verdict.ok, verdict.problems


def test_an_empty_answer():
    assert _problems("anything", "") == ["It is empty."]


# ── the session of 2026-09-20, where the same shape shipped four times ──────

def test_opening_by_describing_the_search():
    """Live: asked about a small consultancy, the whole reply was an account
    of what the search had returned and which pages had not been opened."""
    problems = _problems(
        "so tell me about golden management consultancy ltd",
        "The search returned only titles mentioning “Golden Management "
        "Consultancy Ltd”; none of those pages were opened, so no substantive "
        "information is available from the gathered sources.")
    assert any("describing the search" in p for p in problems)


def test_a_recommendation_asked_for_and_refused():
    """Live: "should i upgrade to 18" was answered with a statement that the
    sources carried no recommendation, then a list of publishers."""
    problems = _problems(
        "i have an iphone 13 should i upgrade to 18",
        "The sources do not provide a direct recommendation on whether to buy, "
        "so this decision is based on the available information.")
    assert any("describing the search" in p for p in problems)


def test_it_depends_on_your_needs_is_not_a_recommendation():
    problems = _problems(
        "so bro iphone 18 is out i have 13 rn u think its good to upgrade rn "
        "or not wroth it",
        "The exact decision depends on your specific needs and budget "
        "constraints.")
    assert any("does not make one" in p for p in problems)


def test_a_source_list_with_nothing_above_it():
    problems = _problems(
        "should i upgrade my iphone 13 to an 18",
        "This decision depends on your needs.\n\nSources:\n\n"
        "PCMag UK: Details on screen size and design.\n"
        "Business Insider: Battery life and camera specifications.")
    assert any("list of sources" in p for p in problems)


def test_denying_that_something_exists_without_citing_anything():
    """Live: asked to compare an iPhone 13 with an 18, it replied that no
    such model exists and compared the 13 with a 14 Pro instead."""
    problems = _problems(
        "no i just want u to compare iphone 13 to 18 and all the upgrades",
        "The comparison is challenging because there isn't a model called "
        "\"iPhone 18.\" I can compare the 13 with the iPhone 14 Pro instead.",
        cited=0)
    assert any("does not exist" in p for p in problems)


def test_denying_existence_is_allowed_once_it_has_been_checked():
    """The same sentence, backed by sources, is a finding rather than the
    training cutoff talking — so citations settle it."""
    verdict = check.run(
        "is there an iphone 25",
        "There is no product called the iPhone 25. Apple's current line tops "
        "out at the 18 Pro Max, per its own newsroom page today.",
        had_evidence=True, cited=4)
    assert verdict.ok, verdict.problems


def test_claiming_it_cannot_see_something_without_trying():
    """Live: asked to look at the user's portfolio, it said it had no access — while
    holding a documents tool and never calling one."""
    problems = _problems(
        "can u see my proflio and tell me what unis i can get in",
        "I don't have access to your portfolio or specific details about it. "
        "However, I can outline a general approach to assessing your chances.",
        tools_used=0)
    assert any("without having tried" in p for p in problems)


def test_saying_it_could_not_reach_something_after_trying_is_fine():
    verdict = check.run(
        "can u see my portfolio and tell me what unis i can get in",
        "I opened the two files in your documents folder and neither is the "
        "portfolio — I can see a CV and a cover letter. Point me at it and I "
        "will read it.",
        tools_used=2)
    assert verdict.ok, verdict.problems


_CALC = "12500 * (1 + 0.042/12)**84 = 16763.69173974558  (to 2dp: 16,763.69)"


def test_asking_the_calculator_and_then_writing_a_different_number():
    """Measured live, on two separate lanes: handed 1.3410953, the reply wrote
    "Using a calculator to find (1.0035)^84: ~ 1.32796" and carried the
    invented factor through to a final value £164 out."""
    problems = _problems(
        "what is 12500 at 4.2% compounded monthly for 7 years",
        "Using a calculator, (1.0035)^84 is about 1.32796, so the final value "
        "is approximately $16,599.50.",
        computed=_CALC)
    assert any("different numbers" in p for p in problems)


def test_using_the_calculated_figure_passes():
    verdict = check.run(
        "what is 12500 at 4.2% compounded monthly for 7 years",
        "It matures at $16,763.69, so $4,263.69 of that is interest.",
        computed=_CALC)
    assert verdict.ok, verdict.problems


def test_a_rounded_form_of_the_calculated_figure_still_counts():
    verdict = check.run(
        "what is 12500 at 4.2% compounded monthly for 7 years",
        "Just under $16,764 at maturity.",
        computed=_CALC)
    assert verdict.ok, verdict.problems


def test_no_calculation_means_no_such_check():
    verdict = check.run("what is 2 plus 2", "Four.", computed="")
    assert verdict.ok, verdict.problems


def test_an_intermediate_rate_is_not_required_in_the_answer():
    """0.0035 is a step, not a result; demanding it would fire on good work."""
    verdict = check.run(
        "what is 12500 at 4.2% compounded monthly for 7 years",
        "It matures at $16,763.69.",
        computed="0.042/12 = 0.0035\n" + _CALC)
    assert verdict.ok, verdict.problems


def test_a_trailing_aside_about_the_retrieval():
    """The opening-only rule cannot see this. Measured live: a good phone
    comparison closed with a paragraph headed "Limitations of the evidence"
    explaining which pages had not been fully opened."""
    problems = _problems(
        "compare the iphone 17 pro and the samsung s25 ultra for photography",
        "The S25 Ultra wins on zoom, with a 5x optical telephoto against the "
        "iPhone's 3x, per ZDNET.\n\n**Limitations of the evidence**\n\nThe "
        "comparison comes solely from the ZDNET table. No other source was "
        "fully opened.",
        had_evidence=True, cited=2)
    assert any("machinery" in p for p in problems)


def test_a_comparison_that_never_picks_a_side():
    """Measured live: a full two-phone spec table, asked which was better for
    photography, closed by saying it could not reveal which took better
    photos. A table is not a verdict."""
    problems = _problems(
        "compare the iphone 17 pro and the samsung s25 ultra for photography",
        "| Feature | iPhone 17 Pro | S25 Ultra |\n|---|---|---|\n"
        "| Main | 48 MP | 200 MP |\n\nThe data above is a high-level hardware "
        "comparison; it does not say which device actually produces nicer "
        "photos in practice.",
        had_evidence=True, cited=3)
    assert any("never comes down on one" in p for p in problems)


# ── good answers, which must pass untouched ────────────────────────────────

@pytest.mark.parametrize("question,answer,kw", [
    # A comparison that lays out the figures AND comes down on one.
    ("compare the iphone 17 pro and the samsung s25 ultra for photography",
     "The S25 Ultra, if you shoot anything at distance — its 200MP wide and 5x "
     "optical telephoto beat the iPhone's 48MP and 3x, per ZDNET's spec table. "
     "The 17 Pro wins on video and on colour you do not have to correct "
     "afterwards, so for people and clips take that instead.",
     {"had_evidence": True, "cited": 3}),
    ("just say yes or no, would you buy btc right now",
     "No. It is up 0.9% on the day into a level it has failed twice, and I would "
     "wait for a close above 82,000 before touching it.", {}),
    ("what is nvidia trading at right now",
     "Nvidia is at $222.27, per Yahoo Finance's quote page a minute ago.",
     {"had_evidence": True, "cited": 3}),
    ("compare rust and go for backend work",
     "Go wins on time-to-first-service and Rust wins on everything that runs "
     "hot. Go's runtime gives you goroutines and a garbage collector...", {}),
    ("do you think index funds beat stock picking",
     "For almost everyone, yes. The evidence on persistence of manager skill "
     "is weak and fees compound against you.", {}),
    ("what is the capital of france",
     "Paris. It has been the capital since 987 and is where the government sits.",
     {}),
    ("how does https work",
     "It is TLS underneath HTTP. The client and server agree a shared secret "
     "through a handshake, then everything after that is encrypted with it.", {}),
    # A figure-free answer to a figure question is fine when it says why.
    ("what is bitcoin worth right now",
     "I could not get a live quote — every source I opened was a background "
     "article rather than a price feed, so I am not going to guess at it.",
     {"had_evidence": True, "cited": 2}),
    # A recommendation that leads with the call and then names the conditions.
    # "Depends" appears, and must not fire: it qualifies a verdict that has
    # already been given rather than standing in for one.
    ("i have an iphone 13 should i upgrade to 18",
     "Not yet. Per Apple's own spec pages the 18 Pro's real gains over your "
     "13 are the 48MP main sensor and about 780mAh more battery, and at "
     "£1,199 that is a lot for a phone whose screen is 0.2in larger. How "
     "much it matters depends on whether your battery is already failing — "
     "if it is, upgrade now.",
     {"had_evidence": True, "cited": 5}),
    # Naming what the evidence did not cover, as a caveat after the answer.
    # Sanctioned by the evidence pack and must never be read as the defect.
    ("what is golden management consultancy ltd",
     "It is a small UK consultancy registered in London, trading since 2019 "
     "and offering operational-efficiency work to mid-market clients. The "
     "sources do not give a headcount or a turnover figure, so I cannot size "
     "it for you.",
     {"had_evidence": True, "cited": 3}),
    # A citation list is fine when there is a real answer above it.
    ("what changed between the iphone 13 and the 18",
     "Three things actually matter. The main camera goes from 12MP to 48MP, "
     "which is the biggest single jump. The A20 Pro is built on a 2nm process "
     "against the A15's 5nm, so sustained performance is where you will feel "
     "it rather than in benchmarks. And the battery goes from 3,279mAh to "
     "4,056mAh. Everything else — 0.2in of screen, 39g of extra weight — is "
     "noise.\n\nSources:\n\nApple newsroom, GSMArena teardown.",
     {"had_evidence": True, "cited": 2}),
    # A SHORT ranked list, where every entry is sourced and the shortfall is
    # stated once at the end. This is the shape the padding rule is meant to
    # produce, so it must never be what the padding rule fires on.
    ("top 10 unis in the uk",
     "Four of the ten positions are settled across the tables I opened:\n\n"
     "1. Oxford — first in the Times/Sunday Times Good University Guide 2027 "
     "and in the Complete University Guide.\n"
     "2. Cambridge — second in both.\n"
     "3. Imperial College London — third in the Complete University Guide.\n"
     "4. LSE — fourth in the Times guide.\n\n"
     "Positions five to ten differ between the two tables I could open, so I "
     "am not going to present them as a single ranking.",
     {"had_evidence": True, "cited": 2}),
    # A ranked list followed by a caveat about METHODOLOGY. The caveat uses the
    # same vocabulary as a padded entry but is about the ranking system, not
    # about whether an entry is evidenced.
    ("top 5 biggest companies in the world",
     "1. NVIDIA — $4.1tn (CEO Today, 2026-05-20).\n"
     "2. Alphabet — $3.4tn (same table).\n"
     "3. Apple — $3.3tn (same table).\n"
     "4. Microsoft — $3.1tn (same table).\n"
     "5. Amazon — $2.6tn (same table).\n\n"
     "These are by market capitalisation on 20 May 2026. A ranking by revenue "
     "is not the same list, and market caps move daily.",
     {"had_evidence": True, "cited": 1}),
    # A site visit, measured live on 2026-09-24: the answer names the page it
    # read and cites in the numbered dialect its lane writes. Not counting
    # either as attribution cost a repair round that came back unchanged.
    ("go to nvda website and tell me whats in there about us",
     "NVIDIA’s “About Us” page describes the company as a pioneer of accelerated "
     "computing, founded in 1993, whose invention of the GPU in 1999 sparked the "
     "growth of PC gaming.",
     {"had_evidence": True, "cited": 0}),
    ("what does nvidia say about itself",
     "NVIDIA says it invented the GPU in 1999【1†L4-L6】 and now builds full "
     "data-centre systems.",
     {"had_evidence": True, "cited": 0}),
    # Contains the whole question, and adds the one word it lacked: that is an
    # answer, not an echo. Flagged live, it bought a repair round.
    ("what is the capital of australia", "Canberra is the capital of Australia.[1]",
     {"had_evidence": True, "cited": 0}),
])
def test_a_good_answer_is_left_alone(question, answer, kw):
    verdict = check.run(question, answer, **kw)
    assert verdict.ok, f"fired on a good answer: {verdict.problems}"


def test_a_ranked_list_padded_out_of_memory_is_caught():
    """The entries disclaim themselves, which is what makes this detectable.

    Recorded from a live turn: two of six selected pages opened before the
    gathering window closed, and the answer produced all ten ranks anyway by
    filling the remainder from the model's own memory — each padded entry
    hedged in its own line. Half the list was unsourced and it read as a
    complete ranking.
    """
    answer = (
        "Here are the top 10 universities in the UK for the 2027 rankings:\n\n"
        "1. University of Manchester - topped the list according to WIONews.\n"
        "2. Imperial College London - ranks highly across multiple sources.\n"
        "3. University of Edinburgh - also ranks well in various league "
        "tables.\n"
        "4. Lancaster University - featured prominently in some rankings but "
        "not explicitly listed as top 10 in all sources.\n"
        "5. Keele University - consistently ranked among the top 20, often "
        "within the top 10 depending on the criteria.\n"
        "6. University of Bristol - ranks highly in multiple guides.\n")
    verdict = check.run("top 10 unis in the uk", answer,
                        had_evidence=True, cited=2)
    assert not verdict.ok
    problem = " ".join(verdict.problems).lower()
    assert "padded" in problem or "could be established" in problem


def test_the_padding_rule_needs_a_list_question():
    """The same hedges outside a ranked-list question are ordinary caution."""
    verdict = check.run(
        "is keele a good university",
        "Yes, on the measures that usually matter. Per the Complete "
        "University Guide it is consistently ranked among the top 20, often "
        "within the top 10 depending on the subject, and its student "
        "satisfaction scores are among the highest in the country.",
        had_evidence=True, cited=1)
    assert verdict.ok, verdict.problems


def test_a_long_answer_that_restates_the_question_first_is_fine():
    """Opening by restating the question is a style, not an echo."""
    verdict = check.run(
        "what is the capital of france",
        "The capital of France is Paris, and it has held that position since "
        "Hugh Capet made it his seat in 987.")
    assert verdict.ok


def test_the_repair_instruction_names_what_is_missing():
    verdict = check.run("just say yes or no, would you buy btc",
                        "Bitcoin is volatile.")
    message = check.repair_message(verdict)
    assert message["role"] == "system"
    body = message["content"].lower()
    assert "yes or no" in body
    assert "do not apologise" in body
    assert "do not mention this note" in body


def test_nothing_to_repair_produces_no_message():
    assert check.repair_message(check.Verdict()) is None


@pytest.mark.parametrize("answer", [
    "Correct, I cannot read or access any of your personal files without "
    "explicit permission.",
    "I don't have access to your documents, so I can only answer generally.",
    "I am not able to open attachments in this conversation.",
])
def test_denying_the_document_capability_is_caught(answer):
    """Every earlier branch of this rule needed an internet word, so a denial
    about FILES passed untouched — measured on the local lane, agreeing with a
    false premise put to it as a statement."""
    verdict = check.run("you cant read my files right", answer,
                        had_evidence=False, cited=0)
    assert not verdict.ok


def test_saying_a_specific_file_did_not_arrive_is_not_a_denial():
    """Reporting a real failure to read one document must still be allowed."""
    verdict = check.run(
        "whats in the pdf i sent",
        "The upload did not finish parsing — cv.pdf came through empty, so "
        "there is nothing for me to read yet. Send it again and I will open it.",
        had_evidence=False, cited=0)
    assert verdict.ok, verdict.problems
