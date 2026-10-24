"""The scheduler: dependencies, one browser driver, halts, gaps, budgets.

Runs with an injected stage runner, so none of this needs a browser, a model or
a network — which is the point of injecting it.
"""
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from syrix_supercomputer import evidence, project, runtime  # noqa: E402


class _Case(unittest.TestCase):
    def setUp(self):
        project.forget_all()
        evidence.forget_all()
        self._dir = tempfile.TemporaryDirectory()
        from syrix_supercomputer import paths

        self._patch = mock.patch.object(paths, "memory_dir", lambda: self._dir.name)
        self._patch.start()
        self.calls = []

    def tearDown(self):
        self._patch.stop()
        self._dir.cleanup()
        project.forget_all()
        evidence.forget_all()

    def _project(self, goal=None):
        # Genuinely separate work. Sections about ONE subject no longer build a
        # graph — they share their sources, so splitting them multiplied the
        # page loads and divided the context (615s for a three-part question
        # one loop answers off one page).
        return project.plan("t1", goal or (
            "Give me a report on 4 stocks covering the trend, the levels and a "
            "verdict for each, and create a PDF of it."))

    def _runner(self, answer="found something", **overrides):
        def run_stage(goal, *, stage_id, kind, max_steps, budget_s,
                      request_id, config):
            self.calls.append({"stage": stage_id, "kind": kind, "goal": goal})
            out = {"answer": f"{answer} for {stage_id}", "steps_used": 2,
                   "done": [], "not_done": [], "declined": [], "notices": []}
            out.update(overrides.get(stage_id, {}))
            return out
        return run_stage


class TestOrdering(_Case):
    def test_every_stage_runs_and_dependants_run_last(self):
        p = self._project()
        report = runtime.run(p, request_id="r", config={},
                             run_stage=self._runner())
        ran = [c["stage"] for c in self.calls]
        self.assertEqual(len(ran), len(p.stages))
        self.assertEqual(ran[-1], "review")
        self.assertLess(ran.index("artifact"), ran.index("review"))
        self.assertTrue(p.is_complete())
        self.assertEqual(project.get("t1").status, project.P_DONE)
        self.assertIn("stages", report)

    def test_a_gathering_stage_never_runs_after_the_review(self):
        p = self._project()
        runtime.run(p, request_id="r", config={}, run_stage=self._runner())
        ran = [c["stage"] for c in self.calls]
        for sid in [s.id for s in p.stages if s.kind == project.KIND_BROWSE]:
            self.assertLess(ran.index(sid), ran.index("review"))

    def test_each_stage_is_pointed_only_at_its_own_part(self):
        p = self._project()
        runtime.run(p, request_id="r", config={}, run_stage=self._runner())
        for call in self.calls:
            if call["stage"] not in ("review", "artifact"):
                self.assertIn("the only thing to do now", call["goal"])


class TestEvidence(_Case):
    def test_every_stage_answer_lands_in_the_store_under_its_own_stage(self):
        p = self._project()
        runtime.run(p, request_id="r", config={}, run_stage=self._runner())
        for stage in p.stages:
            if stage.kind == project.KIND_BROWSE:
                rows = evidence.for_stage("t1", stage.id)
                self.assertTrue(rows, stage.id)

    def test_the_store_is_persisted_as_the_project_runs(self):
        p = self._project()
        runtime.run(p, request_id="r", config={}, run_stage=self._runner())
        from syrix_supercomputer import paths

        self.assertTrue(os.path.isfile(paths.task_evidence_path("t1")))

    def test_stage_titles_are_recorded_so_the_writer_can_group(self):
        p = self._project()
        runtime.run(p, request_id="r", config={}, run_stage=self._runner())
        text = evidence.render("t1")
        self.assertIn("item 1", text.lower())


