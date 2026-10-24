"""Splitting a Zenith question into parts that can be worked in parallel.

A question like "why did IREN move this week, what caused it, and what does
it mean for the sector" is three investigations wearing one sentence. Done
in sequence they cost three times as long and the evidence for each crowds
out the others in one prompt; done together, each gets its own gathering,
its own reading, and its own small conclusion, and the answer is written
from three condensed findings rather than sixty raw sources.

This is the one thing Zenith does that Spark does not, and the reason its
budget carries ``sub_agents``.

Two rules keep it from becoming the old council, which ran nine specialists
over every deep turn whether or not the question had nine parts:

* **It only splits when the question actually splits.** One subject with one
  question about it goes straight down the ordinary path, and the split
  call is skipped entirely.
* **A finding comes back condensed, not raw.** Each sub-agent returns a few
  hundred words and its sources — not its transcript. Handing the chief
  everything each part read is how a deep turn runs out of context before
  it starts writing.
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import gateway, research, wire
from .budget import Budget
import syrix_obs

_SPLIT_TIER = "T2_BALANCED"
# A sub-agent reports what it found; the chief writes the answer. Measured on
# the deep lanes with the full answer persona and a full evidence pack, each
# report was an 8,000-token request: over the fast lane's per-minute budget,
# so every one fell through to the slowest lane and took 25-59 seconds. On
# the answer lanes, with a short brief and a capped pack, a report fits the
# fast lane and comes back in a few seconds.
_FINDING_TIER = "T2_BALANCED"
_FINDING_EVIDENCE_CHARS = 11000
_MAX_PARTS = 4
_MIN_PARTS = 2
_SPLIT_BUDGET_S = 6.0
_PART_DEADLINE_S = 45.0
_FINDING_TOKENS = 900

_SPLIT_PROMPT = """Split this question into the separate investigations it contains.

A part is worth its own investigation when answering it needs different
evidence from the others. "Why did X move and what does it mean for the
sector" is two: what happened to X, and what it implies for the sector.
"What is the capital of France" is one.

Rules:
- 2 to 4 parts, or none at all.
- Each part must be a complete question that stands on its own, with the
  subject named — a sub-agent sees only its own part.
- Do not invent angles the question did not ask for.
- Return {"parts": []} when it is really one question.

