"""A task with structure: stages, dependencies, and one browser driver.

Why this exists
---------------
The single loop is one agent, one transcript, strictly sequential. That is
right for "read this page and answer" and structurally incapable of the work
The user actually wants:

* ten stocks, each needing chart, timeframe, indicators, levels and a verdict —
  about a hundred steps in one conversation that keeps ten messages;
* research a company across its site, filings, LinkedIn and the news, then
  write one cited PDF over all of it;
* four voice APIs compared on four attributes each.

All three fail the same way and it is not the model's fault: by the time it
reaches subject four, subject one has been trimmed out of the transcript, and
the write-up is composed from whatever is at the tail. Measured live — fourteen
pages opened, all four products correctly identified, no comparison produced.

The shape of the fix
--------------------
A project is a small graph of **stages**. Each stage is a unit of work with its
own short transcript, its own budget, and its own slot in
:mod:`syrix_supercomputer.evidence`. The final writer reads the whole evidence
store rather than a conversation. Nothing is remembered by luck.

``kind`` decides who may run a stage, and that is the entire safety model:

===========  ==================  ============  ================
kind         may drive browser   may write     may run parallel
===========  ==================  ============  ================
browse       yes, ONE at a time  no            never
research     no (read-only)      no            yes
compute      no                  workspace     yes
artifact     no                  workspace     no
review       no                  no            no
===========  ==================  ============  ================

A research worker is not *asked* to keep off the browser — it is handed a tool
set that does not contain the browser tools, which is a different and much
stronger promise.

When a project is used at all
-----------------------------
Almost never. :func:`plan` returns None unless the goal genuinely decomposes,
and then the existing single loop runs exactly as it does today. That keeps the
tasks that already work byte-identical, and makes the kill switch
(``supercomputer_project_enabled=false``) a true restore point.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

import syrix_obs as obs

from . import deliverables, paths

# ── stage kinds ────────────────────────────────────────────────────────────
KIND_BROWSE = "browse"      # drives the real browser; one at a time, ever
KIND_RESEARCH = "research"  # read-only search/fetch; safe in parallel
KIND_COMPUTE = "compute"    # sandboxed python; no browser, no network
KIND_ARTIFACT = "artifact"  # documents, spreadsheets, code; no browser
KIND_REVIEW = "review"      # the final write-up and completion check
KINDS = (KIND_BROWSE, KIND_RESEARCH, KIND_COMPUTE, KIND_ARTIFACT, KIND_REVIEW)

# Which kinds may run at the same time as another stage.
PARALLEL_KINDS = frozenset({KIND_RESEARCH, KIND_COMPUTE})

# ── stage status ───────────────────────────────────────────────────────────
PENDING = "pending"
RUNNING = "running"
DONE = "done"
BLOCKED = "blocked"
SKIPPED = "skipped"
TERMINAL = frozenset({DONE, BLOCKED, SKIPPED})

# ── project status ─────────────────────────────────────────────────────────
P_PLANNING = "planning"
P_RUNNING = "running"
P_PAUSED = "paused"
P_DONE = "done"
P_FAILED = "failed"
P_CANCELLED = "cancelled"

# Budgets. Per stage, so one confused stage cannot eat the project, and per
# project so a graph that keeps finding more to do still ends.
DEFAULT_STAGE_STEPS = 26
DEFAULT_STAGE_SECONDS = 420.0
DEFAULT_PROJECT_STEPS = 220
DEFAULT_PROJECT_SECONDS = 3600.0
# A stage gets one re-plan against a different route before it is a stated gap.
MAX_STAGE_ATTEMPTS = 2

# A graph smaller than this is not worth the machinery — the single loop does
# it better and cheaper.
MIN_STAGES = 3
MAX_STAGES = 24

_LOCK = threading.RLock()
_PROJECTS: Dict[str, "Project"] = {}


def enabled(config: Optional[Dict[str, Any]] = None) -> bool:
    return bool((config or {}).get("supercomputer_project_enabled", True))


@dataclass
class Stage:
    id: str
    title: str
    kind: str = KIND_BROWSE
    needs: List[str] = field(default_factory=list)
    status: str = PENDING
    detail: str = ""
    attempts: int = 0
    steps_used: int = 0
    max_steps: int = DEFAULT_STAGE_STEPS
    max_seconds: float = DEFAULT_STAGE_SECONDS
    # Set when this stage is one instance of a repeated unit ("each of the ten
    # tickers"). Carries the subject so the stage goal can name it.
    subject: str = ""

    def is_terminal(self) -> bool:
        return self.status in TERMINAL

    def label(self) -> str:
        return f"{self.title} — {self.subject}" if self.subject else self.title


@dataclass
class Project:
    task_id: str
    goal: str
    stages: List[Stage] = field(default_factory=list)
    status: str = P_PLANNING
    detail: str = ""
    started_ts: float = field(default_factory=time.time)
    steps_used: int = 0
    max_steps: int = DEFAULT_PROJECT_STEPS
    max_seconds: float = DEFAULT_PROJECT_SECONDS

    # ---------------------------------------------------------------- graph
    def stage(self, stage_id: str) -> Optional[Stage]:
        for s in self.stages:
            if s.id == stage_id:
                return s
        return None

    def ready(self) -> List[Stage]:
        """Pending stages whose dependencies are all finished.

        A dependency that ended BLOCKED still unblocks its dependants — a
        source being gated must not silently cancel the write-up; the write-up
        runs and states the gap. Only a cancelled project stops everything.
        """
        done = {s.id for s in self.stages if s.is_terminal()}
        out: List[Stage] = []
        for s in self.stages:
            if s.status != PENDING:
                continue
            if all(dep in done for dep in s.needs):
                out.append(s)
        return out

    def unfinished(self) -> List[Stage]:
        return [s for s in self.stages if not s.is_terminal()]

    def is_complete(self) -> bool:
        return all(s.is_terminal() for s in self.stages)

    def blocked_stages(self) -> List[Stage]:
        return [s for s in self.stages if s.status == BLOCKED]

    def progress(self) -> Tuple[int, int]:
        return sum(1 for s in self.stages if s.is_terminal()), len(self.stages)

    def out_of_budget(self) -> str:
        if self.steps_used >= self.max_steps:
            return f"the project step ceiling ({self.max_steps})"
        if time.time() - self.started_ts >= self.max_seconds:
            return "the project time budget"
        return ""


# ── planning ───────────────────────────────────────────────────────────────
# A repeated unit: "each of these ten", "all five companies", "for every page".
_FANOUT_RE = re.compile(
    r"\b(?:each|every|all)\s+(?:of\s+)?(?:the\s+)?(\d{1,2})?\b|\b(\d{1,2})\s+"
    r"(?:stocks?|tickers?|companies|companys?|universities|sites?|pages?|"
    r"products?|jobs?|listings?|articles?|videos?|competitors?)\b",
    re.I,
)
# An explicit list of subjects: "AAPL, MSFT and NVDA".
_TICKER_RE = re.compile(r"\b[A-Z]{2,5}\b")


def fanout_count(goal: str) -> int:
    """How many repeats the goal asks for, or 0. Capped hard."""
    text = " ".join(str(goal or "").split())
    best = 0
    for match in _FANOUT_RE.finditer(text):
        for group in match.groups():
            if group and group.isdigit():
                best = max(best, int(group))
    return min(best, 12)


def _slug(text: str, fallback: str) -> str:
    body = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")
    return (body[:28] or fallback)


def plan(task_id: str, goal: str, *,
         config: Optional[Dict[str, Any]] = None,
         seed_urls: Optional[Sequence[str]] = None) -> Optional[Project]:
    """Build a project for a goal that needs one, or None to use the loop.

    Deliberately conservative. Structure costs a scheduler, checkpoints and a
    write-up stage; a goal that a single loop already handles should never pay
    that. The test is structural, not a keyword list: does the request name
    several distinct subjects, does it repeat a unit, does it ask for an
    artefact as well as an answer.
    """
    cfg = config or {}
    if not enabled(cfg):
        return None
    text = " ".join(str(goal or "").split())
    if not text:
        return None

    sections = deliverables.requested_sections(text)
    artefacts = deliverables.requested_artefacts(text)
    repeats = fanout_count(text)

    stages: List[Stage] = []

    if repeats >= 2:
        # A repeated unit is the clearest case for structure: each instance
        # gets its own transcript and its own evidence slot, which is exactly
        # what stops the run forgetting subject one by subject four.
        for i in range(repeats):
            stages.append(Stage(
                id=f"unit{i + 1}",
                title=f"Item {i + 1} of {repeats}",
                kind=KIND_BROWSE,
                subject="",
            ))
    else:
        # SEVERAL QUESTIONS ABOUT ONE THING IS NOT A PROJECT.
        #
        # This was the first rule and it was wrong, expensively. "Tell me what
        # DBS does, how they make money and who their CEO is" became five
        # stages, each starting with a blank transcript, each opening the same
        # DBS pages again — 615 seconds for something one loop answers off one
        # page in a fraction of that, and a worse answer at the end because no
        # stage saw the whole picture.
        #
        # Sections about a single subject SHARE their sources, so splitting
        # them multiplies the page loads and divides the context. The coverage
        # gate already guarantees every section gets answered; it does not need
        # a stage each.
        #
        # A project is for work that is genuinely separate: ten different
        # stocks, four different products, each needing its own chart, its own
        # site and its own reading. That is what fan-out detects, and it is now
        # the only thing that builds a graph.
        return None

    gather_ids = [s.id for s in stages]

    if artefacts:
        stages.append(Stage(
            id="artifact",
            title=f"Create the {artefacts[0]}",
            kind=KIND_ARTIFACT,
            needs=list(gather_ids),
        ))

    stages.append(Stage(
        id="review",
        title="Write the answer and check every part is covered",
        kind=KIND_REVIEW,
        needs=list(gather_ids) + (["artifact"] if artefacts else []),
    ))

    if len(stages) < MIN_STAGES:
        return None

    project = Project(
        task_id=str(task_id or ""),
        goal=text,
        stages=stages[:MAX_STAGES],
        status=P_PLANNING,
        max_steps=int(cfg.get("supercomputer_project_max_steps", DEFAULT_PROJECT_STEPS) or DEFAULT_PROJECT_STEPS),
        max_seconds=float(cfg.get("supercomputer_project_budget_s", DEFAULT_PROJECT_SECONDS) or DEFAULT_PROJECT_SECONDS),
    )
    with _LOCK:
        _PROJECTS[project.task_id] = project
    return project


def set_subjects(task_id: str, subjects: Sequence[str]) -> int:
    """Name the fan-out stages once the subjects are actually known.

    The plan is built before anything has been looked up, so "Item 3 of 10" is
    the honest label at that point. As soon as a stage discovers the real list
    the labels are filled in, and the user sees "NVDA" rather than "Item 3".
    """
    project = get(task_id)
    if project is None:
        return 0
    named = 0
    with _LOCK:
        units = [s for s in project.stages if s.id.startswith("unit")]
        for stage, subject in zip(units, list(subjects)):
            text = str(subject or "").strip()[:60]
            if text:
                stage.subject = text
                named += 1
    return named


# ── registry ───────────────────────────────────────────────────────────────
def get(task_id: str) -> Optional[Project]:
    with _LOCK:
        return _PROJECTS.get(str(task_id or ""))


def forget(task_id: str) -> bool:
    with _LOCK:
        return _PROJECTS.pop(str(task_id or ""), None) is not None


def forget_all() -> int:
    with _LOCK:
        n = len(_PROJECTS)
        _PROJECTS.clear()
        return n


def set_status(task_id: str, status: str, *, detail: str = "") -> bool:
    project = get(task_id)
    if project is None:
        return False
    with _LOCK:
        project.status = str(status)
        if detail:
            project.detail = str(detail)[:200]
    return True


def set_stage_status(task_id: str, stage_id: str, status: str, *,
                     detail: str = "") -> bool:
    project = get(task_id)
    if project is None:
        return False
    with _LOCK:
        stage = project.stage(stage_id)
        if stage is None:
            return False
        stage.status = str(status)
        if detail:
            stage.detail = str(detail)[:200]
    return True


def note_steps(task_id: str, stage_id: str, steps: int) -> None:
    project = get(task_id)
    if project is None:
        return
    with _LOCK:
        stage = project.stage(stage_id)
        if stage is not None:
            stage.steps_used += max(0, int(steps))
        project.steps_used += max(0, int(steps))


# The subject of a request: the proper-noun phrase it is about. Used to make a
# section title self-contained — see stage_goal.
_SUBJECT_RE = re.compile(
    r"\b((?:[A-Z][\w&.'-]*)(?:\s+(?:[A-Z][\w&.'-]*|of|the|and|for)){0,3})\b")
_SUBJECT_SKIP = frozenset({
    "i", "look", "read", "open", "tell", "give", "show", "make", "create",
    "research", "find", "compare", "check", "then", "please", "my", "me",
    "the", "a", "an", "and", "go", "get", "write", "build", "do", "use",
})
# Words that get capitalised but name a role or a format, never a subject.
# Without this, "who the CEO is" made CEO the subject of the whole request.
_NOT_A_SUBJECT = frozenset({
    "ceo", "cfo", "cto", "coo", "cio", "md", "vp", "hr", "pdf", "csv", "xlsx",
    "docx", "html", "url", "api", "ai", "uk", "us", "usa", "eu", "gbp", "usd",
})
# Connectives a phrase may run through but must not end on.
_TRAILING = ("and", "of", "the", "for", "or", "to", "in", "on", "with")


def subject_of(goal: str) -> str:
    """What the request is ABOUT, as a namable thing, or "".

    Deliberately shallow: the first capitalised phrase that is not a sentence
    opener. It only has to be good enough to stop a section fragment losing its
    referent, and being wrong is bounded — the whole request is quoted directly
    above it either way.
    """
    text = " ".join(str(goal or "").split())
    if not text:
        return ""
    for match in _SUBJECT_RE.finditer(text):
        words = match.group(1).strip().split()
        while words and words[-1].lower() in _TRAILING:
            words.pop()
        if not words:
            continue
        # "Research Monzo" opens with a capitalised verb; the subject is what
        # follows it, not nothing.
        stripped_verb = False
        while words and words[0].lower() in _SUBJECT_SKIP:
            words.pop(0)
            stripped_verb = True
        if not words:
            continue
        phrase = " ".join(words)
        head = words[0].lower()
        if head in _SUBJECT_SKIP or len(phrase) < 3:
            continue
        # A single word naming a role or a file format is not what the request
        # is about — "who the CEO is" must not make CEO the subject.
        if len(words) == 1 and head in _NOT_A_SUBJECT:
            continue
        # A sentence-opening capital is not a subject — unless a leading verb
        # was stripped, in which case what remains is not the opener.
        if match.start() == 0 and len(words) == 1 and not stripped_verb:
            continue
        return phrase[:60]
    return ""


def stage_goal(project: Project, stage: Stage) -> str:
    """The instruction ONE stage works to.

    Scoped on purpose — handing a stage the whole goal is how a run re-does the
    other stages' work in its own transcript, which is what the graph exists to
    prevent.

    **The request comes first and the part second.** The other way round was
    tried and failed live within thirty seconds: the stage goal opened with the
    fragment "how they make money", the model read "they" with a Google search
    page on screen, and went off to research how Google makes money for a
    question about DBS Bank. A fragment is not an instruction; it needs its
    subject, and the subject lives in the request.
    """
    subject = stage.subject or subject_of(project.goal)
    named = f" — the subject is {subject}" if subject else ""

    if stage.kind == KIND_REVIEW:
        return (f"Write the final answer to this request, using ONLY what the "
                f"task established:\n\n{project.goal}")
    if stage.kind == KIND_ARTIFACT:
        # "A spreadsheet-style view of the data is shown in the table above" —
        # what it actually said, having written a markdown table into the chat
        # for a request that asked for a spreadsheet. A table is not a file.
        return (f"Create the FILE this request asks for, using ONLY what the "
                f"task already established — do not go and research it again:\n\n"
                f"{project.goal}\n\n"
                f"Call create_document. A table written into your answer is NOT "
                f"a file and does not count: the request asked for something "
                f"the user can open. Use csv or xlsx for a spreadsheet, pdf or docx "
                f"for a document. Then preview_document it so he can see it. "
                f"If some rows are missing, make the file with what you have and "
                f"say which rows are missing — an incomplete real file beats a "
                f"complete imaginary one.")

    return (f"THE REQUEST (this is what everything below is about):\n"
            f"{project.goal}\n\n"
            f"YOUR PART of it, and the only thing to do now{named}:\n"
            f"{stage.title}"
            + (f" for {stage.subject}" if stage.subject else "")
            + "\n\nAnswer that one part properly and record what you find with "
              "`note`. Do NOT do the other parts — they have their own turn. "
              "Every pronoun above refers to the subject of the request, never "
              "to whatever happens to be on screen.")


# ── checkpoints ────────────────────────────────────────────────────────────
def _path(task_id: str) -> str:
    safe = "".join(ch for ch in str(task_id or "") if ch.isalnum() or ch in "_-")[:64]
    return os.path.join(paths.memory_dir(), f"{safe or 'unnamed'}.project.json")


def save(task_id: str) -> bool:
    """Checkpoint the graph, so a resume continues rather than restarting.

    Work product only: stage titles, statuses and budgets. No grants, no
    scopes, nothing that could authorise an action — those stay in memory and
    still die with ``boot_id``.
    """
    project = get(task_id)
    if project is None:
        return False
    with obs.swallow("supercomputer.project.save", task_id=str(task_id)):
        path = _path(task_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with _LOCK:
            payload = asdict(project)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)
        return True
    return False


def load(task_id: str) -> Optional[Project]:
    with obs.swallow("supercomputer.project.load", task_id=str(task_id)):
        with open(_path(task_id), "r", encoding="utf-8") as fh:
            data = json.load(fh) or {}
        stages = [Stage(**{k: v for k, v in row.items() if k in Stage.__annotations__})
                  for row in (data.get("stages") or []) if isinstance(row, dict)]
        project = Project(
            task_id=str(data.get("task_id") or task_id),
            goal=str(data.get("goal") or ""),
            stages=stages,
            status=str(data.get("status") or P_PLANNING),
            detail=str(data.get("detail") or ""),
            started_ts=float(data.get("started_ts") or time.time()),
            steps_used=int(data.get("steps_used") or 0),
            max_steps=int(data.get("max_steps") or DEFAULT_PROJECT_STEPS),
            max_seconds=float(data.get("max_seconds") or DEFAULT_PROJECT_SECONDS),
        )
        # A stage caught mid-flight by a restart is pending again, not running.
        for stage in project.stages:
            if stage.status == RUNNING:
                stage.status = PENDING
        with _LOCK:
            _PROJECTS[project.task_id] = project
        return project
    return None


def snapshot(task_id: str) -> Dict[str, Any]:
    """What the workspace and the Island render."""
    project = get(task_id)
    if project is None:
        return {}
    done, total = project.progress()
    with _LOCK:
        return {
            "goal": project.goal[:200],
            "status": project.status,
            "detail": project.detail,
            "done": done,
            "total": total,
            "steps_used": project.steps_used,
            "stages": [
                {"id": s.id, "title": s.label(), "kind": s.kind,
                 "status": s.status, "detail": s.detail[:120],
                 "needs": list(s.needs)}
                for s in project.stages
            ],
        }


def _reset_for_tests() -> None:
    forget_all()


__all__ = [
    "KIND_BROWSE", "KIND_RESEARCH", "KIND_COMPUTE", "KIND_ARTIFACT",
    "KIND_REVIEW", "KINDS", "PARALLEL_KINDS",
    "PENDING", "RUNNING", "DONE", "BLOCKED", "SKIPPED",
    "P_PLANNING", "P_RUNNING", "P_PAUSED", "P_DONE", "P_FAILED", "P_CANCELLED",
    "MAX_STAGE_ATTEMPTS",
    "Stage", "Project", "enabled", "plan", "fanout_count", "set_subjects",
    "get", "forget", "forget_all", "set_status", "set_stage_status",
    "note_steps", "stage_goal", "save", "load", "snapshot",
]