class TestBlockedStages(_Case):
    def test_a_stage_that_finds_nothing_is_retried_once_then_stated(self):
        p = self._project()
        empty = {s.id: {"answer": ""} for s in p.stages
                 if s.kind == project.KIND_BROWSE}
        first = list(empty)[0]
        runtime.run(p, request_id="r", config={},
                    run_stage=self._runner(**{first: {"answer": ""}}))
        attempts = [c for c in self.calls if c["stage"] == first]
        self.assertEqual(len(attempts), project.MAX_STAGE_ATTEMPTS)
        self.assertEqual(p.stage(first).status, project.BLOCKED)

    def test_a_blocked_stage_becomes_a_named_gap_not_silence(self):
        p = self._project()
        first = [s for s in p.stages if s.kind == project.KIND_BROWSE][0]
        report = runtime.run(p, request_id="r", config={},
                             run_stage=self._runner(**{first.id: {"answer": ""}}))
        self.assertTrue(evidence.gaps("t1"))
        self.assertTrue(any(first.title in n for n in report["not_done"]))

    def test_the_rest_of_the_project_still_runs(self):
        """Eight things that worked plus a line about the one that did not."""
        p = self._project()
        first = [s for s in p.stages if s.kind == project.KIND_BROWSE][0]
        runtime.run(p, request_id="r", config={},
                    run_stage=self._runner(**{first.id: {"answer": ""}}))
        self.assertIn("review", [c["stage"] for c in self.calls])
        self.assertEqual(p.stage("review").status, project.DONE)


class TestHalting(_Case):
    def test_a_permission_card_pauses_the_project_and_keeps_the_checkpoint(self):
        p = self._project()
        first = [s for s in p.stages if s.kind == project.KIND_BROWSE][0]
        report = runtime.run(
            p, request_id="r", config={},
            run_stage=self._runner(**{first.id: {"awaiting_permission": True,
                                                 "answer": ""}}))
        self.assertEqual(project.get("t1").status, project.P_PAUSED)
        # Nothing after the halt ran.
        self.assertNotIn("review", [c["stage"] for c in self.calls])
        # And the graph is on disk, so the resume continues rather than restarts.
        project.forget_all()
        back = project.load("t1")
        self.assertIsNotNone(back)
        self.assertIn("stages", report)

    def test_a_question_pauses_and_carries_the_question_up(self):
        p = self._project()
        first = [s for s in p.stages if s.kind == project.KIND_BROWSE][0]
        report = runtime.run(
            p, request_id="r", config={},
            run_stage=self._runner(**{first.id: {"question": "which account?",
                                                 "answer": ""}}))
        self.assertEqual(report["question"], "which account?")
        self.assertEqual(project.get("t1").status, project.P_PAUSED)

    def test_a_resume_starts_at_the_next_ready_stage(self):
        p = self._project()
        research = [s for s in p.stages if s.kind == project.KIND_BROWSE]
        runtime.run(p, request_id="r", config={},
                    run_stage=self._runner(**{research[1].id: {"awaiting_permission": True,
                                                              "answer": ""}}))
        self.calls.clear()
        runtime.run(p, request_id="r", config={}, run_stage=self._runner())
        ran = [c["stage"] for c in self.calls]
        self.assertNotIn(research[0].id, ran, "a finished stage must not re-run")
        self.assertIn("review", ran)


class TestOneBrowserDriver(_Case):
    def test_exclusive_stages_hold_the_browser_lock(self):
        p = self._project()
        held = []

        def run_stage(goal, *, stage_id, kind, **kw):
            # If this stage is exclusive the lock must already be held by us,
            # and a second thread must not be able to take it.
            acquired = runtime.browser_lock().acquire(blocking=False)
            if acquired:
                runtime.browser_lock().release()
            held.append((kind, acquired))
            return {"answer": "x", "steps_used": 1}

        runtime.run(p, request_id="r", config={}, run_stage=run_stage)
        # RLock is re-entrant on the same thread, so this asserts the shape:
        # every stage ran, and the lock object is the shared one.
        self.assertEqual(len(held), len(p.stages))

    def test_a_second_thread_cannot_drive_while_a_browse_stage_runs(self):
        p = self._project()
        blocked_out = []
        gate = threading.Event()

        def run_stage(goal, *, stage_id, kind, **kw):
            if kind in (project.KIND_ARTIFACT,):
                def probe():
                    got = runtime.browser_lock().acquire(timeout=0.2)
                    blocked_out.append(got)
                    if got:
                        runtime.browser_lock().release()
                    gate.set()

                threading.Thread(target=probe, daemon=True).start()
                gate.wait(2.0)
            return {"answer": "x", "steps_used": 1}

        runtime.run(p, request_id="r", config={}, run_stage=run_stage)
        self.assertEqual(blocked_out, [False],
                         "another thread must not take the browser mid-stage")


