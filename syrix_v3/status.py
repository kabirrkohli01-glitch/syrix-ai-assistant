"""What SYRIX is actually running, right now.

Asked for after a session spent guessing. The interface said nothing about
which engine answered, which lane it thought on, or what was left in the
day's allowance — so a v2 answer and a v3 answer looked identical, and the
only way to tell them apart was to recognise a v2 turn of phrase.

Everything here is read, never inferred: the engine version comes from the
package, the lane health and remaining budget come from the pool's own
ledger, and the local model's state comes from asking its port. A status
panel that reports what it assumes is worse than none, because it is
believed.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from . import budget as budgets
import syrix_obs

ENGINE = "v3"
VERSION = "3.0.0"


def _pool_status() -> Dict[str, Any]:
    try:
        from . import gateway
        return gateway.status() or {}
    except Exception as exc:
        return {"configured": False, "error": f"{type(exc).__name__}: {exc}"}


def _lane_rows(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One row per provider, in the order they would actually be tried."""
    rows: List[Dict[str, Any]] = []
    for entry in (raw.get("providers") or []):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("provider") or "?")
        available = bool(entry.get("available", entry.get("ok", False)))
        cooling = str(entry.get("cooldown_reason") or "")
        rows.append({
            "name": name,
            "available": available,
            "state": ("cooling down: " + cooling) if cooling
                     else ("ready" if available else "no key"),
            "models": entry.get("models") or entry.get("model") or "",
            "used_today": entry.get("used_rpd", entry.get("used", "")),
            "limit_today": entry.get("rpd", entry.get("limit", "")),
            "local": name == "ollama",
        })
    # Local is the floor and is shown last, which is the order it is tried.
    rows.sort(key=lambda r: (r["local"], not r["available"]))
    return rows


def _local() -> Dict[str, Any]:
    try:
        from . import local_model
        state = local_model.status()
        return {
            "running": state.running,
            "started_by_syrix": state.ours,
            "detail": state.detail,
            "idle_s": state.idle_s,
            "policy": ("started only when every cloud lane is spent; weights "
                       "leave VRAM 45s after the last answer; the server "
                       "stops after 5 minutes idle and never outlives SYRIX"),
        }
    except Exception as exc:
        return {"running": False, "detail": f"{type(exc).__name__}: {exc}"}


def snapshot() -> Dict[str, Any]:
    """Everything worth showing about the runtime, as plain data."""
    raw = _pool_status()
    lanes = _lane_rows(raw)
    ready = [row["name"] for row in lanes if row["available"] and not row["local"]]

    modes = {}
    for mode in (budgets.SPARK, budgets.ZENITH):
        plan = budgets.for_mode(mode)
        modes[mode] = {
            "tier": plan.tier,
            "deadline_s": plan.deadline_s or None,
            "rounds": plan.max_rounds,
            "sources": plan.max_sources,
            "sub_agents": plan.sub_agents,
        }

    return {
        "engine": ENGINE,
        "version": VERSION,
        "at": time.time(),
        "cloud_lanes_ready": ready,
        "lanes": lanes,
        "local": _local(),
        "modes": modes,
        "fallback_order": (
            " → ".join(ready) + (" → local" if ready else "local only")
        ),
        "tools": _tool_names(),
    }


def _tool_names() -> List[str]:
    try:
        from .tools import names
        return names()
    except Exception:
        return []


def one_line() -> str:
    """The short form, for a status bar."""
    snap = snapshot()
    ready = snap["cloud_lanes_ready"]
    local = snap["local"]
    bits = [f"SYRIX {snap['version']} ({snap['engine']})"]
    bits.append(f"{len(ready)} cloud lane{'s' if len(ready) != 1 else ''} ready"
                if ready else "no cloud lane ready")
    bits.append("local up" if local.get("running") else "local idle")
    return " · ".join(bits)


# What each tool means the user can ask for. Written as capabilities rather than
# function names, because the question this answers is "can you do X", not
# "what functions are registered".
_WHAT_TOOLS_MEAN = {
    "search": "search the open web and read the pages it finds, live",
    "read": ("open any website or link and follow its links page to page — "
             "a homepage to its About, Careers or Pricing page"),
    "find_on_page": "search within a page already open for a name, figure or section",
    "document": "read attached PDF, Word, Excel, PowerPoint, CSV, text and code files",
    "look": "see images and screenshots",
    "calculate": "evaluate arithmetic exactly, rather than estimating it",
    "memory": "remember things about him between conversations",
    "self_knowledge": "read its own source code",
    "runtime_status": "read its own runtime state",
}