Return JSON only: {"parts": ["...", "..."]}"""


@dataclass
class Finding:
    question: str
    text: str = ""
    sources: List[Dict[str, Any]] = field(default_factory=list)
    took_ms: float = 0.0
    problem: str = ""

    def __bool__(self) -> bool:
        return bool(self.text)


def split(question: str, *, budget: Budget) -> List[str]:
    """The independent parts of this question, or an empty list."""
    if not budget.sub_agents:
        return []
    text = " ".join(str(question or "").split())
    # Cheap gate before a model call: a question with one clause is one
    # question, and asking a model to confirm that costs as much as the
    # split would have saved.
    if len(text.split()) < 12:
        return []

    try:
        completion = gateway.complete(
            [{"role": "system", "content": _SPLIT_PROMPT},
             {"role": "user", "content": text}],
            tier=_SPLIT_TIER, max_tokens=400, temperature=0.1,
            prompt_chars=6000, timeout_s=_SPLIT_BUDGET_S,
            allow_local_fallback=False,
        )
    except Exception:
        return []

    match = re.search(r"\{.*\}", completion.text or "", re.S)
    if not match:
        return []
    try:
        parsed = json.loads(match.group(0))
    except ValueError:
        return []

    parts: List[str] = []
    for item in (parsed.get("parts") or []):
        cleaned = " ".join(str(item).split())
        if cleaned and cleaned.lower() not in {p.lower() for p in parts}:
            parts.append(cleaned)
        if len(parts) >= _MAX_PARTS:
            break
    return parts if len(parts) >= _MIN_PARTS else []


def _investigate(part: str, budget: Budget, request_id: str, index: int) -> Finding:
    """One sub-agent: gather for its own part, and report back briefly."""
    started = time.perf_counter()
    wire.step(wire.KIND_ZENITH, f"Sub-agent {index} looking into it",
              request_id=request_id, detail=part[:160],
              agent_id=f"sub{index}", agent_status="searching")

    try:
        _need, evidence = research.prepare(
            part, budget, request_id=request_id, deadline_s=_PART_DEADLINE_S,
        )
    except Exception as exc:
        return Finding(part, problem=f"gathering failed: {type(exc).__name__}: {exc}")

    block = (research.build(evidence, max_chars=min(research.pack_chars(budget),
                                                    _FINDING_EVIDENCE_CHARS))
             if evidence else "")
    system = ("Today is " + time.strftime("%A %d %B %Y") + ". You are a research "
              "sub-agent: read the evidence below and report what it says. Figures, "
              "dates and names come from the evidence, each with its bracketed source "
              "number. Where the evidence is silent, say what you know and that it "
              "may have changed. Never mention your training or the search itself.")
    instruction = (
        "You are investigating ONE part of a larger question. Answer only "
        "this part, in a few hundred words: what you found, the figures, the "
        "dates, and which source each came from. No preamble, no offer of "
        "further help, and do not try to answer the wider question — someone "
        "else is assembling the parts."
    )

    messages = [{"role": "system", "content": system},
                {"role": "system", "content": instruction}]
    if block:
        messages.append({"role": "system", "content": block})
    messages.append({"role": "user", "content": part})

    try:
        completion = gateway.complete(
            messages, tier=_FINDING_TIER, max_tokens=_FINDING_TOKENS,
            temperature=budget.temperature,
            prompt_chars=_FINDING_EVIDENCE_CHARS + 3000,
        )
    except Exception as exc:
        return Finding(part, problem=f"{type(exc).__name__}: {exc}",
                       sources=research.citations(evidence) if evidence else [])

    took = round((time.perf_counter() - started) * 1000.0, 1)
    wire.step(wire.KIND_ZENITH, f"Sub-agent {index} reported",
              request_id=request_id, detail=f"{took:.0f}ms",
              agent_id=f"sub{index}", agent_status="done")

    return Finding(
        question=part,
        text=(completion.text or "").strip(),
        sources=research.citations(evidence) if evidence else [],
        took_ms=took,
    )


def investigate_all(parts: List[str], budget: Budget, *,
                    request_id: str) -> List[Finding]:
    """Work every part at once."""
    if not parts:
        return []
    wire.step(wire.KIND_ZENITH, f"{len(parts)} sub-agents deployed",
              request_id=request_id, detail="; ".join(parts)[:160],
              agents_active=len(parts))

    findings: Dict[int, Finding] = {}
    pool = ThreadPoolExecutor(max_workers=min(len(parts), _MAX_PARTS),
                              thread_name_prefix="v3sub")
    try:
        futures = {
            pool.submit(_investigate, part, budget, request_id, i + 1): i
            for i, part in enumerate(parts)
        }
        for future in as_completed(futures, timeout=_PART_DEADLINE_S * 2):
            index = futures[future]
            try:
                findings[index] = future.result()
            except Exception as exc:
                findings[index] = Finding(parts[index],
                                          problem=f"{type(exc).__name__}: {exc}")
    except Exception as exc:
        syrix_obs.report("v3.subagents.investigate_all", exc, level="debug")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    return [findings[i] for i in range(len(parts)) if i in findings]


def as_evidence(findings: List[Finding]) -> str:
    """The findings, written for whoever assembles the final answer."""
    usable = [f for f in findings if f]
    if not usable:
        return ""

    blocks = ["WHAT THE SUB-AGENTS FOUND", "",
              "Each part below was investigated separately, with its own "
              "sources. Write one answer from all of them — do not present "
              "them as separate reports, and do not mention that the work "
              "was split up.", ""]
    for i, finding in enumerate(usable, 1):
        blocks.append(f"--- Part {i}: {finding.question}")
        blocks.append(finding.text)
        blocks.append("")

    failed = [f for f in findings if not f]
    if failed:
        blocks.append("Could not be investigated: "
                      + "; ".join(f"{f.question} ({f.problem})" for f in failed))
        blocks.append("Say plainly which part you could not cover.")
    return "\n".join(blocks).strip()


def all_sources(findings: List[Finding]) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for finding in findings:
        for source in finding.sources:
            url = str((source or {}).get("url") or "")
            if not url or url in seen:
                continue
            seen.add(url)
            out.append(source)
    return out
