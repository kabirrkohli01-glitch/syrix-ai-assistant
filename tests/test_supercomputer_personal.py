"""Never invent the user's own facts — ask for them.

"Read my CV and my grades, check my UCAS points, find universities I could get
into." If the CV cannot be found and the grades were never read, there are two
honest moves — find them, or ask. A language model will take a third: write a
plausible set and carry the whole task forward on them. The shortlist is then
built on fiction and the application is filled with it, and it looks like
success the entire way.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from syrix_supercomputer import personal  # noqa: E402


UCAS_GOAL = ("Read my existing CV and my grades, improve the CV, open UCAS and "
             "check my qualifications and calculated points, then shortlist "
             "universities near London I could realistically get into.")


class TestRequiredInputs(unittest.TestCase):
    def test_a_possessive_reference_is_a_required_input(self):
        needed = personal.required_inputs(UCAS_GOAL)
        self.assertIn("cv", needed)
        self.assertIn("grades", needed)
        self.assertIn("qualifications", needed)

    def test_someone_elses_cv_is_not_boss_data(self):
        self.assertEqual(personal.required_inputs(
            "Find the CV format most recruiters prefer."), [])
        self.assertEqual(personal.required_inputs(
            "What grades do you need for a computer science degree?"), [])

    def test_no_personal_reference_needs_nothing(self):
        for goal in ("Read this page and summarise it.",
                     "Compare the best voice APIs.", "", None):
            self.assertEqual(personal.required_inputs(goal), [])

    def test_an_assignment_in_my_course_counts(self):
        self.assertIn("course", personal.required_inputs(
            "Do my assignment and upload it to my course page."))


class TestSourcing(unittest.TestCase):
    def test_reading_his_file_counts_as_sourcing(self):
        self.assertTrue(personal.was_sourced(["read_my_file (downloads/cv.pdf)"]))
        self.assertTrue(personal.was_sourced(["list_my_files"]))

    def test_a_signed_in_account_page_counts(self):
        self.assertTrue(personal.was_sourced(["open_service (ucas)"]))

    def test_browsing_the_open_web_does_not(self):
        self.assertFalse(personal.was_sourced(
            ["web_search", "open_url (https://ucas.com)", "read_page", "scroll"]))

    def test_nothing_performed_is_not_sourced(self):
        self.assertFalse(personal.was_sourced([]))
        self.assertFalse(personal.was_sourced(None))


class TestDetectingInventedData(unittest.TestCase):
    def test_a_grade_string_is_a_factual_claim(self):
        self.assertTrue(personal.states_personal_data("You have A*, A, B."))
        self.assertTrue(personal.states_personal_data("That gives 128 UCAS points."))
        self.assertTrue(personal.states_personal_data("Your grades of A in maths"))

    def test_prose_with_no_datum_is_not(self):
        self.assertFalse(personal.states_personal_data(
            "I could not find your CV in Downloads, Documents or Desktop."))
        self.assertFalse(personal.states_personal_data(
            "Entry requirements are typically three A-levels."))

    def test_invented_grades_are_caught(self):
        missing = personal.unsourced(
            UCAS_GOAL,
            "Based on your grades of A*, A, B you have 144 UCAS points, so "
            "Queen Mary is realistic.",
            ["web_search", "open_url (https://ucas.com)", "read_page"])
        self.assertIn("grades", missing)

    def test_real_grades_read_from_his_file_are_fine(self):
        missing = personal.unsourced(
            UCAS_GOAL,
            "Based on your grades of A*, A, B you have 144 UCAS points.",
            ["read_my_file (downloads/results.pdf)", "open_url"])
        self.assertEqual(missing, [])

    def test_honestly_reporting_that_it_could_not_find_them_is_not_pushed_back(self):
        """Saying so is the correct behaviour and must never be punished."""
        missing = personal.unsourced(
            UCAS_GOAL,
            "I could not find your CV or your results anywhere in Downloads, "
            "Documents or Desktop, so I have not guessed at them. Tell me where "
            "they are and I will carry on.",
            ["list_my_files"])
        self.assertEqual(missing, [])

    def test_a_task_with_no_personal_input_is_never_flagged(self):
        self.assertEqual(personal.unsourced(
            "Compare the best voice APIs on latency and pricing.",
            "ElevenLabs is 75ms; Deepgram is 120ms. That is 128 points clear.",
            ["web_search"]), [])


class TestMessages(unittest.TestCase):
    def test_the_question_asks_for_something_answerable(self):
        text = personal.question_for(["cv", "grades"])
        self.assertIn("cv", text)
        self.assertIn("grades", text)
        self.assertIn("Downloads", text)
        # It must not read as a refusal.
        self.assertNotIn("cannot help", text.lower())

    def test_the_pushback_forbids_writing_a_plausible_version(self):
        text = personal.pushback(["grades"])
        self.assertIn("ask_user", text)
        self.assertIn("read_my_file", text)
        self.assertIn("plausible", text)

    def test_empty_inputs_produce_no_question(self):
        self.assertEqual(personal.question_for([]), "")


class TestKillSwitch(unittest.TestCase):
    def test_the_guard_is_on_by_default_and_can_be_turned_off(self):
        self.assertTrue(personal.guard_enabled({}))
        self.assertTrue(personal.guard_enabled(None))
        self.assertFalse(personal.guard_enabled(
            {"supercomputer_personal_data_guard_enabled": False}))


if __name__ == "__main__":
    unittest.main()