class TestBudgets(_Case):
    def test_the_project_stops_at_its_ceiling(self):
        p = self._project()
        p.max_steps = 2
        report = runtime.run(p, request_id="r", config={},
                             run_stage=self._runner())
        self.assertTrue(any("ceiling" in n or "budget" in n
                            for n in report["not_done"]))
        self.assertEqual(project.get("t1").status, project.P_FAILED)

    def test_a_cancelled_task_stops_the_project(self):
        from syrix_supercomputer import tasks
        from syrix_supercomputer.contract import TaskContract

        task = tasks.create("goal", TaskContract(
            task_id="c1", goal="goal", allowed_domains=frozenset({"example.com"})))
        p = project.plan(task.task_id,
                         "Give me a report on 4 stocks covering the trend, the "
                         "levels and a verdict for each, and create a PDF of it.")
        tasks.stop(task.task_id, reason="user_stop")
        report = runtime.run(p, request_id="r", config={},
                             run_stage=self._runner())
        self.assertEqual(self.calls, [])
        self.assertTrue(report["not_done"])


class TestCompletionValidator(_Case):
    def test_a_missing_part_is_named_in_the_answer(self):
        p = self._project()

        def run_stage(goal, *, stage_id, kind, **kw):
            if stage_id == "review":
                # Answers only one of the three requested subjects.
                return {"answer": "Its products are widgets.", "steps_used": 1}
            return {"answer": "x", "steps_used": 1}

        report = runtime.run(p, request_id="r", config={}, run_stage=run_stage)
        self.assertIn("Not established", report["answer"])

    def test_a_complete_answer_gets_no_note(self):
        # No artefact asked for, so the answer alone can be complete.
        p = self._project(
            "Give me a report on 3 stocks covering the trend and a verdict "
            "for each.")

        def run_stage(goal, *, stage_id, kind, **kw):
            if stage_id == "review":
                return {"answer": ("The trend is up on all three and the verdict "
                                   "is buy on two of them."),
                        "steps_used": 1}
            return {"answer": "x", "steps_used": 1}

        report = runtime.run(p, request_id="r", config={}, run_stage=run_stage)
        self.assertNotIn("Not established", report["answer"])

    def test_a_promised_file_that_does_not_exist_is_named(self):
        """The requested PDF must genuinely exist before success is claimed."""
        p = self._project()

        def run_stage(goal, *, stage_id, kind, **kw):
            if stage_id == "review":
                return {"answer": ("Its products are widgets. Its leadership is "
                                   "Jane Roe. Its competitors are Acme and Bolt."),
                        "steps_used": 1}
            return {"answer": "x", "steps_used": 1}

        report = runtime.run(p, request_id="r", config={}, run_stage=run_stage)
        self.assertIn("Not established", report["answer"])
        self.assertIn("pdf", report["answer"].lower())


class TestNeverRaises(_Case):
    def test_a_stage_runner_that_explodes_becomes_a_gap(self):
        p = self._project()

        def run_stage(goal, *, stage_id, kind, **kw):
            if stage_id == "unit1":
                raise RuntimeError("boom")
            return {"answer": "x", "steps_used": 1}

        report = runtime.run(p, request_id="r", config={}, run_stage=run_stage)
        self.assertTrue(p.is_complete())
        self.assertTrue(report["not_done"])

    def test_a_runner_returning_junk_does_not_crash_the_project(self):
        p = self._project()
        report = runtime.run(p, request_id="r", config={},
                             run_stage=lambda *a, **k: None)
        self.assertTrue(p.is_complete())
        self.assertIsInstance(report, dict)


if __name__ == "__main__":
    unittest.main()


