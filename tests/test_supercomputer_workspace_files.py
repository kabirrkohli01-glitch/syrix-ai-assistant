"""Coding workspace containment, the local preview, and the task loop.

Containment is the whole safety story for Phase 7: "write a file" must never
become "write any file". It is enforced by realpath resolution rather than by
convention, so the tests that matter are the escape attempts — traversal,
absolute paths, and symlinks that point outside.
"""
from __future__ import annotations

import os
import sys
import unittest
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from supercomputer_testkit import SupercomputerTestMixin  # noqa: E402

from syrix_supercomputer import agent, preview, results, tasks, workspace  # noqa: E402
from syrix_supercomputer.browser import policy  # noqa: E402
from syrix_supercomputer.contract import TaskContract  # noqa: E402


class TestContainment(SupercomputerTestMixin, unittest.TestCase):
    def test_a_normal_relative_path_resolves_inside(self):
        target, reason = workspace.resolve("t1", "index.html")
        self.assertIsNotNone(target, reason)
        self.assertTrue(target.startswith(os.path.realpath(workspace.root_for("t1"))))

    def test_traversal_is_refused(self):
        for rel in ("../escape.txt", "a/../../escape.txt", "..\\escape.txt",
                    "sub/../../../../Windows/System32/x.txt"):
            target, reason = workspace.resolve("t1", rel)
            self.assertIsNone(target, f"{rel} escaped the workspace")
            self.assertEqual(reason, "outside_workspace")

    def test_absolute_paths_are_refused(self):
        for rel in (r"C:\Windows\System32\drivers\etc\hosts", "/etc/passwd"):
            target, reason = workspace.resolve("t1", rel)
            self.assertIsNone(target)
            self.assertEqual(reason, "absolute_path_refused")

    def test_executable_file_types_are_refused(self):
        for rel in ("run.exe", "go.bat", "x.ps1", "hook.cmd", "s.vbs", "k.reg"):
            target, reason = workspace.resolve("t1", rel)
            self.assertIsNone(target, f"{rel} should be refused")
            self.assertTrue(reason.startswith("refused_file_type"))

    def test_writing_outside_the_workspace_fails_and_creates_nothing(self):
        res = workspace.write("t1", "../escaped.txt", "nope")
        self.assertFalse(res["ok"])
        escaped = os.path.join(os.path.dirname(workspace.root_for("t1")), "escaped.txt")
        self.assertFalse(os.path.exists(escaped))

    def test_two_tasks_cannot_see_each_other(self):
        workspace.write("t1", "secret.txt", "mine")
        self.assertEqual(workspace.listing("t2"), [])
        self.assertFalse(workspace.read("t2", "secret.txt")["ok"])

    def test_syrix_repo_is_recognised_so_it_can_be_refused(self):
        self.assertTrue(workspace.is_syrix_path(os.path.join(workspace.syrix_repo_root(),
                                                             "syrix_host.py")))
        self.assertFalse(workspace.is_syrix_path(workspace.root_for("t1")))

    def test_a_workspace_path_is_never_inside_syrix(self):
        """Generated code must not be able to land in the assistant's own repo."""
        target, _ = workspace.resolve("t1", "app.py")
        self.assertFalse(workspace.is_syrix_path(target))


class TestFileOps(SupercomputerTestMixin, unittest.TestCase):
    def test_write_then_read_round_trips(self):
        res = workspace.write("t1", "index.html", "<h1>Hi</h1>\n")
        self.assertTrue(res["ok"])
        self.assertEqual(res["data"]["lines"], 1)
        self.assertFalse(res["data"]["replaced"])
        got = workspace.read("t1", "index.html")
        self.assertEqual(got["data"]["content"], "<h1>Hi</h1>\n")

    def test_rewriting_reports_that_it_replaced(self):
        workspace.write("t1", "a.txt", "one")
        res = workspace.write("t1", "a.txt", "two")
        self.assertTrue(res["data"]["replaced"])

    def test_nested_directories_are_created(self):
        res = workspace.write("t1", "assets/css/site.css", "body{}")
        self.assertTrue(res["ok"])
        self.assertIn("assets/css/site.css", [f["relative"] for f in workspace.listing("t1")])

    def test_oversized_files_are_refused(self):
        res = workspace.write("t1", "big.txt", "x" * (workspace.MAX_FILE_BYTES + 1))
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "file_too_large")

    def test_missing_file_reads_as_not_found(self):
        self.assertEqual(workspace.read("t1", "nope.txt")["reason"], "not_found")


