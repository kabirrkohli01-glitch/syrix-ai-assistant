"""What did the user actually ask for, and is every part of it in the answer?

The failure this closes
-----------------------
Asked to "research this company and tell me what it does, how it makes money,
who its customers are, who the current CEO is, its main competitors, its
business model and its risks", the mode would come back with two solid
paragraphs about what the company does — and simply not mention the CEO, the
competitors or the risks. Nothing was wrong with what it said. It just answered
three of seven questions and presented that as the answer, so the user had to
re-read his own request to notice.

The same shape appears with artefacts: "create a professional PDF covering X,
Y and Z" comes back with the findings in chat and no file, or a file with two
of the three sections in it.

:mod:`syrix_core_v2.answer_coverage` already answers "does this reply contain
the *kind* of thing asked for" (a price, a date, a verdict). This module answers
the different question "does it contain *every part* asked for", which a
compound request needs and a type check cannot see.

How it works
------------
Deterministic and structural — no model call, and **no matching on hedging
phrases**, which has failed four times in this codebase (four runs, four
phrasings). A request part is found in the answer when the answer contains one
of that part's own content words or a listed equivalent. That is
wording-independent by construction: it asks whether the subject is present,
not how the sentence was phrased.

Two consumers:

* the completion gate pushes back **once**, naming the missing parts exactly;
* the final report **labels** whatever is still missing, so a gap is stated
  rather than silently dropped.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

# ── the request verbs a list of parts hangs off ────────────────────────────
# "tell me A, B and C" / "covering A, B and C" / "including A, B and C".
# Anchoring on these keeps ordinary prose ("I went to the shop, then home")
# from being read as a list of deliverables.
_LIST_ANCHOR = re.compile(
    r"\b(?:tell\s+me|give\s+me|show\s+me|covering|cover|including|include|"
    r"with|about|explaining|explain|detailing|detail|list|compare|comparing|"
    r"summaris\w*|summariz\w*|analys\w*|analyz\w*|report\s+on|research)\b",
    re.I,
)

# A list can also be set off by dashes or a colon rather than by a verb —
# "compare the voice APIs — latency, quality, pricing and free tier — and
# recommend one". That is the same structure with different punctuation.
# A spaced ASCII hyphen counts too — that is what gets typed in practice
# ("...for a real-time assistant - latency, quality, pricing - and recommend
# one"), and requiring a true em dash lost the first item of the list every
# time. The spaces matter: they keep "real-time" and "free-tier" intact.
_DASH_LIST = re.compile(
    r"(?:[—–]|\s-\s)\s*(?P<body>(?:(?![—–]|\s-\s).){10,200})\s*(?:[—–]|\s-\s)"
    # A colon introduces a list only with WHITESPACE after it. Without that,
    # "open https://chromium.org/notes and summarise" was split on the colon in
    # the scheme and "//chromium.org/notes" became a requested section.
    r"|:[ 	]+(?P<tail>.{10,200})$")

# URLs are never requested sections, and their punctuation confuses every
# splitter. Removed before parsing, not matched around.
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)

# Splitting a list: commas and the final "and"/"or", plus semicolons.
_SPLIT = re.compile(r"\s*(?:,|;|\band\b|\bplus\b|\bas\s+well\s+as\b)\s*", re.I)

# Words that carry no subject on their own, so a part made only of these is
# not a deliverable.
_STOP = frozenset("""
a an the its it their his her our your my this that these those of for to in on
at by with from about into over under is are was were be been being do does did
what who whom whose which how why when where any all some each other others more
most much many few several one two three both either neither not no nor so than
then there here also just only very really quite such same different current
main key major top best worst new old latest recent overall general specific
please me you he she they we us them i and or but if as well
""".split())

# Equivalences, so "who runs it" satisfies a request for the CEO and "rivals"
# satisfies "competitors". Deliberately small and concrete — this is a synonym
# table, not a thesaurus, and every entry is a phrasing seen in a real answer.
_EQUIVALENT: Dict[str, Tuple[str, ...]] = {
    "ceo": ("ceo", "chief executive", "founder", "co-founder", "runs it",
            "leadership", "leads the company", "president"),
    "competitor": ("competitor", "competition", "rival", "compet", "alternative",
                   "versus", " vs "),
    "customer": ("customer", "client", "user", "audience", "market", "buyer"),
    "revenue": ("revenue", "money", "makes money", "monetis", "monetiz",
                "pricing", "subscription", "income", "earns", "business model",
                "sales", "charges"),
    "risk": ("risk", "threat", "challenge", "headwind", "weakness", "concern",
             "downside", "exposure"),
    "strategy": ("strategy", "strategic", "plan", "direction", "roadmap",
                 "priorit", "focus"),
    "pricing": ("pricing", "price", "cost", "tier", "plan", "$", "£", "€",
                "free tier", "per month"),
    "leadership": ("leadership", "ceo", "chief executive", "management",
                   "executive", "founder"),
    "product": ("product", "offering", "service", "platform", "tool", "app"),
    "history": ("history", "founded", "started", "origin", "began"),
    "latency": ("latency", "ms", "millisecond", "response time", "delay",
                "time to first"),
    "quality": ("quality", "natural", "realistic", "fidelity", "mos", "clarity"),
    "market": ("market", "industry", "sector", "segment", "target"),
    "growth": ("growth", "growing", "expansion", "scale", "traction"),
    "funding": ("funding", "raised", "investor", "valuation", "series"),
    "competitors": ("competitor", "rival", "compet", "alternative"),
    "risks": ("risk", "threat", "challenge", "weakness", "concern"),
}

# File formats a request can name, mapped to the extensions that satisfy them.
_ARTEFACTS: Dict[str, Tuple[str, ...]] = {
    "pdf": (".pdf",),
    "spreadsheet": (".xlsx", ".csv"),
    "excel": (".xlsx",),
    "csv": (".csv",),
    "word document": (".docx",),
    "docx": (".docx",),
    "presentation": (".pptx",),
    "slide deck": (".pptx",),
    "powerpoint": (".pptx",),
    "website": (".html",),
    "web page": (".html",),
    "document": (".pdf", ".docx", ".md", ".html", ".txt"),
    "report": (".pdf", ".docx", ".md", ".html", ".txt"),
    "chart": (".png", ".jpg", ".svg", ".pdf"),
    "graph": (".png", ".jpg", ".svg", ".pdf"),
}

# Verbs that make a list item an ACTION rather than a subject. "…give me the
# top stories, and save them" asks for one subject and one action; treating
# "save them" as a missing section would push back on an answer that listed
# every story. Actions are already covered by the artefact check and by the
# gates that verify a claimed file exists.
_ACTION_WORDS = frozenset("""
save saves saving send sends sending download downloads downloading upload
uploads uploading open opens opening create creates creating make makes making
publish publishes publishing share shares sharing email emails emailing print
prints printing show shows showing tell tells telling give gives giving find
finds finding get gets getting check checks checking look looks looking read
reads reading write writes writing add adds adding put puts putting keep keeps
keeping use uses using do does doing set sets setting run runs running go goes
going click clicks clicking type types typing attach attaches attaching
""".split())

# A part shorter than this is a connective, not a subject.
_MIN_PART_WORDS = 1
_MAX_PARTS = 12


def _content_words(part: str) -> List[str]:
    words = re.findall(r"[a-z][a-z'\-]{2,}", str(part or "").lower())
    return [w for w in words if w not in _STOP]


def _groups_for(word: str) -> List[Tuple[str, ...]]:
    """Equivalence groups a request word belongs to.

    Reverse lookup, not just a key match: "how it makes MONEY" and "its
    BUSINESS MODEL" both ask about revenue, and neither uses the word
    "revenue". A group is claimed when the word is the key, or is one of the
    group's own surface forms (whole-word, so "sales" matches "sales" but not
    "wholesalers").
    """
    stem = word.rstrip("s")
    out: List[Tuple[str, ...]] = []
    for key, forms in _EQUIVALENT.items():
        if word == key or stem == key.rstrip("s"):
            out.append(forms)
            continue
        for form in forms:
            if word in form.split() or stem in [f.rstrip("s") for f in form.split()]:
                out.append(forms)
                break
    return out


def _needles(part: str) -> List[str]:
    """Every string whose presence would show the part was addressed."""
    words = _content_words(part)
    out: List[str] = []
    for w in words:
        out.append(w)
        # Match the stem, so "competitors" is found by "competitor" and
        # "competing". Five characters is enough to stay specific.
        if len(w) > 5:
            out.append(w[:-1])
        for forms in _groups_for(w):
            out.extend(forms)
    seen: Set[str] = set()
    uniq: List[str] = []
    for n in out:
        n = n.strip()
        if len(n) >= 3 and n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq


def requested_sections(goal: str) -> List[str]:
    """The distinct subjects a compound request asks for, in order.

    Empty for a simple request — a one-part question has nothing to check
    against, and inventing parts for it would push back on a perfectly good
    short answer.
    """
    text = " ".join(str(goal or "").split())
    if not text:
        return []
    text = _URL_RE.sub(" ", text)
    # A dash- or colon-delimited list is the list, wherever the verb sits.
    dashed = _DASH_LIST.search(text)
    if dashed:
        tail = dashed.group("body") or dashed.group("tail") or ""
    else:
        # The LAST request verb, not the first: "Research this company and tell
        # me what it does, how it makes money…" hangs its list off "tell me",
        # and anchoring on "research" made "this company" a section.
        anchors = list(_LIST_ANCHOR.finditer(text))
        if not anchors:
            return []
        tail = text[anchors[-1].end():]
    # Stop at a sentence boundary: the list belongs to this clause.
    tail = re.split(r"(?<![A-Z])\.\s+|\?\s+|\bthen\b", tail, maxsplit=1)[0]
    raw = [p.strip(" .:-—") for p in _SPLIT.split(tail)]
    parts: List[str] = []
    for p in raw:
        if not p:
            continue
        words = _content_words(p)
        if len(words) < _MIN_PART_WORDS:
            continue
        # An instruction to do something is not a section to cover.
        if all(w in _ACTION_WORDS for w in words):
            continue
        # A whole clause is not a section; keep the subject phrase.
        if len(p.split()) > 9:
            continue
        parts.append(p[:70])
        if len(parts) >= _MAX_PARTS:
            break
    # One part is not a compound request.
    return parts if len(parts) >= 2 else []


def missing_sections(goal: str, answer: str) -> List[str]:
    """Requested subjects the answer never mentions. Order follows the goal."""
    body = " ".join(str(answer or "").lower().split())
    if not body:
        return []
    out: List[str] = []
    for part in requested_sections(goal):
        needles = _needles(part)
        if not needles:
            continue
        if not any(n in body for n in needles):
            out.append(part)
    return out


def requested_artefacts(goal: str) -> List[str]:
    """File kinds the request names ("a professional PDF", "a spreadsheet")."""
    text = " ".join(str(goal or "").lower().split())
    # Only when the request actually asks for one to be produced — "read the
    # PDF in my Downloads" names a pdf and asks for no file at all.
    if not re.search(r"\b(?:creat|mak|build|writ|generat|produc|export|sav|draw|"
                     r"design|prepar|compil|assembl|draft)\w*\b|\bput\s+it\s+in\b",
                     text):
        return []
    found: List[str] = []
    for name in _ARTEFACTS:
        if re.search(rf"\b{re.escape(name)}s?\b", text) and name not in found:
            found.append(name)
    # "document"/"report" are generic: drop them when a specific format is
    # also named, so "a professional PDF report" is one artefact, not two.
    specific = [f for f in found if f not in ("document", "report")]
    return specific or found


def missing_artefacts(goal: str, files: Sequence[str]) -> List[str]:
    """Requested file kinds with no matching file produced."""
    have = [str(f or "").lower() for f in files or []]
    out: List[str] = []
    for name in requested_artefacts(goal):
        exts = _ARTEFACTS.get(name, ())
        if not any(h.endswith(exts) for h in have if h):
            out.append(name)
    return out


def shortfall(goal: str, answer: str, files: Sequence[str]) -> Dict[str, List[str]]:
    """Everything asked for that is not in the answer or on disk."""
    return {
        "sections": missing_sections(goal, answer),
        "artefacts": missing_artefacts(goal, files),
    }


def pushback(gap: Dict[str, List[str]]) -> str:
    """The message that sends the agent back for the missing parts. "" = done."""
    sections = list(gap.get("sections") or [])
    artefacts = list(gap.get("artefacts") or [])
    if not sections and not artefacts:
        return ""
    bits: List[str] = []
    if sections:
        bits.append(
            "The user asked for " + _join(sections) + " and your answer does not cover "
            + ("them" if len(sections) > 1 else "it") + ". Go and get "
            + ("those" if len(sections) > 1 else "that") + " now — a task with "
            "several parts is not done when some of them are missing.")
    if artefacts:
        bits.append(
            "He also asked for " + _join(artefacts) + ", and no such file exists. "
            "Create it with create_document and save it.")
    bits.append("If a part genuinely cannot be established, keep it in the answer "
                "and say in one line what is missing and where you got blocked — "
                "do not quietly leave it out.")
    return " ".join(bits)


def gap_note(gap: Dict[str, List[str]]) -> str:
    """The line appended to a final answer so a gap is labelled, not dropped."""
    sections = list(gap.get("sections") or [])
    artefacts = list(gap.get("artefacts") or [])
    if not sections and not artefacts:
        return ""
    missing = sections + [f"the {a}" for a in artefacts]
    return "**Not established:** " + _join(missing) + "."


def _join(items: Sequence[str]) -> str:
    items = [str(i).strip() for i in items if str(i).strip()]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def files_for_task(task_id: str) -> List[str]:
    """Basenames of the files a task actually produced. Never raises."""
    try:
        from . import results as _results

        return [os.path.basename(str(r.get("location") or r.get("title") or ""))
                for r in _results.for_task(task_id) if r.get("kind") == "file"]
    except Exception:
        return []


__all__ = [
    "requested_sections", "missing_sections",
    "requested_artefacts", "missing_artefacts",
    "shortfall", "pushback", "gap_note", "files_for_task",
]