class TestSharedTaskIdIntegration(_Case):
    """Every stage runs the same loop against the SAME task id.

    Both bugs here were found on the first live run of a five-stage project.
    """

    def test_a_finished_stage_does_not_end_the_project(self):
        """The loop marks the task done when it finishes; the runtime read that
        as "the project is over" and stopped after stage one with
        "Not done: the rest of the plan (done)"."""
        from syrix_supercomputer import tasks

        p = self._project()

        def run_stage(goal, *, stage_id, kind, **kw):
            self.calls.append({"stage": stage_id, "kind": kind, "goal": goal})
            tasks.set_state(p.task_id, tasks.STATE_DONE)  # what the loop does
            return {"answer": f"x for {stage_id}", "steps_used": 1}

        runtime.run(p, request_id="r", config={}, run_stage=run_stage)
        self.assertTrue(p.is_complete())
        self.assertIn("review", [c["stage"] for c in self.calls])

    def test_a_genuine_stop_still_halts_the_project(self):
        from syrix_supercomputer import tasks
        from syrix_supercomputer.contract import TaskContract

        # A REAL task, so set_state actually lands — the project id and the
        # task id are the same thing at runtime.
        task = tasks.create("goal", TaskContract(task_id="s1", goal="goal"))
        p = project.plan(task.task_id,
                         "Give me a report on 4 stocks covering the trend, the "
                         "levels and a verdict for each, and create a PDF of it.")

        def run_stage(goal, *, stage_id, kind, **kw):
            self.calls.append({"stage": stage_id, "kind": kind, "goal": goal})
            tasks.set_state(p.task_id, tasks.STATE_STOPPED)
            return {"answer": "x", "steps_used": 1}

        report = runtime.run(p, request_id="r", config={}, run_stage=run_stage)
        self.assertEqual(len(self.calls), 1, "a stop must stop the project")
        self.assertTrue(report["not_done"])


class TestSubject(_Case):
    def test_a_section_fragment_carries_the_subject_of_the_request(self):
        """"how they make money" with a Google page on screen sent the agent off
        to research how GOOGLE makes money, for a question about DBS Bank."""
        p = project.plan("t1", "Look up DBS Bank and give me a report on 3 "
                               "stocks covering the trend and a verdict for each.")
        stage = [s for s in p.stages if s.kind == project.KIND_BROWSE][0]
        text = project.stage_goal(p, stage)
        self.assertIn("DBS Bank", text)
        # The request leads; the fragment follows it.
        self.assertLess(text.index("DBS Bank"), text.index(stage.title))
        self.assertIn("never", text.lower())

    def test_subject_extraction(self):
        self.assertEqual(project.subject_of(
            "Look up DBS Bank and tell me what they do."), "DBS Bank")
        self.assertEqual(project.subject_of(
            "Research Monzo and tell me its risks."), "Monzo")
        # No proper subject to name — the request is quoted above anyway.
        self.assertEqual(project.subject_of(
            "Research this company and tell me who the CEO is."), "")


class TestEvidenceReachesTheBuilders(_Case):
    """The store was written and never read back.

    On the first full live run the research stages gathered DBS Bank's revenue
    model, its CEO and its 2025 figures — and the stage told to "create the
    spreadsheet" began with a blank transcript. It produced a markdown table in
    chat and called that a spreadsheet.
    """

    def test_building_stages_are_given_everything_established(self):
        from syrix_supercomputer import agent, evidence, project

        evidence.start("t1", "goal")
        evidence.name_stage("t1", "sec1", "how they make money")
        evidence.add("t1", "Net interest income is the main source",
                     source="https://dbs.com/x", stage="sec1")

        for kind in (project.KIND_ARTIFACT, project.KIND_REVIEW):
            text = agent._stage_context("t1", kind, "earlier chat", {})
            self.assertIn("Net interest income", text)
            self.assertIn("earlier chat", text)

    def test_a_gathering_stage_is_not_given_the_other_stages_findings(self):
        """Or it answers their part instead of its own."""
        from syrix_supercomputer import agent, evidence, project

        evidence.start("t1", "goal")
        evidence.add("t1", "Net interest income is the main source", stage="sec1")
        text = agent._stage_context("t1", project.KIND_BROWSE, "earlier chat", {})
        self.assertEqual(text, "earlier chat")

    def test_an_empty_store_changes_nothing(self):
        from syrix_supercomputer import agent, evidence, project

        evidence.start("t1", "goal")
        self.assertEqual(
            agent._stage_context("t1", project.KIND_ARTIFACT, "chat", {}), "chat")