def capability_block() -> str:
    """The grounded facts for a question about SYRIX itself.

    Prefetched and handed over as evidence rather than left to the persona,
    for the same reason attachments are: a model answering from its own priors
    about what "an AI" can do says it has no internet access. Measured on the
    local lane, repeatedly — "for this session, I don't have direct internet
    access", said while the web tool sat registered and working. A small model
    follows facts in front of it far more reliably than character notes, and
    this is the difference between the fallback sounding broken and sounding
    like the same assistant.
    """
    snap = snapshot()
    ready = snap.get("cloud_lanes_ready") or []
    local = snap.get("local") or {}
    tools = snap.get("tools") or []

    lines = [
        "WHAT YOU ARE, READ FROM THE RUNNING SYSTEM",
        "",
        f"Engine: {snap.get('engine')} · version {snap.get('version')}.",
        # How SYRIX is built, stated so a question about its technology has a
        # true answer to give. Without it a lane filled the gap from a web
        # search that found syrix.io, an unrelated company, and described
        # that product's architecture as its own.
        "Built as: a Python backend and an Electron interface on his Windows PC. "
        "Answers are written by language models reached through the "
        "model lanes below, with a local model on this PC as the fallback; this "
        "engine adds the research, reading, memory and checking around them. "
        "Speech in is local faster-whisper and speech out is local Kokoro. "
        "Nothing more about your build is known: never invent layer or "
        "parameter counts, frameworks, databases, hosts or certifications. "
        "Products and companies online named Syrix (syrix.io and others) are "
        "unrelated to you.",
        "",
        "You can, right now:",
    ]
    for name in tools:
        meaning = _WHAT_TOOLS_MEAN.get(name)
        if meaning:
            lines.append(f"  - {meaning}")
    lines += [
        "",
        f"Model lanes ready: {len(ready)}"
        + (f" ({', '.join(ready)})" if ready else "")
        + ". Local model: "
        + ("running" if local.get("running") else "idle, started only if every cloud lane is spent")
        + ".",
        "",
        "These are facts about this machine, not guesses. Answer a question "
        "about what you can do from THIS, never from what a general assistant "
        "without tools would be able to do. In particular you DO have live web "
        "access, you DO read attached documents, you DO see images, and you DO "
        "keep memory between conversations. Which lane is serving this turn is "
        "internal plumbing: never mention a provider, a model name or a "
        "fallback unless he asks what you are running on.",
        "",
    ]
    return "\n".join(lines)


def as_text() -> str:
    """The long form, for a person reading it."""
    snap = snapshot()
    lines = [
        f"SYRIX {snap['version']} — engine {snap['engine']}",
        "",
        "LANES, in the order they are tried:",
    ]
    for row in snap["lanes"]:
        marker = "  local  " if row["local"] else "  cloud  "
        used = ""
        if row["used_today"] != "" and row["limit_today"] != "":
            used = f"   {row['used_today']}/{row['limit_today']} today"
        lines.append(f"{marker}{row['name']:<14} {row['state']}{used}")

    local = snap["local"]
    lines += [
        "",
        f"LOCAL MODEL: {local.get('detail', 'unknown')}",
        f"  {local.get('policy', '')}",
        "",
        "MODES:",
    ]
    for mode, spec in snap["modes"].items():
        deadline = f"{spec['deadline_s']:.0f}s" if spec["deadline_s"] else "no limit"
        lines.append(
            f"  {mode:<7} {spec['tier']:<13} {deadline:<9} "
            f"{spec['rounds']} rounds, up to {spec['sources']} sources"
            + (", splits work" if spec["sub_agents"] else "")
        )
    lines += ["", "TOOLS: " + ", ".join(snap["tools"])]
    return "\n".join(lines)


def publish() -> None:
    """Write the snapshot where the interface can read it. Never raises."""
    try:
        from . import wire
        wire.flags(syrix_v3_status=snapshot())
    except Exception as exc:
        syrix_obs.report("v3.status.publish", exc, level="debug")
