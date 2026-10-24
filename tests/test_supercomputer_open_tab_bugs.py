"""Three bugs a live run found and left behind, 2026-07-27.

1. The Sources line printed the same URL twice, so one page read twice looked
   like two independent sources — the opposite of what a citation list is for.
2. The run had DBS's Group Executive Committee page OPEN in a tab and still
   reported the posts on it as not established. Having a page open is not
   reading it, and nothing noticed the difference.
3. Gmail was read from its Primary category tab and the whole inbox was
   reported empty. Primary is not the inbox.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from supercomputer_testkit import SupercomputerTestMixin  # noqa: E402

from syrix_supercomputer import agent, destinations, loop, results, tools  # noqa: E402


class TestOnePageIsOneSource(SupercomputerTestMixin, unittest.TestCase):
    def setUp(self):  # noqa: N802
        super().setUp()
        results.clear()

    def test_reading_the_same_page_twice_records_one_source(self):
        results.source("t1", "DBS Leadership", "https://www.dbs.com/leadership")
        results.source("t1", "DBS Leadership", "https://www.dbs.com/leadership")
        sources = [r for r in results.for_task("t1") if r["kind"] == "source"]
        self.assertEqual(len(sources), 1)

    def test_trivial_url_differences_are_the_same_page(self):
        for url in (
            "https://www.dbs.com/leadership",
            "https://www.dbs.com/leadership/",
            "HTTPS://WWW.DBS.COM/leadership",
            "https://www.dbs.com/leadership#board",
        ):
            results.source("t1", "DBS", url)
        self.assertEqual(
            len([r for r in results.for_task("t1") if r["kind"] == "source"]), 1)

    def test_a_query_string_still_makes_it_a_different_page(self):
        """Query strings are load-bearing on plenty of sites."""
        results.source("t1", "Search p1", "https://ex.com/s?page=1")
        results.source("t1", "Search p2", "https://ex.com/s?page=2")
        self.assertEqual(
            len([r for r in results.for_task("t1") if r["kind"] == "source"]), 2)

    def test_genuinely_different_pages_are_both_kept(self):
        results.source("t1", "Leadership", "https://www.dbs.com/leadership")
        results.source("t1", "Results", "https://www.dbs.com/investors")
        self.assertEqual(
            len([r for r in results.for_task("t1") if r["kind"] == "source"]), 2)

    def test_a_missing_title_is_filled_in_by_the_second_visit(self):
        results.source("t1", "", "https://www.dbs.com/leadership")
        results.source("t1", "DBS Leadership", "https://www.dbs.com/leadership")
        sources = [r for r in results.for_task("t1") if r["kind"] == "source"]
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["title"], "DBS Leadership")

    def test_the_report_does_not_repeat_a_source(self):
        results._RESULTS.setdefault("t1", [])
        # Bypass results.source to simulate an entry arriving from the evidence
        # store, which does not go through the same door.
        results.add("t1", results.KIND_SOURCE, "DBS", location="https://dbs.com/x")
        results.add("t1", results.KIND_SOURCE, "DBS", location="https://dbs.com/x/")
        text = agent.format_report({"task_id": "t1", "answer": "Done.", "done": [],
                                    "not_done": [], "declined": [], "notices": []})
        self.assertEqual(text.count("https://dbs.com/x"), 1)


class TestAnOpenTabIsNotAReadPage(SupercomputerTestMixin, unittest.TestCase):
    GOAL = "find the DBS Bank group executive committee members and their posts"

    def _ctx(self):
        return tools.ToolContext(task_id="t1", request_id="r1", config={})

    def _tabs(self, tabs):
        return mock.patch.object(
            loop.browser, "tabs",
            lambda config=None: {"ok": True, "data": {"tabs": tabs}})

    def test_it_spots_the_page_it_never_opened(self):
        tabs = [{"url": "https://www.dbs.com/group-executive-committee",
                 "title": "DBS Group Executive Committee"}]
        with self._tabs(tabs):
            found = loop._unread_relevant_tab(self.GOAL, set(), self._ctx())
        self.assertIsNotNone(found)
        self.assertIn("dbs.com", found[1])

    def test_a_page_already_read_is_not_flagged(self):
        url = "https://www.dbs.com/group-executive-committee"
        tabs = [{"url": url, "title": "DBS Group Executive Committee"}]
        with self._tabs(tabs):
            found = loop._unread_relevant_tab(self.GOAL, {url}, self._ctx())
        self.assertIsNone(found, "reading it is the whole point; do not nag")

    def test_trailing_slash_still_counts_as_read(self):
        tabs = [{"url": "https://www.dbs.com/group-executive-committee/",
                 "title": "DBS Group Executive Committee"}]
        with self._tabs(tabs):
            found = loop._unread_relevant_tab(
                self.GOAL, {"https://www.dbs.com/group-executive-committee"},
                self._ctx())
        self.assertIsNone(found)

    def test_an_unrelated_tab_is_ignored(self):
        tabs = [{"url": "https://news.ycombinator.com/", "title": "Hacker News"}]
        with self._tabs(tabs):
            self.assertIsNone(
                loop._unread_relevant_tab(self.GOAL, set(), self._ctx()))

    def test_one_shared_common_word_is_not_enough(self):
        """Two independent subject words, so a coincidence is not a match."""
        tabs = [{"url": "https://example.com/committee", "title": "Committee"}]
        with self._tabs(tabs):
            self.assertIsNone(
                loop._unread_relevant_tab(self.GOAL, set(), self._ctx()))

    def test_browser_internal_tabs_are_never_suggested(self):
        tabs = [{"url": "about:blank", "title": "DBS group executive committee"}]
        with self._tabs(tabs):
            self.assertIsNone(
                loop._unread_relevant_tab(self.GOAL, set(), self._ctx()))

    def test_no_tabs_open_is_not_an_error(self):
        with self._tabs([]):
            self.assertIsNone(
                loop._unread_relevant_tab(self.GOAL, set(), self._ctx()))

    def test_a_goal_of_only_stopwords_matches_nothing(self):
        tabs = [{"url": "https://www.dbs.com/x", "title": "The page for you"}]
        with self._tabs(tabs):
            self.assertIsNone(loop._unread_relevant_tab("find the page", set(),
                                                        self._ctx()))

    def test_the_verify_gate_pushes_back_on_it(self):
        ctx = self._ctx()
        report = {"goal": self.GOAL, "done": ["open_url"], "not_done": []}
        tabs = [{"url": "https://www.dbs.com/group-executive-committee",
                 "title": "DBS Group Executive Committee"}]
        with self._tabs(tabs):
            ok, message = loop._verify(
                "I could not establish who holds the other top posts at DBS.",
                ctx, report, pages_read=1, pushed_back=set(), urls_read=set())
        self.assertFalse(ok)
        self.assertIn("open already", message)

    def test_the_kill_switch_turns_it_off(self):
        ctx = self._ctx()
        ctx.config = {"supercomputer_unread_tab_gate_enabled": False}
        report = {"goal": self.GOAL, "done": ["open_url"], "not_done": []}
        tabs = [{"url": "https://www.dbs.com/group-executive-committee",
                 "title": "DBS Group Executive Committee"}]
        with self._tabs(tabs):
            _, message = loop._verify(
                "I could not establish who holds the other top posts at DBS.",
                ctx, report, pages_read=1,
                pushed_back={"half_done", "ungrounded"}, urls_read=set())
        self.assertNotIn("open already", message)

    def test_half_done_still_fires_when_there_is_no_unread_tab(self):
        """The new check was inserted ahead of half_done; half_done must remain."""
        ctx = self._ctx()
        report = {"goal": self.GOAL, "done": ["open_url"], "not_done": []}
        with self._tabs([]):
            ok, message = loop._verify(
                "I could not establish who holds the other top posts at DBS.",
                ctx, report, pages_read=1, pushed_back=set(),
                urls_read={"https://www.dbs.com/leadership"})
        self.assertFalse(ok)
        self.assertIn("You have read the source", message)

    def test_a_confident_answer_is_not_pushed_back(self):
        """The gate triggers on giving up, not on every finish."""
        ctx = self._ctx()
        report = {"goal": self.GOAL, "done": ["open_url"], "not_done": []}
        tabs = [{"url": "https://www.dbs.com/group-executive-committee",
                 "title": "DBS Group Executive Committee"}]
        with self._tabs(tabs):
            ok, _ = loop._verify(
                "The DBS group executive committee is chaired by Tan Su Shan, "
                "with Chng Sok Hui as Chief Financial Officer.",
                ctx, report, pages_read=1, pushed_back=set(),
                urls_read={"https://www.dbs.com/group-executive-committee"})
        self.assertTrue(ok)

    def test_complete_comparison_cannot_finish_with_an_unread_page_window(self):
        ctx = self._ctx()
        ctx.next_read_offset = 2600
        report = {
            "goal": "Compare every 2026 bank-holiday date in all three jurisdictions, "
                    "including totals and unique dates.",
            "done": ["open_url", "read_page"], "not_done": [],
        }
        with self._tabs([]):
            ok, message = loop._verify(
                "England and Wales has 8 dates; Scotland has 9; Northern Ireland has 10.",
                ctx, report, pages_read=1, pushed_back=set(), urls_read=set())
        self.assertFalse(ok)
        self.assertIn("offset 2600", message)

    def test_single_fact_question_can_finish_with_later_page_text_unread(self):
        ctx = self._ctx()
        ctx.next_read_offset = 2600
        report = {"goal": "What is the next England bank holiday?",
                  "done": ["open_url", "read_page"], "not_done": []}
        self.assertFalse(loop._requires_complete_page_scan(report["goal"]))


class TestPrimaryIsNotTheInbox(unittest.TestCase):
    def test_my_inbox_goes_to_every_category(self):
        for phrase in ("my inbox", "inbox", "my email", "my emails"):
            self.assertEqual(destinations.SERVICES[phrase],
                             "https://mail.google.com/mail/u/0/#search/in%3Ainbox",
                             phrase)

    def test_open_gmail_still_opens_gmail(self):
        """Asking for the app should land where a person expects."""
        self.assertEqual(destinations.SERVICES["gmail"], "https://mail.google.com/")

    def test_the_inbox_url_is_still_on_the_gmail_host(self):
        """It must pass the same host checks the plain destination does."""
        self.assertEqual(
            destinations.host_of(destinations.SERVICES["my inbox"]),
            destinations.host_of(destinations.SERVICES["gmail"]))

    def test_reading_gmail_warns_that_it_is_one_category_only(self):
        note = tools._site_note("https://mail.google.com/mail/u/0/#inbox", "")
        self.assertIn("category tabs", note)
        self.assertIn("NOT the whole inbox", note)

    def test_the_warning_reaches_the_page_the_model_reads(self):
        page = {"url": "https://mail.google.com/mail/u/0/#inbox",
                "title": "Inbox", "text": "Primary  Promotions  Social"}
        rendered = tools._fmt_page(page)
        self.assertIn("NOT the whole inbox", rendered)

    def test_other_sites_get_no_gmail_note(self):
        self.assertEqual(tools._site_note("https://www.dbs.com/leadership", ""), "")


class TestLongPageReadWindows(unittest.TestCase):
    def test_truncated_page_gives_the_next_read_offset(self):
        page = {
            "url": "https://www.gov.uk/bank-holidays",
            "title": "UK bank holidays - GOV.UK",
            "text": "x" * 2600,
            "meta": {
                "truncated": True,
                "textOffset": 0,
                "textWindowChars": 2600,
            },
        }
        rendered = tools._fmt_page(page)
        self.assertIn("read_page with offset 2600", rendered)


if __name__ == "__main__":
    unittest.main()
