"""When the user says HOW to do it, that is not a suggestion.

The failure
-----------
"Just go and search the DBS website." "Do it in the tab that's already open."
"Only use the official site." The mode would hear the subject, ignore the
method, and go off and run nine Google searches anyway — arriving somewhere
reasonable by a route he had explicitly ruled out, slowly.

That is worse than being wrong. He gave the instruction because he knows
something the agent does not: which source is authoritative, which page he is
looking at, how much time this is worth.

The rule
--------
If he said how, that is the method. If he did not, the agent works it out —
that half is not touched, because "figure out how to get from A to Z" is the
whole point of the mode when no route was given.

Extraction is structural: an imperative phrase naming a route, matched against
a small closed set of route kinds. It never guesses at intent beyond that, and
an unrecognised instruction leaves the run exactly as it was.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# Route kinds a direct instruction can name.
USE_OPEN_TAB = "use_open_tab"
ONLY_SITE = "only_site"
NO_SEARCH = "no_search"
SEARCH_FIRST = "search_first"
QUICK = "quick"

# Each rule: (kind, pattern). Deliberately anchored on imperative phrasing so
# ordinary description ("the page is already open") is not read as an order.
_RULES: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    (USE_OPEN_TAB, re.compile(
        r"\b(?:in|on|use|using|from|with)\s+(?:the\s+)?"
        r"(?:tab|page|site|window|one)\s+(?:that\s+is|that's|thats|which\s+is|"
        r"already|i\s+have|currently)?\s*(?:already\s+)?"
        r"(?:open|opened|up|on\s+screen|here|beside\s+you)\b", re.I)),
    (USE_OPEN_TAB, re.compile(
        r"\b(?:this|that|the)\s+(?:tab|page)\s+(?:only|itself)\b", re.I)),
    (USE_OPEN_TAB, re.compile(r"\bdon'?t\s+open\s+(?:anything|any)\s+new\b", re.I)),
    (NO_SEARCH, re.compile(
        r"\b(?:don'?t|do\s+not|no|without)\s+(?:google|search|searching|"
        r"web\s+search)\b", re.I)),
    (SEARCH_FIRST, re.compile(r"\b(?:google|search\s+for)\s+it\s+first\b", re.I)),
    (QUICK, re.compile(
        r"\b(?:quick(?:ly)?|fast|briefly|just\s+tell\s+me|one\s+line|"
        r"in\s+a\s+sentence|short(?:\s+answer)?)\b", re.I)),
)

# "go to the DBS website", "only use bbc.co.uk", "search the Companies House
# site". The captured group is the site as the user named it.
_SITE_RULES = (
    re.compile(r"\b(?:only\s+)?(?:use|check|read|search|go\s+to|look\s+at|on)\s+"
               r"(?:the\s+)?([a-z0-9][\w.-]*\.[a-z]{2,})\b", re.I),
    re.compile(r"\b(?:go(?:\s+and)?\s+)?(?:search|check|look\s+at|read|use)\s+"
               r"(?:the\s+)?([A-Z][\w&'-]*(?:\s+[A-Z][\w&'-]*){0,2})\s+"
               r"(?:web\s?site|website|site|page)\b"),
)

# What each kind tells the agent, appended to the goal as a scope note. Written
# as instructions to the agent, not as prose about the user.
_SCOPE = {
    USE_OPEN_TAB: (
        "The user told you to use the page that is ALREADY OPEN. Do not open "
        "anything new and do not search — read the tab on screen with "
        "read_page, scroll it, and answer from it. If it genuinely does not "
        "contain the answer, say so and say what it does contain; do not go "
        "somewhere else instead."),
    NO_SEARCH: (
        "The user told you NOT to search. Go directly to the source he named or to "
        "the official site and read it. web_search is not available to you for "
        "this task."),
    SEARCH_FIRST: (
        "The user told you to search first. Run web_search before opening "
        "anything, and open a result rather than guessing at a URL."),
    QUICK: (
        "The user asked for this QUICKLY. Take the shortest honest route: read what "
        "is in front of you, answer in a sentence or two, and stop. Do not "
        "open a series of sources to be thorough — he asked for speed and "
        "thoroughness is not what he wants here."),
}


def _site_scope(site: str) -> str:
    return (f"The user told you to use {site}. Go there and read it — its own "
            f"search box if you need one. Do not answer from a different site, "
            f"and do not run a general web search unless {site} genuinely does "
            f"not have it, in which case say so first.")


def extract(request: str) -> List[str]:
    """The route kinds the user named, in the order they appear. [] when none."""
    text = " ".join(str(request or "").split())
    if not text:
        return []
    found: List[str] = []
    for kind, pattern in _RULES:
        if pattern.search(text) and kind not in found:
            found.append(kind)
    return found


def named_site(request: str) -> str:
    """The site the user told the agent to use, or ""."""
    text = " ".join(str(request or "").split())
    for pattern in _SITE_RULES:
        match = pattern.search(text)
        if match:
            site = match.group(1).strip(" .,")
            # A bare verb or pronoun is not a site.
            if len(site) >= 3 and site.lower() not in ("the", "it", "this", "that"):
                return site[:60]
    return ""


def scope_note(request: str) -> str:
    """The scope block to append to a goal, or "" when he gave no method.

    Empty is the common case and the important one: when the user says only WHAT
    he wants, nothing here fires and the agent works out the route itself.
    """
    parts: List[str] = []
    kinds = extract(request)
    site = named_site(request)

    # A named site and "use the open tab" contradict each other; the open tab
    # is more specific about what he is pointing at, so it wins.
    if USE_OPEN_TAB in kinds:
        site = ""

    for kind in kinds:
        note = _SCOPE.get(kind)
        if note:
            parts.append(note)
    if site:
        parts.append(_site_scope(site))
    if not parts:
        return ""
    return ("\n\n[HOW the user told you to do this — these are instructions, not "
            "preferences, and they override your own judgement about the best "
            "route:\n" + "\n".join(f"- {p}" for p in parts) + "]")


def forbids_search(request: str) -> bool:
    """True when the agent must not run a web search for this task."""
    kinds = extract(request)
    return NO_SEARCH in kinds or USE_OPEN_TAB in kinds


def wants_speed(request: str) -> bool:
    return QUICK in extract(request)


def enabled(config: Optional[Dict[str, Any]] = None) -> bool:
    return bool((config or {}).get("supercomputer_directives_enabled", True))


__all__ = [
    "USE_OPEN_TAB", "ONLY_SITE", "NO_SEARCH", "SEARCH_FIRST", "QUICK",
    "extract", "named_site", "scope_note", "forbids_search", "wants_speed",
    "enabled",
]
