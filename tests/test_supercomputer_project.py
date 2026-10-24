"""The project graph: stages, dependencies, budgets and checkpoints.

Two things are being pinned. That a goal which genuinely decomposes gets
structure — because the single loop demonstrably cannot hold ten subjects in
one trimmed transcript. And that a goal which does not decompose gets NO
structure, because paying for a scheduler on "read this page" would make the
common case worse to fix the rare one.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from syrix_supercomputer import evidence, project  # noqa: E402


class _Case(unittest.TestCase):
    def setUp(self):
        project.forget_all()
        evidence.forget_all()
        self._dir = tempfile.TemporaryDirectory()
        from syrix_supercomputer import paths

        self._patch = mock.patch.object(paths, "memory_dir", lambda: self._dir.name)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._dir.cleanup()
        project.forget_all()
        evidence.forget_all()


class TestWhenToUseAProject(_Case):
    def test_a_simple_request_gets_no_project(self):
        for goal in ("Read this page and tell me what it says.",
                     "What is the price of Apple stock?",
                     "Summarise this article.",
                     ""):
            self.assertIsNone(project.plan("t1", goal), goal)

    def test_a_ten_stock_report_fans_out(self):
        p = project.plan("t1", "Give me a report on 10 stocks covering the trend, "
                               "key levels, the RSI reading and a verdict for each.")
        self.assertIsNotNone(p)
        units = [s for s in p.stages if s.kind == project.KIND_BROWSE]
        self.assertEqual(len(units), 10)
        # Every unit is independent — none waits on another.
        self.assertTrue(all(not s.needs for s in units))

    def test_several_questions_about_ONE_subject_get_no_project(self):
        """They share their sources. Splitting them multiplied the page loads
        and divided the context — 615s for a three-part question one loop
        answers off one page, and a worse answer at the end."""
        self.assertIsNone(project.plan(
            "t1",
            "Research this company and tell me what it does, how it makes money, "
            "who the current CEO is, its main competitors and its risks."))

    def test_an_artefact_request_adds_an_artifact_stage_after_the_gathering(self):
        p = project.plan(
            "t1",
            "Give me a report on 4 stocks covering the trend and a verdict for "
            "each, and create a professional PDF of it.")
        self.assertIsNotNone(p)
        artifact = p.stage("artifact")
        self.assertIsNotNone(artifact)
        self.assertEqual(artifact.kind, project.KIND_ARTIFACT)
        gather = [s.id for s in p.stages if s.kind == project.KIND_BROWSE]
        self.assertEqual(sorted(artifact.needs), sorted(gather))

    def test_every_project_ends_with_a_review_stage(self):
        p = project.plan("t1", "Give me a report on 3 stocks covering the trend "
                               "and a verdict for each.")
        self.assertEqual(p.stages[-1].kind, project.KIND_REVIEW)

    def test_the_kill_switch_returns_the_single_loop(self):
        goal = "Give me a report on 10 stocks covering trend, levels and a verdict."
        self.assertIsNotNone(project.plan("t1", goal))
        project.forget_all()
        self.assertIsNone(project.plan(
            "t1", goal, config={"supercomputer_project_enabled": False}))


class TestFanoutDetection(_Case):
    def test_counts(self):
        self.assertEqual(project.fanout_count("a report on 10 stocks"), 10)
        self.assertEqual(project.fanout_count("compare 5 companies"), 5)
        self.assertEqual(project.fanout_count("read this page"), 0)

    def test_a_silly_count_is_capped(self):
        self.assertLessEqual(project.fanout_count("all 99 pages"), 12)

    def test_subjects_replace_the_placeholder_labels(self):
        p = project.plan("t1", "Report on 3 stocks covering the trend, the levels "
                               "and a verdict for each.")
        named = project.set_subjects("t1", ["AAPL", "MSFT", "NVDA"])
        self.assertEqual(named, 3)
        labels = [s.label() for s in p.stages if s.id.startswith("unit")]
        self.assertIn("AAPL", " ".join(labels))


class TestScheduling(_Case):
    def _graph(self):
        return project.plan(
            "t1",
            "Give me a report on 4 stocks covering the trend and a verdict for "
            "each, and create a PDF of it.")

    def test_only_dependency_free_stages_are_ready_first(self):
        p = self._graph()
        ready = {s.id for s in p.ready()}
        self.assertNotIn("artifact", ready)
        self.assertNotIn("review", ready)
        self.assertTrue(ready)

    def test_a_dependant_unlocks_when_its_dependencies_finish(self):
        p = self._graph()
        for s in p.stages:
            if s.kind == project.KIND_BROWSE:
                project.set_stage_status("t1", s.id, project.DONE)
        self.assertIn("artifact", {s.id for s in p.ready()})

    def test_a_blocked_dependency_still_unlocks_the_write_up(self):
        """A gated source must not silently cancel the report — the report runs
        and states the gap."""
        p = self._graph()
        research = [s for s in p.stages if s.kind == project.KIND_BROWSE]
        project.set_stage_status("t1", research[0].id, project.BLOCKED,
                                 detail="login wall")
        for s in research[1:]:
            project.set_stage_status("t1", s.id, project.DONE)
        self.assertIn("artifact", {s.id for s in p.ready()})

    def test_progress_and_completion(self):
        p = self._graph()
        self.assertEqual(p.progress()[0], 0)
        for s in list(p.stages):
            project.set_stage_status("t1", s.id, project.DONE)
        self.assertTrue(p.is_complete())
        self.assertEqual(p.progress()[0], p.progress()[1])

    def test_only_read_only_kinds_may_run_in_parallel(self):
        self.assertIn(project.KIND_RESEARCH, project.PARALLEL_KINDS)
        self.assertIn(project.KIND_COMPUTE, project.PARALLEL_KINDS)
        # The browser driver is the one thing that must never be concurrent.
        self.assertNotIn(project.KIND_BROWSE, project.PARALLEL_KINDS)
        self.assertNotIn(project.KIND_ARTIFACT, project.PARALLEL_KINDS)
        self.assertNotIn(project.KIND_REVIEW, project.PARALLEL_KINDS)


class TestBudgets(_Case):
    def test_the_project_stops_at_its_step_ceiling(self):
        p = project.plan("t1", "Report on 4 stocks covering trend, levels and a verdict.")
        self.assertEqual(p.out_of_budget(), "")
        project.note_steps("t1", p.stages[0].id, p.max_steps)
        self.assertIn("step ceiling", p.out_of_budget())

    def test_the_project_stops_at_its_time_budget(self):
        p = project.plan("t1", "Report on 4 stocks covering trend, levels and a verdict.")
        p.started_ts -= (p.max_seconds + 1)
        self.assertIn("time budget", p.out_of_budget())

    def test_steps_are_counted_against_the_stage_and_the_project(self):
        p = project.plan("t1", "Report on 4 stocks covering trend, levels and a verdict.")
        sid = p.stages[0].id
        project.note_steps("t1", sid, 5)
        self.assertEqual(p.stage(sid).steps_used, 5)
        self.assertEqual(p.steps_used, 5)


class TestStageGoals(_Case):
    def test_a_stage_is_told_to_do_only_its_own_part(self):
        p = project.plan("t1", "Give me a report on 3 stocks covering the trend "
                               "and a verdict for each.")
        stage = [s for s in p.stages if s.kind == project.KIND_BROWSE][0]
        text = project.stage_goal(p, stage)
        self.assertIn("the only thing to do now", text)
        # The request leads, so a fragment never loses its subject.
        self.assertLess(text.index("THE REQUEST"), text.index("YOUR PART"))
        self.assertIn(stage.title, text)

    def test_the_review_stage_is_told_to_use_what_was_established(self):
        p = project.plan("t1", "Give me a report on 3 stocks covering the trend "
                               "and a verdict for each.")
        text = project.stage_goal(p, p.stage("review"))
        self.assertIn("ONLY what the task established", text)


class TestCheckpoints(_Case):
    def test_a_project_survives_a_restart(self):
        p = project.plan("t1", "Report on 4 stocks covering trend, levels and a verdict.")
        project.set_stage_status("t1", p.stages[0].id, project.DONE)
        project.note_steps("t1", p.stages[0].id, 7)
        self.assertTrue(project.save("t1"))
        project.forget_all()
        self.assertIsNone(project.get("t1"))

        back = project.load("t1")
        self.assertIsNotNone(back)
        self.assertEqual(back.stage(p.stages[0].id).status, project.DONE)
        self.assertEqual(back.steps_used, 7)

    def test_a_stage_caught_mid_flight_is_pending_again_not_running(self):
        p = project.plan("t1", "Report on 4 stocks covering trend, levels and a verdict.")
        project.set_stage_status("t1", p.stages[0].id, project.RUNNING)
        project.save("t1")
        project.forget_all()
        back = project.load("t1")
        self.assertEqual(back.stage(p.stages[0].id).status, project.PENDING)

    def test_loading_a_project_that_was_never_saved_is_None(self):
        self.assertIsNone(project.load("never-existed"))

    def test_the_checkpoint_carries_no_authority(self):
        """Work product only. A checkpoint must never be able to re-grant a
        permission that died with the session."""
        p = project.plan("t1", "Report on 4 stocks covering trend, levels and a verdict.")
        project.save("t1")
        import json

        with open(project._path("t1"), encoding="utf-8") as fh:
            raw = json.load(fh)
        blob = json.dumps(raw).lower()
        for forbidden in ("grant", "permission", "scope", "token", "secret",
                          "boot_id", "allow"):
            self.assertNotIn(forbidden, blob)


class TestSnapshot(_Case):
    def test_snapshot_is_renderable(self):
        project.plan("t1", "Give me a report on 3 stocks covering the trend "
                           "and a verdict for each.")
        snap = project.snapshot("t1")
        self.assertIn("stages", snap)
        self.assertEqual(snap["done"], 0)
        self.assertGreaterEqual(snap["total"], 3)
        for row in snap["stages"]:
            self.assertIn(row["kind"], project.KINDS)

    def test_snapshot_of_nothing_is_empty(self):
        self.assertEqual(project.snapshot("nope"), {})


if __name__ == "__main__":
    unittest.main()
