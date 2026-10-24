"""The only thing that touches an answer after the model writes it.

The runtime this replaces had fourteen modules re-reading every reply, some
of them calling the model again to do it. Together they accounted for most
of a turn's latency and they disagreed with each other. There is one module
here and it does one job: remove text that is provably machine plumbing
rather than prose.

The distinction is the whole rule. A citation marker like the one a model
emits when it has been trained to footnote is not something a person wrote
and not something a person should read; deleting it changes nothing about
what the answer says. A sentence that hedges, or commits, or runs long, IS
what the answer says, and nothing here touches it. The previous runtime had
a guard that deleted any sentence naming an action and a direction, which
meant SYRIX could read every filing a company had made and then remove the
one sentence that took a view.

Nothing here shortens an answer, softens it, or removes a conclusion.
"""
from __future__ import annotations

import re

# A numbered citation in the dialect several lanes are trained to write,
# 【1†L1-L3】: source 1, lines 1 to 3. The evidence those lanes are given is
# numbered the same way, so the number is kept as the [1] the interface turns
# into a source chip. Deleting it, as the marker below still is when it names
# no source, threw away the attribution the answer had made. A number with no
# source behind it is dropped by the interface rather than shown.
_NUMBERED_CITATION = re.compile(r"【(\d{1,2})(?:†[^】]{0,80})?】")

# Footnote markers models emit when they have been trained to cite, in the
# several dialects they come in. None of these is prose.
_CITATION_ARTEFACTS = (
    re.compile(r"【[^】]{0,80}】"),                    # 【turn0search3】, 【oai_citation…】
    re.compile(r"\[citation:[^\]]{0,80}\]", re.I),
    re.compile(r"\[\^\d{1,3}\]"),                     # [^1]
    re.compile(r"【oai_citation[^】]*】", re.I),
)

# Control tokens from a chat template that escaped into the body.
_CONTROL_TOKENS = re.compile(
    r"<\|(?:im_start|im_end|eot_id|start_header_id|end_header_id|"
    r"begin_of_text|end_of_text|channel|message)\|>"
)

# Tool-protocol markup written into prose instead of emitted through the
# transport. With native tool calling this should never appear; when it
# does it is a provider defect, and the caller is told so rather than the
# user being shown the tags.
# The separator after the tag name is not always whitespace — `<function=name>`
# is a real dialect, and a pattern that only allowed a space matched the
# closing tags and left the opening one on screen.
_TOOL_MARKUP = re.compile(
    r"</?(?:tool_call|function_call|function|parameter|invoke|action|final)"
    r"(?:[\s=][^>]*)?>",
    re.I,
)

# A tool name used as a heading, which is the model narrating the plumbing.
# Measured live: an answer opened "Runtime_status: Search functionality is
# operational but restricted to local resources". The name of the function that
# produced a fact is not part of the fact, and the thinking panel already shows
# which tools ran.
_TOOL_NAME_PREFIX = re.compile(
    r"^\s*(?:runtime_status|self_knowledge|calculate|document|search|read|"
    r"look|memory)\s*:\s*",
    re.I,
)

# A formulaic preamble announcing that a search happened, at the very start of
# the answer. "Based on the sources gathered:", "According to the sources I've
# reviewed:", "Based on recent searches,".
#
# This is the one piece of retrieval narration that is safe to cut rather than
# send back for a rewrite, and it earned the exception: the check catches it and
# asks for the answer again, and on a weaker lane the rewrite came back opening
# exactly the same way. It carries no information — the reader did not ask
# whether a search happened and the source list is on screen — and what follows
# it is the answer, untouched. Only a leading clause is removed, and only when
# the sentence continues afterwards.
_SEARCH_PREAMBLE = re.compile(
    r"\A\s*(?:based (?:on|upon)|according to|drawing on|from)\s+"
    r"(?:the\s+|my\s+|recent\s+|these\s+|those\s+|available\s+)*"
    r"(?:sources?|searches|search results?|evidence|information|"
    r"material|research|findings)"
    r"(?:\s+(?:i(?:'ve| have)?\s+)?(?:gathered|reviewed|found|read|"
    r"provided|available|consulted|examined))?"
    r"\s*[,:;.\-—]+\s*",
    re.I,
)