class TestPreviewServer(SupercomputerTestMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        workspace.write("t1", "index.html", "<h1>Preview</h1>")
        workspace.write("t1", "secret.txt", "not served without the token")
        started = preview.start("t1")
        self.assertTrue(started["ok"], started.get("reason"))
        self.url = started["data"]["url"]
        self.origin = started["data"]["origin"]
        self.addCleanup(preview.stop)

    def fetch(self, url):
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, ""

    def test_it_binds_loopback_only(self):
        self.assertTrue(self.origin.startswith("127.0.0.1:"))

    def test_the_token_path_serves_the_workspace(self):
        status, body = self.fetch(self.url)
        self.assertEqual(status, 200)
        self.assertIn("Preview", body)

    def test_without_the_token_nothing_is_served(self):
        status, _ = self.fetch(f"http://{self.origin}/index.html")
        self.assertEqual(status, 404)

    def test_a_wrong_token_is_not_distinguishable_from_a_missing_file(self):
        status, _ = self.fetch(f"http://{self.origin}/wrongtoken/index.html")
        self.assertEqual(status, 404)

    def test_traversal_through_the_preview_is_refused(self):
        base = self.url.rstrip("/")
        status, _ = self.fetch(f"{base}/../../../../Windows/win.ini")
        self.assertEqual(status, 404)

    def test_only_web_content_types_are_served(self):
        workspace.write("t1", "notes.bin", "x")
        base = self.url.rstrip("/")
        # .bin is not in the allow-list, so it is simply not there.
        status, _ = self.fetch(f"{base}/notes.bin")
        self.assertEqual(status, 404)

    def test_the_preview_is_the_only_loopback_exception(self):
        """Starting it must not open the loopback interface generally."""
        self.assertEqual(policy.preview_origin(), self.origin)
        ok, _ = policy.check_url(f"http://{self.origin}/x")
        self.assertTrue(ok)
        for other in ("http://127.0.0.1:9/x", "http://localhost:8080/",
                      "http://192.168.1.1/"):
            blocked, reason = policy.check_url(other)
            self.assertFalse(blocked, f"{other} must stay blocked")

    def test_stopping_revokes_the_exception(self):
        preview.stop()
        self.assertEqual(policy.preview_origin(), "")
        ok, reason = policy.check_url(f"http://{self.origin}/x")
        self.assertFalse(ok)
        self.assertEqual(reason, "private_network")


class TestProjectParsing(unittest.TestCase):
    def test_the_file_envelope_is_split(self):
        text = (
            "=== FILE: index.html ===\n<h1>Hi</h1>\n"
            "=== FILE: style.css ===\nbody{margin:0}\n"
        )
        files = agent.parse_files(text)
        self.assertEqual(set(files), {"index.html", "style.css"})
        self.assertIn("<h1>Hi</h1>", files["index.html"])

    def test_stray_code_fences_are_stripped(self):
        text = "=== FILE: a.js ===\n```js\nconst x = 1;\n```\n"
        self.assertEqual(agent.parse_files(text)["a.js"], "const x = 1;")

    def test_escaping_names_are_dropped(self):
        text = "=== FILE: ../evil.txt ===\nbad\n=== FILE: /etc/passwd ===\nbad\n"
        self.assertEqual(agent.parse_files(text), {})

    def test_empty_output_yields_no_files(self):
        self.assertEqual(agent.parse_files(""), {})
        self.assertEqual(agent.parse_files("I cannot help with that."), {})


class TestTaskPlanning(unittest.TestCase):
    def test_build_requests_are_classified_as_build(self):
        for text in ("build me a website like this", "create a landing page",
                     "make a dashboard app"):
            self.assertEqual(agent.classify(text, []), agent.KIND_BUILD)

    def test_research_requests_are_classified_as_browse(self):
        for text in ("open example.com and summarise", "read the news on bbc.com"):
            self.assertEqual(agent.classify(text, []), agent.KIND_BROWSE)

    def test_the_plan_is_seeded_before_anything_runs(self):
        labels = agent.plan_labels(agent.KIND_BROWSE, ["https://a.com", "https://b.com"])
        self.assertGreater(len(labels), 3)
        self.assertTrue(any("Open a.com" in l for l in labels))
        self.assertTrue(any("summarise" in l.lower() for l in labels))

    def test_the_contract_scope_comes_from_the_users_message(self):
        """The user's own destinations, plus the search engine (a task allowed
        to look things up must reach the engine it looks them up on). Nothing
        else — and nothing a page suggested."""
        c = agent.build_contract("t1", "goal", ["https://a.com/x"], {})
        self.assertIn("a.com", c.allowed_domains)
        self.assertIn("google.com", c.allowed_domains)
        self.assertNotIn("evil.example", c.allowed_domains)

    def test_a_service_named_in_the_request_is_in_scope(self):
        c = agent.build_contract("t1", "open youtube and find the video", [], {})
        self.assertIn("youtube.com", c.allowed_domains)


class TestReportHonesty(SupercomputerTestMixin, unittest.TestCase):
    def test_a_report_with_nothing_done_says_so(self):
        text = agent.format_report({"task_id": "t1", "done": [], "not_done": [],
                                    "declined": [], "answer": "", "notices": []})
        self.assertIn("did not manage", text.lower())

    def test_declines_are_reported_separately_from_failures(self):
        text = agent.format_report({
            "task_id": "t1", "done": ["opened a.com"], "not_done": ["read b.com (timeout)"],
            "declined": ["send the email"], "answer": "", "notices": [],
        })
        self.assertIn("You declined:", text)
        self.assertIn("send the email", text)
        self.assertIn("Not done:", text)
        self.assertIn("read b.com", text)

    def test_created_files_are_listed_with_their_location(self):
        results.file_created("t1", r"C:\ws\t1\index.html")
        text = agent.format_report({"task_id": "t1", "done": ["created 1 file"],
                                    "not_done": [], "declined": [], "answer": "",
                                    "notices": []})
        self.assertIn("Files created:", text)
        self.assertIn("index.html", text)

    def test_a_preview_url_is_offered_for_inspection(self):
        text = agent.format_report({"task_id": "t1", "done": [], "not_done": [],
                                    "declined": [], "answer": "", "notices": [],
                                    "preview_url": "http://127.0.0.1:1234/tok/"})
        self.assertIn("127.0.0.1:1234", text)
        self.assertIn("take control", text.lower())


if __name__ == "__main__":
    unittest.main()
