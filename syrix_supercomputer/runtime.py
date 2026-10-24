"""The scheduler: runs a project's stages, one browser driver at a time.

This is the piece that turns :mod:`syrix_supercomputer.project`'s graph into
work. It is deliberately small — the intelligence is in the loop that already
exists, and this only decides *what that loop is pointed at next*, keeps the
findings, and refuses to let two things touch the browser at once.

Rules it enforces, in order of how much they matter:

1. **One browser driver.** ``browse``, ``artifact`` and ``review`` stages hold a
   process-wide lock for their whole run. Nothing else may drive the browser
   while one is held — not by agreement, by construction.
2. **A stage's findings are kept.** Every stage writes into
   :mod:`syrix_supercomputer.evidence` under its own id, and the ``review``
   stage reads the whole store rather than the tail of a conversation.
3. **A halt pauses the project, it does not lose it.** A permission card stops
   the running stage; the checkpoint records exactly which stages are done, and
   the resume picks up at the next ready one instead of starting again.
4. **Budgets are real.** Per stage and per project, checked between stages, so a
   confused stage costs a stage and not the job.
5. **A blocked stage is a stated gap.** It gets one retry against a different
   route, then it is recorded in the evidence store as NOT FOUND and the
   remaining stages still run. The user gets the eight things that worked plus a
   line about the one that did not — never silence.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional

import syrix_obs as obs

from . import deliverables, evidence, project, tasks

# The one browser. Held for the whole of a stage that may drive it, so a
# research worker can never interleave a click into someone else's page.
_BROWSER_LOCK = threading.RLock()

# Kinds that take the browser lock. Artifact and review do not click, but they
# do preview and read, and serialising them costs nothing and removes a whole
# class of race.
_EXCLUSIVE = frozenset({project.KIND_BROWSE, project.KIND_ARTIFACT,
                        project.KIND_REVIEW})

# Task states that mean the whole project must stop. Deliberately NOT DONE:
# every stage runs the same loop against the same task id, so a finished stage
# leaves the task marked done and that must not be read as "the project is
# over" — it was, and a five-stage project ended after stage one.
_HALT_STATES = frozenset({tasks.STATE_STOPPED, tasks.STATE_FAILED,
                          tasks.STATE_PAUSED_USER})


def enabled(config: Optional[Dict[str, Any]] = None) -> bool:
    return project.enabled(config)


def browser_lock() -> threading.RLock:
    """Exposed so a research worker can assert it is NOT holding this."""
    return _BROWSER_LOCK


class StageResult:
    """What one stage produced. Never an exception."""

    def __init__(self, stage_id: str, *, ok: bool, report: Optional[Dict[str, Any]] = None,
                 halted: bool = False, detail: str = ""):
        self.stage_id = stage_id
        self.ok = ok
        self.report = report or {}
        self.halted = halted
        self.detail = detail

    def answer(self) -> str:
        return str(self.report.get("answer") or "")


def run(
    proj: project.Project,
    *,
    request_id: str,
    config: Dict[str, Any],
    run_stage: Callable[..., Dict[str, Any]],
    on_progress: Optional[Callable[[], None]] = None,
) -> Dict[str, Any]:
    """Drive a project to completion. Returns a report shaped like the loop's.

    ``run_stage`` is injected rather than imported so this module can be tested
    without a browser, a model or a network — and so the loop keeps owning what
    a step actually is.
    """
    cfg = config or {}
    evidence.start(proj.task_id, proj.goal)
    for stage in proj.stages:
        evidence.name_stage(proj.task_id, stage.id, stage.label())

    report: Dict[str, Any] = {
        "task_id": proj.task_id, "goal": proj.goal, "answer": "",
        "done": [], "not_done": [], "declined": [], "notices": [],
        "steps_used": 0, "question": "", "stages": [],
    }
    project.set_status(proj.task_id, project.P_RUNNING)

    while True:
        halt = _project_halt(proj)
        if halt:
            report["not_done"].append(halt)
            project.set_status(proj.task_id, project.P_FAILED, detail=halt)
            break

        ready = list(proj.ready())
        if not ready:
            if proj.is_complete():
                project.set_status(proj.task_id, project.P_DONE)
            else:
                # Nothing ready and not complete means a dependency cycle, which
                # is a planning bug rather than a run failure. Say so instead of
                # spinning.
                stuck = ", ".join(s.id for s in proj.unfinished()[:4])
                report["not_done"].append(
                    f"the rest of the plan — its steps depend on each other in a "
                    f"loop ({stuck})")
                project.set_status(proj.task_id, project.P_FAILED, detail="cycle")
            break

        batch = _next_batch(ready, cfg)
        results = _run_batch(proj, batch, request_id=request_id, config=cfg,
                             run_stage=run_stage)
        project.save(proj.task_id)
        evidence.save(proj.task_id, config=cfg)
        if on_progress:
            with obs.swallow("supercomputer.runtime.progress"):
                on_progress()

        halted = next((r for r in results if r.halted), None)
        if halted is not None:
            # A permission card, a stop, or the user taking control. The checkpoint
            # is already written, so the resume continues from the next ready
            # stage rather than from nothing. Everything the rest of the batch
            # managed is kept — it is already in the evidence store, and its
            # `done` lines belong in the report whether or not a sibling halted.
            report["question"] = str(halted.report.get("question") or "")
            report["answer"] = halted.answer()
            for result in results:
                for key in ("done", "not_done", "declined", "notices"):
                    report[key].extend(result.report.get(key) or [])
            project.set_status(proj.task_id, project.P_PAUSED, detail=halted.detail)
            return _finish(proj, report, cfg, partial=True)

        for stage, result in zip(batch, results):
            for key in ("done", "declined", "notices"):
                report[key].extend(result.report.get(key) or [])

            # The review stage IS the project's answer. Without this the whole
            # graph could run, every finding could be gathered, the write-up
            # could be produced — and the report would hand the user an empty string.
            if stage.kind == project.KIND_REVIEW and result.answer():
                report["answer"] = result.answer()
            elif (not report["answer"] and result.answer()
                    and _last_speaking_stage(proj, stage)):
                # No review stage reached (blocked, or a graph without one). The
                # last stage that produced prose is better than nothing.
                report["answer"] = result.answer()
        report["steps_used"] = proj.steps_used

    return _finish(proj, report, cfg, partial=False)


def _next_batch(ready: List[project.Stage],
                config: Dict[str, Any]) -> List[project.Stage]:
    """The stages to run together — usually exactly one.

    A batch is only ever formed from consecutive parallel-safe stages, and only
    from kinds that have been stripped of every browser tool (see
    ``tools.BROWSER_TOOLS``). Anything else runs alone, exactly as before, so
    the ordinary single-stage path is unchanged.
    """
    if not ready:
        return []
    first = ready[0]
    if first.kind not in project.PARALLEL_KINDS:
        return [first]
    if not bool(config.get("supercomputer_parallel_stages_enabled", True)):
        return [first]
    try:
        from . import resources

        workers = int(resources.max_workers(config))
    except Exception as exc:
        obs.report("supercomputer.runtime.max_workers", exc, level="debug")
        workers = 1
    if workers <= 1:
        return [first]
    batch = [s for s in ready if s.kind in project.PARALLEL_KINDS][:workers]
    return batch or [first]


def _run_batch(proj: project.Project, batch: List[project.Stage], *,
               request_id: str, config: Dict[str, Any],
               run_stage: Callable[..., Dict[str, Any]]) -> List[StageResult]:
    """Run a batch, in parallel when it holds more than one stage.

    Order of the returned results matches ``batch``, so the caller can pair a
    result with the stage that produced it.
    """
    if len(batch) <= 1:
        return [_run_one(proj, batch[0], request_id=request_id, config=config,
                         run_stage=run_stage)] if batch else []

    from concurrent.futures import ThreadPoolExecutor

    obs.report("supercomputer.runtime.parallel",
               RuntimeError(f"running {len(batch)} stages together"),
               level="debug", task_id=proj.task_id,
               stages=",".join(s.id for s in batch))
    results: List[Optional[StageResult]] = [None] * len(batch)
    with ThreadPoolExecutor(max_workers=len(batch),
                            thread_name_prefix="sc-stage") as pool:
        futures = {
            pool.submit(_run_one, proj, stage, request_id=request_id,
                        config=config, run_stage=run_stage): i
            for i, stage in enumerate(batch)
        }
        for future in futures:
            i = futures[future]
            try:
                results[i] = future.result()
            except Exception as exc:
                # _run_one already swallows; this is the belt for a failure in
                # the executor itself. A stage that could not run is a gap.
                obs.report("supercomputer.runtime.worker", exc,
                           task_id=proj.task_id, stage=batch[i].id)
                results[i] = _blocked(proj, batch[i], "it could not be run")
    return [r for r in results if r is not None]


def _run_one(proj: project.Project, stage: project.Stage, *,
             request_id: str, config: Dict[str, Any],
             run_stage: Callable[..., Dict[str, Any]]) -> StageResult:
    """Run one stage under the right lock, and record what it found."""
    project.set_stage_status(proj.task_id, stage.id, project.RUNNING)
    stage.attempts += 1
    goal = project.stage_goal(proj, stage)

    lock = _BROWSER_LOCK if stage.kind in _EXCLUSIVE else _NullLock()
    started = time.time()
    with lock:
        with obs.swallow("supercomputer.runtime.stage", stage=stage.id):
            raw = run_stage(
                goal,
                stage_id=stage.id,
                kind=stage.kind,
                max_steps=stage.max_steps,
                budget_s=stage.max_seconds,
                request_id=request_id,
                config=config,
            ) or {}
            return _grade(proj, stage, raw, started)
    # The swallow ate an exception. A stage that could not run is a gap, not a
    # crash, and the rest of the project still has work to do.
    return _blocked(proj, stage, "it could not be run")


def _grade(proj: project.Project, stage: project.Stage,
           raw: Dict[str, Any], started: float) -> StageResult:
    project.note_steps(proj.task_id, stage.id, int(raw.get("steps_used") or 0))

    if raw.get("question"):
        project.set_stage_status(proj.task_id, stage.id, project.PENDING,
                                 detail="waiting on you")
        return StageResult(stage.id, ok=False, report=raw, halted=True,
                           detail="awaiting_user")
    if raw.get("awaiting_permission"):
        project.set_stage_status(proj.task_id, stage.id, project.PENDING,
                                 detail="waiting on your permission")
        return StageResult(stage.id, ok=False, report=raw, halted=True,
                           detail="awaiting_permission")

    answer = str(raw.get("answer") or "").strip()
    if answer:
        # A stage's answer IS a finding. Without this a research stage that
        # never called `note` would leave nothing behind and the writer would
        # have no idea what it learned.
        evidence.add(proj.task_id, answer[:evidence.MAX_CLAIM],
                     stage=stage.id, source=str(raw.get("source") or ""))

    found = len(evidence.for_stage(proj.task_id, stage.id))
    if found:
        project.set_stage_status(proj.task_id, stage.id, project.DONE,
                                 detail=f"{found} finding(s)")
        return StageResult(stage.id, ok=True, report=raw)

    # Nothing at all. One more attempt, then a stated gap.
    if stage.attempts < project.MAX_STAGE_ATTEMPTS:
        project.set_stage_status(proj.task_id, stage.id, project.PENDING,
                                 detail="found nothing; trying another route")
        return StageResult(stage.id, ok=False, report=raw)
    return _blocked(proj, stage, "nothing could be established for it",
                    report=raw)


def _blocked(proj: project.Project, stage: project.Stage, why: str,
             report: Optional[Dict[str, Any]] = None) -> StageResult:
    """A stage that could not be done becomes a NAMED gap, not silence."""
    project.set_stage_status(proj.task_id, stage.id, project.BLOCKED, detail=why)
    evidence.add_gap(proj.task_id, stage.label(), stage=stage.id, why=why)
    return StageResult(stage.id, ok=False, report=report or {}, detail=why)


def _last_speaking_stage(proj: project.Project, stage: project.Stage) -> bool:
    """True when no later stage will produce an answer of its own.

    The review stage normally writes the answer. If the graph has none, or it
    was blocked, the last stage that said something real is what the user gets —
    an imperfect answer he can judge beats an empty one.
    """
    review = [s for s in proj.stages if s.kind == project.KIND_REVIEW]
    return not any(s.status not in (project.BLOCKED, project.SKIPPED)
                   and s.id != stage.id for s in review)


def _project_halt(proj: project.Project) -> str:
    """Why the whole project must stop right now, or "".

    Every stage is a run of the same loop against the SAME task id, and a loop
    that finishes marks that task done. So "the task is terminal" cannot mean
    "the project is over" — it meant the opposite, and the first live run of a
    five-stage project stopped after stage one with "Not done: the rest of the
    plan (done)". Only the states that mean somebody or something STOPPED it
    halt the project.
    """
    budget = proj.out_of_budget()
    if budget:
        return f"the rest of the plan — it reached {budget}"
    task = tasks.get(proj.task_id)
    if task is not None and task.state in _HALT_STATES:
        return f"the rest of the plan ({task.state})"
    return ""


def _finish(proj: project.Project, report: Dict[str, Any],
            config: Dict[str, Any], *, partial: bool) -> Dict[str, Any]:
    """Close the project out and make sure the answer names what is missing."""
    project.save(proj.task_id)
    evidence.save(proj.task_id, config=config)

    report["stages"] = project.snapshot(proj.task_id).get("stages") or []
    report["evidence"] = evidence.snapshot(proj.task_id)

    blocked = proj.blocked_stages()
    for stage in blocked:
        note = f"{stage.label()} — {stage.detail or 'could not be established'}"
        if note not in report["not_done"]:
            report["not_done"].append(note)

    if partial:
        return report

    # The completion validator. A project may not report success while a
    # requested part is missing: the answer says so, in the answer, where the user
    # reads it — not in a log.
    answer = str(report.get("answer") or "")
    if answer:
        gap = deliverables.shortfall(proj.goal, answer,
                                     deliverables.files_for_task(proj.task_id))
        note = deliverables.gap_note(gap)
        if note and note.lower()[:20] not in answer.lower():
            report["answer"] = f"{answer}\n\n{note}".strip()
    return report


class _NullLock:
    """A lock that is not one, for the stages that may run unserialised."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


__all__ = ["enabled", "run", "browser_lock", "StageResult"]