# Sentences about how the answer was made rather than about the subject: pages
# that would not open, access restrictions, "this answer relies on prior
# knowledge", the model's training data. The check already flags them, and
# measured, the repair round it buys (13s on a free lane) came back with the
# same sentence, so the original was kept with it. They are removed here
# instead, at sentence level, and only when a substantial answer remains:
#
# * a leading clause ("While the list could not be opened, typical picks are
#   X and Y") loses the clause and keeps the rest;
# * a sentence that is narration throughout is dropped, unless it carries a
#   bolded item, which is content;
# * lists, tables, headings and code are never touched.
_NARRATION = re.compile(
    r"\bno (?:other )?(?:opened |retrieved )?sources?\b[^.?!]{0,40}"
    r"\b(?:provided|added|gave|contained|covered|opened|read)\b"
    # "was opened" and "could not be opened" only with a page-like subject:
    # a museum that was opened in 1884 is content.
    r"|\b(?:sources?|pages?|articles?|sites?|websites?|links?|results?|lists?|"
    r"guides?|documents?|reports?)\b(?:[^.?!]|\.(?=\w)){0,80}?\b(?:could ?n[o'’]t be|"
    r"(?:was|were)(?: not|n[o'’]t)?(?: fully| successfully)?) "
    r"(?:retrieved|opened|accessed|read|loaded|fetched|accessible)\b"
    r"|\baccess restrictions?\b|\bpaywall(?:ed)?\b"
    r"|\bunable to (?:access|open|retrieve|load)\b"
    r"|\b(?:my|its) (?:training data|knowledge cut-?off|training cut-?off)\b"
    r"|\bknowledge cut-?off\b|\bas of my (?:last|latest|most recent) "
    r"(?:update|training|knowledge)\b"
    r"|\bfrom (?:my |general |prior |established )*(?:prior|general) knowledge\b"
    r"|\b(?:relies|based|drawn) (?:solely |only |entirely )?on (?:my |general |prior )+"
    r"knowledge\b"
    r"|\b(?:would|will|may|might) (?:require|need) (?:verification|confirming|checking)\b",
    re.I,
)
_LEAD_CLAUSE = re.compile(
    r"^(?P<lead>(?:\*\*[^*\n]{1,80}\*\*\s*[–—:-]\s*)?)"
    r"(?:while|although|though|even though|since|as|because)\b(?P<clause>[^,\n]{0,220}),\s*"
    r"(?P<rest>\S.*)$",
    re.I | re.S,
)
_SENTENCE_END = re.compile(
    r"(?:(?<=[.!?])|(?<=[.!?][*_)”\"]))\s+(?=[*_(“\"]*[A-Z0-9])")
_STRUCTURE = re.compile(r"^\s*(?:#|\||[-*•]\s|\d+[.)]\s|>|\x00CODE)")
_MIN_LEFT = 150


def drop_narration(text: str) -> tuple[str, list[str]]:
    """The answer without its sentences about the search. See ``_NARRATION``."""
    body = str(text or "")
    if not _NARRATION.search(body):
        return body, []
    removed: list[str] = []
    paragraphs = body.split("\n\n")
    for i, paragraph in enumerate(paragraphs):
        if "\n" in paragraph.strip() or _STRUCTURE.match(paragraph):
            continue
        kept = []
        for sentence in _SENTENCE_END.split(paragraph.strip()):
            if not _NARRATION.search(sentence):
                kept.append(sentence)
                continue
            lead = _LEAD_CLAUSE.match(sentence)
            if lead and _NARRATION.search(lead.group("clause")) and len(lead.group("rest")) >= 40:
                rest = lead.group("rest")
                if not lead.group("lead"):
                    rest = rest[:1].upper() + rest[1:]
                sentence = lead.group("lead") + rest
                removed.append("a clause about the search")
                if not _NARRATION.search(sentence):
                    kept.append(sentence)
                    continue
            if "**" in sentence:
                kept.append(sentence)
                continue
            removed.append("a sentence about the search")
        left = " ".join(kept).strip()
        # An emphasis marker left without its sentence is not content.
        paragraphs[i] = "" if not left.strip("*_ ") else left
    out = "\n\n".join(p for p in paragraphs if p.strip())
    if len(out.strip()) < _MIN_LEFT:
        return body, []
    return out, removed


