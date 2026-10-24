"""Parallel research workers — designed since 2026-07-26, wired 2026-07-28.

The scheduler ran one stage at a time even though `project.PARALLEL_KINDS`
named the safe ones. Wiring the thread pool first required closing a real gap:
project.py's docstring said a research stage "may not drive the browser", and
NOTHING enforced it — `kind` reached the stage runner and was used only to pick
what context to show. Running two of those in parallel would have interleaved
clicks into the one BrowserView, which is the failure the design forbids.

So the order here is: take the browser away from parallel-safe kinds, give them
an HTTP fetch instead, and only then run them together.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from supercomputer_testkit import SupercomputerTestMixin  # noqa: E402

from syrix_supercomputer import project, runtime, tools  # noqa: E402


class TestAParallelStageHasNoBrowser(unittest.TestCase):
    def test_research_loses_every_browser_tool(self):
        names = {t.name for t in tools.available({}, kind="research")}
        self.assertFalse(names & tools.BROWSER_TOOLS,
                         "a stage that runs alongside another must not be able "
                         "to touch the one browser")

    def test_compute_loses_them_too(self):
        names = {t.name for t in tools.available({}, kind="compute")}
        self.assertFalse(names & tools.BROWSER_TOOLS)

    def test_every_parallel_kind_is_covered(self):
        """The bar and the schedule must agree, or one of them is a lie."""
        for kind in project.PARALLEL_KINDS:
            names = {t.name for t in tools.available({}, kind=kind)}
            self.assertFalse(names & tools.BROWSER_TOOLS, kind)

    def test_research_keeps_a_way_to_read_a_page(self):
        names = {t.name for t in tools.available({}, kind="research")}
        self.assertIn("fetch_page", names,
                      "taking the browser away without giving it fetch_page "
                      "would just make research useless")
        self.assertIn("note", names)

    def test_an_ordinary_task_is_unchanged(self):
        """No kind means the single-loop path, which must keep everything."""
        names = {t.name for t in tools.available({}, kind="")}
        self.assertIn("open_url", names)
        self.assertIn("click", names)

    def test_a_browse_stage_keeps_the_browser(self):
        names = {t.name for t in tools.available({}, kind="browse")}
        self.assertIn("open_url", names)
        self.assertIn("click", names)

    def test_the_prompt_says_so(self):
        text = tools.describe_tools({}, kind="research")
        self.assertIn("NO browser", text)
        self.assertIn("fetch_page", text)
        self.assertNotIn("- click(", text)

    def test_dispatch_refuses_a_browser_tool_even_if_asked(self):
        """The prompt is a hint; this is the enforcement."""
        ctx = tools.ToolContext(task_id="t1", request_id="r1", config={},
                                kind="research")
        res = tools.dispatch("click", {"element_id": "e1"}, ctx)
        self.assertFalse(res.ok)
        self.assertIn("fetch_page", res.observation)

    def test_dispatch_allows_it_for_a_browse_stage(self):
        ctx = tools.ToolContext(task_id="t1", request_id="r1", config={},
                                kind="browse")
        res = tools.dispatch("click", {"element_id": "nope"}, ctx)
        # It may well fail for other reasons; what matters is that it was not
        # refused by the parallel-safety bar.
        self.assertNotIn("runs alongside other steps", res.observation)


class _Stage:
    """Enough of a project.Stage for the batching decision."""

    def __init__(self, sid, kind):
        self.id = sid
        self.kind = kind


def _workers(n):
    """Pin the worker cap, so batching tests do not depend on what is on
    screen — the governor drops it to 1 whenever a fullscreen app is in
    front, which is correct behaviour and would otherwise flap these tests."""
    from unittest import mock

    from syrix_supercomputer import resources

    return mock.patch.object(resources, "max_workers", lambda config=None: n)


class TestBatching(unittest.TestCase):
    def test_a_lone_browse_stage_runs_alone(self):
        ready = [_Stage("s1", project.KIND_BROWSE), _Stage("s2", project.KIND_RESEARCH)]
        self.assertEqual([s.id for s in runtime._next_batch(ready, {})], ["s1"])

    def test_consecutive_research_stages_batch(self):
        ready = [_Stage(f"s{i}", project.KIND_RESEARCH) for i in range(3)]
        # Pinned: max_workers legitimately returns 1 when the governor sees a
        # fullscreen app in front, so reading the live value would make this
        # test pass or fail depending on what is on screen.
        with _workers(2):
            batch = runtime._next_batch(ready, {})
        self.assertGreater(len(batch), 1)

    def test_the_batch_never_exceeds_the_worker_cap(self):
        ready = [_Stage(f"s{i}", project.KIND_RESEARCH) for i in range(9)]
        with _workers(3):
            self.assertLessEqual(len(runtime._next_batch(ready, {})), 3)

    def test_the_governor_can_force_it_back_to_one(self):
        """While the user is gaming, "parallel" must mean "not right now"."""
        ready = [_Stage(f"s{i}", project.KIND_RESEARCH) for i in range(4)]
        with _workers(1):
            self.assertEqual(len(runtime._next_batch(ready, {})), 1)

    def test_a_browse_stage_is_never_swept_into_a_batch(self):
        ready = [_Stage("r1", project.KIND_RESEARCH),
                 _Stage("b1", project.KIND_BROWSE),
                 _Stage("r2", project.KIND_RESEARCH)]
        batch = runtime._next_batch(ready, {})
        self.assertTrue(all(s.kind in project.PARALLEL_KINDS for s in batch))

    def test_the_kill_switch_restores_one_at_a_time(self):
        ready = [_Stage(f"s{i}", project.KIND_RESEARCH) for i in range(4)]
        batch = runtime._next_batch(
            ready, {"supercomputer_parallel_stages_enabled": False})
        self.assertEqual(len(batch), 1)

    def test_nothing_ready_is_an_empty_batch(self):
        self.assertEqual(runtime._next_batch([], {}), [])


class TestTheStagesActuallyOverlap(SupercomputerTestMixin, unittest.TestCase):
    def test_two_research_stages_run_at_the_same_time(self):
        """The point of the exercise: wall clock, not thread count."""
        proj = project.Project(task_id="t-par", goal="gather three things")
        proj.stages = [
            project.Stage(id=f"s{i}", kind=project.KIND_RESEARCH,
                          title=f"find thing {i}")
            for i in range(3)
        ]
        project.save_project(proj) if hasattr(project, "save_project") else None

        overlap = {"max": 0}
        live = {"n": 0}
        lock = threading.Lock()

        def fake_stage(goal, *, stage_id, kind, max_steps, budget_s,
                       request_id, config):
            with lock:
                live["n"] += 1
                overlap["max"] = max(overlap["max"], live["n"])
            time.sleep(0.25)
            with lock:
                live["n"] -= 1
            return {"answer": f"found something for {stage_id}", "steps_used": 1}

        started = time.time()
        runtime._run_batch(proj, proj.stages, request_id="r1", config={},
                           run_stage=fake_stage)
        elapsed = time.time() - started

        self.assertGreater(overlap["max"], 1,
                           "the stages ran one after another — the pool is not wired")
        self.assertLess(elapsed, 0.7,
                        f"three 0.25s stages took {elapsed:.2f}s; that is serial")

    def test_a_single_stage_batch_does_not_use_the_pool(self):
        proj = project.Project(task_id="t-one", goal="one thing")
        stage = project.Stage(id="s0", kind=project.KIND_RESEARCH, title="find it")
        proj.stages = [stage]
        threads = []

        def fake_stage(goal, **kw):
            threads.append(threading.current_thread().name)
            return {"answer": "found it", "steps_used": 1}

        runtime._run_batch(proj, [stage], request_id="r1", config={},
                           run_stage=fake_stage)
        self.assertTrue(threads)
        self.assertFalse(threads[0].startswith("sc-stage"),
                         "one stage should run inline, not on a worker")

    def test_a_worker_that_explodes_becomes_a_gap_not_a_crash(self):
        proj = project.Project(task_id="t-boom", goal="two things")
        proj.stages = [
            project.Stage(id="s0", kind=project.KIND_RESEARCH, title="ok"),
            project.Stage(id="s1", kind=project.KIND_RESEARCH, title="boom"),
        ]

        def fake_stage(goal, *, stage_id, **kw):
            if stage_id == "s1":
                raise RuntimeError("nope")
            return {"answer": "found it", "steps_used": 1}

        results = runtime._run_batch(proj, proj.stages, request_id="r1",
                                     config={}, run_stage=fake_stage)
        self.assertEqual(len(results), 2, "a failed sibling must not lose the batch")

    def test_results_line_up_with_their_stages(self):
        proj = project.Project(task_id="t-order", goal="ordered")
        proj.stages = [
            project.Stage(id=f"s{i}", kind=project.KIND_RESEARCH, title=f"t{i}")
            for i in range(3)
        ]

        def fake_stage(goal, *, stage_id, **kw):
            # Finish out of submission order on purpose.
            time.sleep(0.15 if stage_id == "s0" else 0.01)
            return {"answer": f"answer-{stage_id}", "steps_used": 1}

        results = runtime._run_batch(proj, proj.stages, request_id="r1",
                                     config={}, run_stage=fake_stage)
        for stage, result in zip(proj.stages, results):
            self.assertEqual(result.stage_id, stage.id,
                             "a finding filed under the wrong stage is worse "
                             "than no parallelism")


if __name__ == "__main__":
    unittest.main()
