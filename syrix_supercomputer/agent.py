"""Task setup and the completion report, around :mod:`syrix_supercomputer.loop`.

This module owns the two ends of a task — what it is allowed to be, and what
The user is told happened — while the middle (choosing and running actions) belongs
to the agent loop.

The split matters. Scope is decided here, once, from the user's message and the
named-service table, and frozen into a :class:`TaskContract` before the model
gets a turn. So the model can choose *what to do next* without ever being able
to choose *what this task is about* or *where it may go*: widening either needs
a human answering a permission card.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import syrix_obs as obs

from . import destinations, gate, loop, results, tasks
from .browser import controller as browser
from .contract import TaskContract, normalise_host

KIND_BROWSE = "browse"
KIND_BUILD = "build"

_BUILD_RE = re.compile(
    r"\b(build|create|make|generate|scaffold|write)\b.{0,40}"
    r"\b(site|website|page|app|project|landing|dashboard|prototype|clone)\b",
    re.IGNORECASE,
)


def classify(request: str, urls: List[str]) -> str:
    """Kept for the report label and for tests; the loop no longer branches on it."""
    if _BUILD_RE.search(request or ""):
        return KIND_BUILD
    return KIND_BROWSE


def build_contract(task_id: str, request: str, urls: List[str],
                   cfg: Dict[str, Any]) -> TaskContract:
    """Scope comes from the user and nowhere else.

    "Somewhere the user named" includes services named by name — "YouTube" is
    youtube.com — the search engine, because a task allowed to look things up
    must be able to reach the engine it looks them up on, and **the sites
    already open in his browser**. What it still never includes is anywhere a
    *page* suggested.

    Open tabs count because the user opened them: "read this article", "compare
    these two" and "is this site legitimate" all point at a page he chose, and
    scoping that out meant the agent could read the tab it was on but was
    refused every link inside it — so it clicked in circles and reported that
    the article could not be opened. This is his browsing context entering the
    scope, not the model widening its own.
    """
    hosts: List[str] = []
    for url in urls:
        host = destinations.scope_host(url)
        if host and host not in hosts:
            hosts.append(host)
    for host in destinations.seed_domains(request, cfg):
        if host not in hosts:
            hosts.append(host)
    for host in _open_tab_hosts(cfg):
        if host not in hosts:
            hosts.append(host)
    return TaskContract(
        task_id=task_id,
        goal=request,
        allowed_domains=frozenset(hosts),
        allowed_actions=frozenset(gate.capabilities.enabled_actions(cfg)),
    )


def _open_tab_hosts(cfg: Dict[str, Any]) -> List[str]:
    """Hosts of the pages the user already has open. Never raises."""
    out: List[str] = []
    with obs.swallow("supercomputer.agent.open_tab_hosts"):
        res = browser.tabs(config=cfg)
        if not res.get("ok"):
            return out
        for tab in (res.get("data") or {}).get("tabs") or []:
            url = str(tab.get("url") or "")
            if not url.lower().startswith(("http://", "https://")):
                continue
            host = destinations.scope_host(url)
            if host and host not in out:
                out.append(host)
    return out


def run(request: str, urls: List[str], *, rid: str, cfg: Dict[str, Any],
        context: str = "") -> Dict[str, Any]:
    """Execute one task. Returns a report dict; never raises.

    ``context`` is what the session has already done — earlier turns and the
    digests of finished tasks — so a follow-up has something to resolve against.
    It is background only: the contract goal is still exactly what the user just
    said, which keeps the "goal is immutable once captured" property intact.
    """
    kind = classify(request, urls)
    task = tasks.create(request, build_contract(rid or "sc", request, urls, cfg))
    browser.set_contract(task.contract)

    report: Dict[str, Any] = {
        "kind": kind, "task_id": task.task_id, "done": [], "not_done": [],
        "declined": [], "answer": "", "notices": [], "question": "",
    }
    try:
        staged = _run_as_project(request, task, rid=rid, cfg=cfg, urls=urls,
                                 context=context)
        if staged is not None:
            report.update(staged)
        else:
            report.update(loop.run(
                request, task_id=task.task_id, request_id=rid, config=cfg,
                seed_urls=urls, context=context,
            ))
    except Exception as exc:
        obs.report("supercomputer.agent.run", exc, task_id=task.task_id)
        tasks.block_remaining(task.task_id, detail=type(exc).__name__)
        tasks.set_state(task.task_id, tasks.STATE_FAILED, detail=type(exc).__name__)
        report["not_done"].append(f"the task stopped on {type(exc).__name__}")
    report["task_id"] = task.task_id
    report["kind"] = kind
    return report


def _run_as_project(request: str, task, *, rid: str, cfg: Dict[str, Any],
                    urls: List[str], context: str) -> Optional[Dict[str, Any]]:
    """Run this as a staged project, or return None to use the single loop.

    The decision lives in :func:`project.plan`, which is deliberately hard to
    trigger — a request that a single loop already handles must never pay for a
    scheduler. Any failure here returns None, so the worst case is the mode
    behaving exactly as it did before the runtime existed.
    """
    with obs.swallow("supercomputer.agent.project", task_id=task.task_id):
        from . import project, runtime

        if not runtime.enabled(cfg):
            return None
        proj = project.plan(task.task_id, request, config=cfg, seed_urls=urls)
        if proj is None:
            return None
        tasks.plan_steps(task.task_id, [s.label() for s in proj.stages])
        tasks.set_state(task.task_id, tasks.STATE_RUNNING)

        def _run_stage(goal, *, stage_id, kind, max_steps, budget_s,
                       request_id, config):
            # Each stage is its own short run of the loop it already trusts,
            # with its own budget and its own evidence slot. Nothing about how
            # a step works changes; only what it is pointed at.
            stage_cfg = dict(config or {})
            stage_cfg["supercomputer_max_steps"] = int(max_steps)
            stage_cfg["supercomputer_task_budget_s"] = float(budget_s)
            return loop.run(goal, task_id=task.task_id, request_id=request_id,
                            config=stage_cfg, seed_urls=urls,
                            context=_stage_context(task.task_id, kind, context,
                                                   cfg),
                            stage_id=stage_id, kind=kind)

        return runtime.run(proj, request_id=rid, config=cfg,
                           run_stage=_run_stage,
                           on_progress=lambda: _publish_project(task.task_id, cfg))
    return None


def _stage_context(task_id: str, kind: str, base: str,
                   cfg: Dict[str, Any]) -> str:
    """What a stage is told about what the earlier stages found.

    THE point of the evidence store, and it was written but never read back —
    so on the first full live run the research stages gathered DBS Bank's
    revenue model, its CEO and its 2025 figures, and then the stage told to
    "create the spreadsheet" started with a blank transcript and no idea any of
    it existed. It produced a markdown table in chat and called that a
    spreadsheet; the completion validator correctly reported the file missing.

    A gathering stage gets only the conversation, because handing it the other
    stages' findings is how it ends up answering their part instead of its own.
    The stages that BUILD get everything, because building from the whole task
    is precisely their job.
    """
    from . import evidence, project

    if kind not in (project.KIND_ARTIFACT, project.KIND_REVIEW):
        return base
    with obs.swallow("supercomputer.agent.stage_context", task_id=task_id):
        brief = evidence.render(
            task_id, header="EVERYTHING THE EARLIER STAGES ESTABLISHED — build "
                            "from THIS, and do not go and look it up again")
        if brief:
            return f"{brief}\n\n{base}".strip() if base else brief
    return base


def _publish_project(task_id: str, cfg: Dict[str, Any]) -> None:
    """Keep the Steps panel and the Island honest between stages."""
    with obs.swallow("supercomputer.agent.publish_project"):
        from . import hud_api, project

        snap = project.snapshot(task_id)
        for index, row in enumerate(snap.get("stages") or []):
            state = {
                project.DONE: tasks.STEP_DONE,
                project.RUNNING: tasks.STEP_RUNNING,
                project.BLOCKED: tasks.STEP_BLOCKED,
                project.SKIPPED: tasks.STEP_SKIPPED,
            }.get(row.get("status") or "", tasks.STEP_PENDING)
            tasks.set_step(task_id, index, state, detail=str(row.get("detail") or ""))
        hud_api.push_state(cfg)


def plan_labels(kind: str, urls: List[str]) -> List[str]:
    """A deterministic plan, used when the planning model gives nothing back.

    The Steps panel is scaffolding, not a precondition for working — a task that
    refused to start because it could not phrase its own plan would be absurd.
    """
    if kind == KIND_BUILD:
        labels = ["Understand the request and define the scope"]
        if urls:
            labels.append(f"Inspect {normalise_host(urls[0])} for structure and layout")
        labels += ["Generate the project files", "Start a local preview",
                   "Verify the preview loads"]
        return labels
    labels = ["Understand the request and define the scope"]
    for u in urls[:3]:
        labels.append(f"Open {normalise_host(u)}")
        labels.append(f"Read and extract key data from {normalise_host(u)}")
    labels.append("Analyse and summarise the findings")
    return labels


_FILE_RE = re.compile(r"^===\s*FILE:\s*(?P<name>[^\n=]+?)\s*===\s*$", re.MULTILINE)


def parse_files(text: str) -> Dict[str, str]:
    """Split the ``=== FILE: name ===`` envelope into ``{relative: content}``."""
    body = str(text or "")
    matches = list(_FILE_RE.finditer(body))
    out: Dict[str, str] = {}
    for i, m in enumerate(matches):
        name = m.group("name").strip().strip("`").replace("\\", "/")
        if not name or name.startswith("/") or ".." in name:
            continue
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        content = body[m.end():end].strip("\n")
        # Models sometimes wrap the body in a fence despite being told not to.
        content = re.sub(r"^```[a-zA-Z]*\n", "", content)
        content = re.sub(r"\n```\s*$", "", content)
        if content.strip():
            out[name] = content
    return out


# ── the report ─────────────────────────────────────────────────────────────
# Actions that are worth counting in a footer. Reading and scrolling happen
# constantly and belong in the Activity panel, not in the answer.
_NOTABLE = ("open_url", "web_search", "create_document", "write_file", "click",
            "type_text", "preview_site")


def _tally(done: List[str]) -> str:
    """A one-line count of what actually happened, not a keystroke log."""
    counts: Dict[str, int] = {}
    for entry in done:
        name = str(entry).split(" (", 1)[0]
        if name in _NOTABLE:
            counts[name] = counts.get(name, 0) + 1
    if not counts:
        return ""
    label = {
        "open_url": "page opened", "web_search": "search run",
        "create_document": "document created", "write_file": "file written",
        "click": "click", "type_text": "field filled",
        "preview_site": "preview opened",
    }
    parts = []
    for name, n in counts.items():
        word = label.get(name, name)
        parts.append(f"{n} {word}{'s' if n > 1 and not word.endswith('s') else ''}")
    return ", ".join(parts)


def _unique_by(rows, key):
    """First occurrence wins, order preserved. Blank keys are never merged."""
    seen = set()
    out = []
    for row in rows:
        k = key(row)
        if k and k in seen:
            continue
        if k:
            seen.add(k)
        out.append(row)
    return out


def format_report(report: Dict[str, Any]) -> str:
    """The completion report. Never hides a failure behind vague wording."""
    parts: List[str] = []
    if report.get("answer"):
        parts.append(report["answer"])
    if report.get("question"):
        parts.append(f"**I need one thing from you:** {report['question']}")
    for notice in report.get("notices", []):
        if notice:
            parts.append(notice)

    lines: List[str] = []
    task_id = report.get("task_id", "")
    entries = results.for_task(task_id)

    # Deduped again at the point of display. results.source already folds
    # repeat visits, but a source can also arrive from the evidence store, and
    # a citation list that repeats itself makes one page look like two.
    sources = _unique_by(
        [r for r in entries if r["kind"] == "source"],
        lambda r: results._normalise_url(r.get("location") or "") or r.get("title", ""),
    )
    if sources:
        lines.append("**Sources:** " + "; ".join(
            f"{r['title']} ({r['location']})" for r in sources[:6]) + ".")

    created = _unique_by([r for r in entries if r["kind"] == "file"],
                         lambda r: str(r.get("location") or "").lower())
    if created:
        lines.append("**Files created:** " + ", ".join(r["location"] for r in created[:8]) + ".")

    tally = _tally(report.get("done") or [])
    if tally:
        lines.append(f"**Did:** {tally}.")
    if report.get("declined"):
        lines.append("**You declined:** " + "; ".join(report["declined"]) + ".")
    if report.get("not_done"):
        lines.append("**Not done:** " + "; ".join(report["not_done"]) + ".")
    if report.get("preview_url"):
        lines.append(f"**Preview:** {report['preview_url']} — take control to click around.")

    if lines:
        parts.append("\n".join(lines))
    if not parts:
        parts.append("I did not manage to do anything on that. Nothing was changed or sent.")
    return "\n\n".join(parts)


__all__ = ["run", "classify", "plan_labels", "build_contract", "parse_files",
           "format_report", "KIND_BROWSE", "KIND_BUILD"]