_BLANK_RUN = re.compile(r"\n{3,}")
# An image the model wrote into its own answer. Pictures are attached from
# fetched, verified sources; an image link a model writes is recalled rather
# than found, and measured, it wrote plausible Unsplash photo IDs that do not
# exist.
_IMAGE_MARKDOWN = re.compile(r"^[ \t]*!\[[^\]\n]*\]\([^)\n]*\)[ \t]*\n?|!\[[^\]\n]*\]\([^)\n]*\)", re.M)
# Only between words. Collapsing every run of spaces also collapsed the
# indentation of code, turning a correct Python function into one that does
# not parse — measured on all three coding questions of the evaluation.
_SPACE_RUN = re.compile(r"(?<=\S)[ \t]{2,}(?=\S)")
_FENCED = re.compile(r"```.*?(?:```|\Z)", re.S)
_HELD = re.compile(r"\x00CODE(\d+)\x00")
# Inline code, held aside as well. Measured: "`python -m venv .venv`" came out
# as "`python -m venv.venv`" — the space-before-punctuation rule, meant for the
# gap a removed marker leaves, joined a command to its argument. Anything in
# backticks is literal and is put back exactly as written.
_INLINE_CODE = re.compile(r"`[^`\n]+`")
_HELD_INLINE = re.compile(r"\x00IC(\d+)\x00")

# Lines that start a Python definition, and the lines that belong to it.
_CODE_START = re.compile(r"^(?:def |class |async def |import |from \S+ import |@\w)")
_CODE_BODY = re.compile(r"^(?:[ \t]+\S|\s*$|\)|\]|\})")


def fence_code(text: str) -> str:
    """Put unfenced Python into a fenced block.

    Asked for a function, a lane sometimes writes the code as bare lines
    under the prose. Rendered as Markdown that loses its indentation and
    cannot be copied into a file. Only done when the answer has no fences of
    its own, so a correctly formatted answer is never touched.
    """
    if "```" in text:
        return text
    lines = text.split("\n")
    out, i, changed = [], 0, False
    while i < len(lines):
        if _CODE_START.match(lines[i]):
            j = i + 1
            while j < len(lines) and (_CODE_BODY.match(lines[j]) or _CODE_START.match(lines[j])):
                j += 1
            while j > i + 1 and not lines[j - 1].strip():
                j -= 1
            block = lines[i:j]
            if len(block) >= 2 or block[0].startswith(("def ", "class ")):
                out += ["```python"] + block + ["```"]
                changed = True
                i = j
                continue
        out.append(lines[i])
        i += 1
    return "\n".join(out) if changed else text


_SPACE_BEFORE_PUNCT = re.compile(r"[ \t]+([,.;:!?])")

# A label in front of the answer. "Short answer:" announces brevity rather
# than being brief, and both frontier style guides ban it by name; the answer
# after it is kept word for word.
_ANSWER_LABEL = re.compile(
    r"\A\s*(?:\*\*|__)?(?:short answer|short version|quick answer|tl;?dr|in short|"
    r"bottom line)(?:\*\*|__)?\s*[:—–-]\s*(?:\*\*|__)?\s*", re.I)

# A closing offer of more help. The answer is the whole deliverable; related
# questions are already shown under it as buttons, so an offer in prose
# repeats them less usefully. Only a final paragraph made entirely of offers
# is removed, and never one that is most of the answer.
_OFFER = re.compile(
    r"^(?:if (?:you(?:'d| would)? (?:like|want|prefer)|it helps|useful|needed),?\s*"
    r"(?:i can|i could|i'?ll|i'?m happy to|happy to|let me know)|let me know if|"
    r"feel free to|(?:would|do) you (?:like|want) (?:me to|to (?:know|see|hear|explore|dive)|"
    r"more|a |an |further)|want me to|shall i|(?:is there )?anything else|"
    r"i can (?:also|then|next)|i(?:'m| am) happy to|happy to (?:help|go|dig|expand)|"
    r"hope (?:this|that) helps)\b",
    re.I)

# "claim [2]." reads as a footnote left hanging; "claim.[2]" is the form both
# frontier assistants use. The space before the bracket is required, so an
# index written in prose ("array[2].") is never moved.
# Some lanes space with U+202F or U+00A0. Measured: a ". [2]" written with a
# narrow no-break space slipped past a pattern that knew spaces and tabs.
_GAP = "[ \t\u00a0\u202f\u2009]"
_CITE_BEFORE_STOP = re.compile(
    r"(?<=[\w)%\"”’*_\x00])" + _GAP + r"+((?:\[\d{1,2}\])(?:" + _GAP + r"*\[\d{1,2}\])*)"
    + _GAP + r"*([.,;:!?])")
