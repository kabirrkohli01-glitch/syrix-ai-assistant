"""When the user says HOW to do it, that is the method — not a suggestion.

"Just go and search the DBS website." "Do it in the tab that's already open."
The mode heard the subject, ignored the route, and ran nine Google searches
anyway — arriving somewhere reasonable by a path he had explicitly ruled out,
slowly. He gave the instruction because he knows which source is
authoritative and which page he is looking at.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from syrix_supercomputer import directives  # noqa: E402


class TestExtraction(unittest.TestCase):
    def test_use_the_open_tab(self):
        for text in ("Do it in the tab that is already open.",
                     "Use the page already open.",
                     "Answer from this page only.",
                     "Don't open anything new — just read it."):
            self.assertIn(directives.USE_OPEN_TAB, directives.extract(text), text)

    def test_do_not_search(self):
        for text in ("Do not google it.", "Without searching, tell me.",
                     "Don't search for it."):
            self.assertTrue(directives.forbids_search(text), text)

    def test_a_named_site(self):
        self.assertEqual(directives.named_site(
            "Just go and search the DBS website."), "DBS")
        self.assertEqual(directives.named_site(
            "Only use bbc.co.uk for this."), "bbc.co.uk")

    def test_speed(self):
        self.assertTrue(directives.wants_speed("Quickly tell me what this says."))
        self.assertTrue(directives.wants_speed("Just tell me in one line."))
        self.assertFalse(directives.wants_speed("Research this thoroughly."))

    def test_saying_only_WHAT_leaves_the_route_to_the_agent(self):
        """The half that must not change: with no method given, working it out
        is the whole point of the mode."""
        for text in ("Look up DBS Bank and tell me who their CEO is.",
                     "Compare these three GPUs on price.",
                     "Research this company and write me a PDF."):
            self.assertEqual(directives.extract(text), [], text)
            self.assertEqual(directives.scope_note(text), "", text)

    def test_description_is_not_an_order(self):
        """"the page is already open" states a fact; it does not tell the agent
        to confine itself to it."""
        self.assertNotIn(directives.USE_OPEN_TAB,
                         directives.extract("The page is already open, by the way."))


class TestScopeNote(unittest.TestCase):
    def test_the_note_is_framed_as_an_instruction(self):
        note = directives.scope_note("Do it in the tab that is already open.")
        self.assertIn("instructions, not", note)
        self.assertIn("override", note)
        self.assertIn("read_page", note)

    def test_the_open_tab_beats_a_named_site(self):
        """They contradict; the tab is the more specific thing he is pointing
        at."""
        note = directives.scope_note(
            "Use the tab already open, not the bbc.co.uk one.")
        self.assertIn("ALREADY OPEN", note)
        self.assertNotIn("bbc.co.uk", note)

    def test_a_named_site_sends_it_there_but_allows_an_honest_fallback(self):
        note = directives.scope_note("Only use bbc.co.uk for this.")
        self.assertIn("bbc.co.uk", note)
        self.assertIn("say so first", note)

    def test_quick_asks_for_the_shortest_honest_route(self):
        note = directives.scope_note("Quickly, what does this page say?")
        self.assertIn("shortest honest route", note)
        self.assertIn("thoroughness is not what he wants", note)

    def test_junk_never_raises(self):
        for text in (None, "", 12345, "?" * 800):
            self.assertIsInstance(directives.scope_note(text), str)


class TestKillSwitch(unittest.TestCase):
    def test_default_on_and_switchable(self):
        self.assertTrue(directives.enabled({}))
        self.assertFalse(directives.enabled(
            {"supercomputer_directives_enabled": False}))


class TestPlannerThreshold(unittest.TestCase):
    """Several questions about ONE thing is not a project.

    "What DBS does, how they make money and who their CEO is" became five
    stages, each starting blank, each re-opening the same pages: 615 seconds
    for something one loop answers off one page, and a worse answer because no
    stage saw the whole picture.
    """

    def setUp(self):
        from syrix_supercomputer import project

        self.project = project
        project.forget_all()

    def tearDown(self):
        self.project.forget_all()

    def test_sections_about_one_subject_stay_in_the_single_loop(self):
        self.assertIsNone(self.project.plan(
            "t1", "Look up DBS Bank and tell me what they do, how they make "
                  "money and who their CEO is."))

    def test_even_with_an_artefact_one_subject_stays_in_the_loop(self):
        self.assertIsNone(self.project.plan(
            "t1", "Research this company and create a PDF covering its "
                  "products, leadership and risks."))

    def test_genuinely_separate_work_still_gets_a_graph(self):
        p = self.project.plan(
            "t1", "Give me a report on 10 stocks covering the trend, the levels "
                  "and a verdict for each.")
        self.assertIsNotNone(p)
        self.assertEqual(
            len([s for s in p.stages if s.kind == self.project.KIND_BROWSE]), 10)


if __name__ == "__main__":
    unittest.main()