_CITE_ONLY_LINE = re.compile(r"^" + _GAP + r"*((?:\[\d{1,2}\]" + _GAP + r"*)+)$")
# "…core project. [1]" — the citation belongs against the stop it follows.
_CITE_AFTER_GAP = re.compile(r"([.!?])" + _GAP + r"+((?:\[\d{1,2}\])+)(?=\s|$)")
_ANY_CITE = re.compile(r"\[(\d{1,2})\]")

# "Based on the King's College London website content provided:" — the
# machinery words (content, provided, available) are what make it narration;
# "Based on the NVIDIA website, …" says where a fact came from and stays.
_VISIT_PREAMBLE = re.compile(
    r"\A\s*(?:based (?:on|upon)|according to|drawing on|from)\s+(?:the\s+)?"
    r"(?:[\w'’&.\-]+\s+){0,6}?(?:website|site|page|homepage|profile|pages)(?:'s)?\s+"
    r"(?:content|information|text|data)(?:\s+(?:provided|available|shown|above|supplied))?"
    r"\s*[,:;.\-—]+\s*", re.I)


def _one_source_once(body: str) -> tuple[str, int]:
    """One source cited on every line is cited once.

    A profile or a single page read is the whole of the evidence, and the
    lane marked every bullet with the same [1] — eight identical chips that
    say nothing the first did not. With two or more sources each citation
    distinguishes something, and all are kept.
    """
    numbers = _ANY_CITE.findall(body)
    if len(numbers) < 3 or len(set(numbers)) != 1:
        return body, 0
    first = [True]

    def keep_first(match: "re.Match[str]") -> str:
        if first[0]:
            first[0] = False
            return match.group(0)
        return ""
    return _ANY_CITE.sub(keep_first, body), len(numbers) - 1


def _tidy_citations(body: str) -> tuple[str, int]:
    """Citations after the punctuation they follow, and never on a line alone."""
    body, moved = _CITE_BEFORE_STOP.subn(
        lambda m: m.group(2) + re.sub(_GAP, "", m.group(1)), body)
    body, gap = _CITE_AFTER_GAP.subn(r"\1\2", body)
    moved += gap
    out: list[str] = []
    for line in body.split("\n"):
        alone = _CITE_ONLY_LINE.match(line)
        if alone:
            j = len(out) - 1
            while j >= 0 and not out[j].strip():
                j -= 1
            # Attached to the sentence above unless that is a heading, a table
            # row or code, where a citation would break the structure.
            if j >= 0 and not out[j].lstrip().startswith(("#", "|", ">")) \
                    and "\x00CODE" not in out[j]:
                out[j] = out[j].rstrip() + re.sub(r"\s+", "", alone.group(1))
                del out[j + 1:]
                moved += 1
                continue
        out.append(line)
    return "\n".join(out), moved


def _drop_closing_offer(body: str) -> tuple[str, bool]:
    paragraphs = body.rstrip().split("\n\n")
    if len(paragraphs) < 2:
        return body, False
    last = paragraphs[-1].strip()
    if _STRUCTURE.match(last) or "\x00CODE" in last or len(last) > 320:
        return body, False
    sentences = [s for s in _SENTENCE_END.split(last) if s.strip()]
    if not sentences or not all(_OFFER.match(s.strip().lstrip("*_ ")) for s in sentences):
        return body, False
    kept = "\n\n".join(paragraphs[:-1]).rstrip()
    # Never when the offer is most of the reply: what remains must at least
    # outweigh it.
    if len(kept) < max(60, len(last)):
        return body, False
    return kept, True


def clean(text: str) -> tuple[str, list[str]]:
    """Return the answer with plumbing removed, and what was removed.

    The list is for diagnostics: tool markup in a reply means a provider
    mangled the protocol, which is worth knowing about even though the user
    never sees it.
    """
    body = str(text or "")
    if not body:
        return "", []

    removed: list[str] = []

    fenced = fence_code(body)
    if fenced != body:
        body = fenced
        removed.append("fenced code that had no fences")

    # Code is held aside while the prose is cleaned, and put back unchanged.
    held: list[str] = []

    def _hold(match: "re.Match[str]") -> str:
        held.append(match.group(0))
        return f"\x00CODE{len(held) - 1}\x00"

    body = _FENCED.sub(_hold, body)
    inline: list[str] = []

    def _hold_inline(match: "re.Match[str]") -> str:
        inline.append(match.group(0))
        return f"\x00IC{len(inline) - 1}\x00"

    body = _INLINE_CODE.sub(_hold_inline, body)

    body, count = _NUMBERED_CITATION.subn(lambda m: f" [{int(m.group(1))}]", body)
    if count:
        removed.append(f"{count} numbered citation{'s' if count > 1 else ''} rewritten as [n]")

    for pattern in _CITATION_ARTEFACTS:
        body, count = pattern.subn("", body)
        if count:
            removed.append(f"{count} citation marker{'s' if count > 1 else ''}")

    body, count = _CONTROL_TOKENS.subn("", body)
    if count:
        removed.append(f"{count} chat-template token{'s' if count > 1 else ''}")

    body, count = _TOOL_MARKUP.subn("", body)
    if count:
        removed.append(f"{count} tool-protocol tag{'s' if count > 1 else ''}")

    body, count = _TOOL_NAME_PREFIX.subn("", body)
    if count:
        removed.append("a tool-name prefix")

    body, count = _IMAGE_MARKDOWN.subn("", body)
    if count:
        removed.append(f"{count} image link{'s' if count > 1 else ''} the model wrote")

    # Only if something substantial follows it, so an answer that is genuinely
    # nothing but the preamble is left alone for the check to catch.
    trimmed = _SEARCH_PREAMBLE.sub("", body, count=1)
    if trimmed == body:
        trimmed = _VISIT_PREAMBLE.sub("", body, count=1)
    if trimmed != body and len(trimmed.strip()) >= 24:
        body = trimmed[:1].upper() + trimmed[1:] if trimmed else trimmed
        removed.append("a search preamble")

    body, narration = drop_narration(body)
    removed.extend(narration)

    labelled = _ANSWER_LABEL.sub("", body, count=1)
    if labelled != body and len(labelled.strip()) >= 24:
        body = labelled[:1].upper() + labelled[1:]
        removed.append("an answer label")

    body, offered = _drop_closing_offer(body)
    if offered:
        removed.append("a closing offer")

    body, moved = _tidy_citations(body)
    if moved:
        removed.append(f"{moved} citation{'s' if moved > 1 else ''} moved after punctuation")
    body, repeats = _one_source_once(body)
    if repeats:
        removed.append(f"{repeats} repeats of the only source cited")

    # Removing an inline marker leaves the gap it sat in.
    body = _SPACE_BEFORE_PUNCT.sub(r"\1", body)
    body = _SPACE_RUN.sub(" ", body)
    body = _BLANK_RUN.sub("\n\n", body)
    body = _HELD_INLINE.sub(lambda m: inline[int(m.group(1))], body)
    body = _HELD.sub(lambda m: held[int(m.group(1))], body)
    return body.strip(), removed


def for_speech(text: str) -> str:
    """The same answer, written to be heard rather than read.

    Voice Mode and dictation replies go through this; a typed answer never
    does. Markdown read aloud is a list of asterisks, and a URL read aloud
    is thirty seconds of punctuation.
    """
    body = str(text or "")
    if not body:
        return ""

    body = re.sub(r"```[\w-]*\n.*?```", " (code omitted, it is on screen) ",
                  body, flags=re.S)
    body = re.sub(r"`([^`]+)`", r"\1", body)
    body = re.sub(r"!?\[([^\]]*)\]\((?:[^)]+)\)", r"\1", body)   # links → their text
    body = re.sub(r"https?://\S+", "the link on screen", body)
    body = re.sub(r"^\s{0,3}#{1,6}\s*", "", body, flags=re.M)     # headings
    body = re.sub(r"\*\*([^*]+)\*\*", r"\1", body)
    body = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", body)
    body = re.sub(r"^\s*[-*+]\s+", "", body, flags=re.M)          # bullets
    body = re.sub(r"^\s*\|.*\|\s*$", "", body, flags=re.M)        # table rows
    body = _BLANK_RUN.sub("\n\n", body)
    return body.strip()
