"""Who made SYRIX: one fixed answer, whatever the language or wording.

The creator is a fact, not something to look up or infer, and two parts of
the turn kept getting it wrong:

* A short follow-up is rewritten against the thread before triage reads it.
  After a conversation about AI assistants, "who made you" became a question
  about ChatGPT, the self-question exemption no longer matched, the web was
  searched, and the answer credited OpenAI — with citations.
* Several free lanes run models trained by other companies. Without the fact
  in front of them they describe their own origin, and a phrasing the triage
  patterns did not cover ("which company built you", any non-English
  wording) went to the web and came back with whatever the results said.

So a creator question is recognised on the raw message — in many languages,
in any word order, with common typos — kept away from the rewrite and the
web, given the fact as evidence, streamed nowhere, and the finished answer is
checked. An answer that does not name the creator, or credits a company, is
replaced with the fixed reply in the question's language.

This is the one answer v3 corrects after the model rather than asking the
model to repair (see check.py): the fact has exactly one correct value, so a
repair round could only reproduce it or get it wrong again.

Outside creator questions, a first-person claim to have been made by another
company ("I was created by OpenAI") is replaced by the identity sentence,
because a lane can volunteer its origin in the middle of an unrelated answer.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List, Optional, Sequence, Tuple

from .creator_languages import HANT, KO_NOISE, LANGS, REPLIES, ZH_PARTS, Lang
from .persona import CREATOR, NAME

# ── Normalising ───────────────────────────────────────────────────────────────

# Split on whitespace and punctuation only. Word-character classes drop the
# combining vowel signs of Indic scripts, which would cut "किसने" in two.
_SPLIT = re.compile(
    r"[\s.,!?;:¿¡\"'`“”‘’«»()\[\]{}<>/\\|،؟。！？、，；：…*_~#@+=\-]+")
_RUN = re.compile(r"([a-z])\1{2,}")


def _normalise(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).lower()


def _tokens(text: str) -> List[str]:
    # "whooo madeee youuu" is the same question; a run of three or more of one
    # Latin letter collapses to one.
    return [_RUN.sub(r"\1", w) for w in _SPLIT.split(text) if w]


def _has(tokens: Sequence[str], words: Iterable[str]) -> List[int]:
    """Positions of tokens in ``words``. An entry ending in * is a prefix."""
    exact = {w for w in words if not w.endswith("*")}
    prefixes = tuple(w[:-1] for w in words if w.endswith("*"))
    return [i for i, t in enumerate(tokens)
            if t in exact or (prefixes and t.startswith(prefixes))]


# ── Languages other than English ─────────────────────────────────────────────


def _match_lang(lang: Lang, text: str, tokens: Sequence[str]) -> bool:
    if lang.patterns:
        body = text.replace(" ", "")
        return any(re.search(p.format(**ZH_PARTS), body) for p in lang.patterns)
    if lang.unspaced:
        body = text.replace(" ", "")
        if lang.code == "ko":
            body = KO_NOISE.sub("", body)
        present = lambda words: any(w in body for w in words)  # noqa: E731
        if lang.joined and present(lang.joined):
            return True
        if present(lang.you) and present(lang.who) and (present(lang.made) or present(lang.maker)):
            return True
        return bool(lang.your) and present(lang.your) and present(lang.maker)

    if lang.joined and _has(tokens, lang.joined):
        return True
    if len(tokens) <= 14 and _has(tokens, lang.who) and _has(tokens, lang.you) \
            and _has(tokens, lang.made):
        return True
    yours, makers = _has(tokens, lang.your), _has(tokens, lang.maker)
    return any(abs(a - b) <= 2 for a in yours for b in makers)


# ── English ─────────────────────────────────────────────────────────────────
#
# English gets word order, because it is the language where "you" is as often
# the subject as the object: "who made you" asks about SYRIX, "can you build
# an openai agent" does not.

_WHO = frozenset("who whom whose whos hwo woh".split())
_ORG = frozenset("company companies team firm lab labs organisation organization org "
                 "person people guy guys group business corp corporation startup "
                 "country individual human".split())
_YOU = frozenset("you u ya yu yuo yourself urself syrix thee".split())
_YOUR = frozenset("your ur yur yor".split())
# Past or third-person forms: "who made you". The bare form only counts after
# did/does/do, so "who can make you smarter" is left alone.
_MADE_PAST = ("made makes created creates built builds developed develops designed "
              "programmed coded trained invented engineered wrote written founded owns "
              "owned produced manufactured crafted").split()
_MADE_BASE = frozenset("make create build develop design program code train invent own "
                       "produce write".split())
_AUX_DO = frozenset("did does do".split())
_PASSIVE = frozenset("were was are r is been be got get being".split())
_MAKERS = ("creator creators maker makers developer developers dev devs author authors "
           "inventor founder founders owner owners programmer programmers engineer engineers "
           "builder builders manufacturer father dad daddy parent parents boss designer "
           "designers coder trainer").split()
_SELF_PARTS = frozenset("code source software program brain system model engine core ai "
                        "mind".split())
# "who made you cry", "who made you say that" ask about a feeling or an act.
_NOT_ORIGIN = frozenset("cry laugh smile sad mad angry upset happy think say said do feel "
                        "like love hate believe change stop wait sound act forget remember "
                        "answer reply respond break crash fail slow lag so this that want "
                        "choose pick decide go come look seem write use search realise realize "
                        "notice get give pay work smarter better worse feel".split())
_COMPANIES = frozenset("openai chatgpt gpt google deepmind gemini meta facebook llama "
                       "anthropic claude microsoft copilot mistral deepseek alibaba qwen xai "
                       "grok nvidia apple amazon ibm groq cohere baidu tencent moonshot kimi "
                       "zhipu glm perplexity huggingface".split())
_CREATOR_NAMES = frozenset("kabirr kabir kohli".split())


def _near(word: str, targets: Sequence[str], sub_min: int = 6) -> bool:
    """One slip of the finger: a swap anywhere, or — past four letters — one
    letter added, dropped or changed ("mdae", "craeted", "bulit", "devloped")."""
    for t in targets:
        if word == t:
            return True
        if len(word) == len(t) and len(t) >= 4:
            diff = [i for i in range(len(t)) if word[i] != t[i]]
            if len(diff) == 2 and diff[1] == diff[0] + 1 \
                    and word[diff[0]] == t[diff[1]] and word[diff[1]] == t[diff[0]]:
                return True
            if len(diff) == 1 and len(t) >= sub_min:
                return True
        elif abs(len(word) - len(t)) == 1 and min(len(word), len(t)) >= 5:
            longer, shorter = (word, t) if len(word) > len(t) else (t, word)
            if any(longer[:i] + longer[i + 1:] == shorter for i in range(len(longer))):
                return True
    return False


def _english(tokens: Sequence[str]) -> bool:
    n = len(tokens)
    if not n:
        return False
    who = [i for i, t in enumerate(tokens) if t in _WHO]
    asks_who = bool(who) or (("what" in tokens or "which" in tokens)
                             and any(t in _ORG for t in tokens))
    you = [i for i, t in enumerate(tokens) if t in _YOU]
    your = [i for i, t in enumerate(tokens) if t in _YOUR]
    past = [i for i, t in enumerate(tokens) if _near(t, _MADE_PAST)]
    base = [i for i, t in enumerate(tokens) if t in _MADE_BASE]
    names = (any(t in _COMPANIES or t in _CREATOR_NAMES for t in tokens)
             or ("open" in tokens and "ai" in tokens))
    by_whom = any(tokens[i] == "by" and i + 1 < n and tokens[i + 1] in ("who", "whom")
                  for i in range(n))

    # Your maker: "who is ur real creator", "creator of you", "syrix's
    # developer". Not "are you a developer", which asks what SYRIX is.
    makers = [i for i, t in enumerate(tokens) if _near(t, _MAKERS, sub_min=7)]
    for m in makers:
        if any(0 < m - y <= 3 for y in your):
            return True
        if any((y == m + 2 and tokens[m + 1] == "of")
               or (0 < m - y <= 2 and tokens[y] == "syrix") for y in you):
            return True

    anchored = asks_who or names or by_whom
    # A very short message is a jumble of the three words: "made you who".
    if n <= 4 and who and you and (past or base) \
            and not any(t in _NOT_ORIGIN for t in tokens):
        return True
    for v in past + [b for b in base if b > 0 and tokens[b - 1] in _AUX_DO]:
        for y in you:
            after = tokens[y + 1] if y + 1 < n else ""
            # Object: "who made you", "did openai build you".
            if 1 <= y - v <= 2 and anchored and after not in _NOT_ORIGIN:
                return True
            # Passive: "you were made by who", "were you trained by google".
            if 1 <= v - y <= 3 and anchored and v in past and (
                    any(tokens[k] in _PASSIVE for k in range(y + 1, v))
                    or (v + 1 < n and tokens[v + 1] == "by")):
                return True
        # "who wrote your code", "who built your brain".
        if asks_who and any(0 < y - v <= 2 and y + 1 < n and tokens[y + 1] in _SELF_PARTS
                            for y in your):
            return True
    for y in you:
        window = tokens[max(0, y - 2): y + 4]
        # "who is behind you", "who do you belong to".
        if who and ("behind" in window or any(t.startswith("belong") for t in window)):
            return True
        # "are you from openai", "r u chatgpt", "you're an openai model".
        if (y > 0 and tokens[y - 1] in ("are", "r", "were")) or (
                y + 1 < n and tokens[y + 1] in ("re", "are", "r")):
            rest = tokens[y + 1: y + 5]
            if rest[:1] and rest[0] in ("re", "are", "r"):
                rest = rest[1:]
            if rest[:1] and rest[0] in ("from", "by", "a", "an", "the"):
                rest = rest[1:]
            if rest[:1] and (rest[0] in _COMPANIES or rest[:2] == ["open", "ai"]):
                return True
    return False


# ── Public ──────────────────────────────────────────────────────────────────

_MAX_WORDS = 40
_MAX_CHARS = 300


def asks(text: str) -> Optional[str]:
    """The question's language code if it asks who made SYRIX, else None."""
    norm = _normalise(text)
    tokens = _tokens(norm)
    # The question is a line, not a document. A pasted cover letter holds
    # "you", "develop" and "build" and is not asking who made anyone.
    if not tokens or len(tokens) > _MAX_WORDS or len(norm) > _MAX_CHARS:
        return None
    if _english(tokens):
        return "en"
    for lang in LANGS:
        if _match_lang(lang, norm, tokens):
            if lang.code == "zh" and HANT.search(norm):
                return "zh_hant"
            return lang.code
    return None


def note() -> str:
    """The fact, as evidence for a creator question."""
    # Worded about SYRIX in the third person and asking for a first-person
    # answer. Written as "you were built by", the note was copied into the
    # reply word for word, and SYRIX told the person asking that THEY had been
    # built by its creator.
    return (
        f"WHO MADE YOU — a fixed fact, not something to look up or reason about: "
        f"{NAME} was built by {CREATOR}. Answer as {NAME}, in the first person "
        f"(\"{CREATOR} built me\"); the person asking is not {NAME}, so never "
        f"\"you were built\". Say it the way you would about someone you are proud "
        f"of, not as a bare name: he designed and built {NAME} himself, and a "
        f"sentence of warmth about that belongs in the answer. "
        f"Keep it short. Answer in the language of the question and write "
        f"the name exactly as \"{CREATOR}\" in Latin letters. Never say or suggest "
        f"that a company or model provider — OpenAI, Google, Meta, Anthropic or any "
        f"other — made, trained or owns you, and cite nothing."
    )


def reply(lang: Optional[str]) -> str:
    """The fixed answer, in the question's language where there is one."""
    return REPLIES.get(lang or "en", REPLIES["en"]).format(c=CREATOR, n=NAME)


# ── Checking the answer ─────────────────────────────────────────────────────

# Latin-script names only, bounded by non-letters rather than \b: in
# "我是由OpenAI开发的" the CJK characters count as word characters, so a \b
# boundary never forms and the name would slip through.
_OTHER_MAKER = re.compile(
    r"(?<![A-Za-z0-9])(?:open\s?ai|chat\s?gpt|gpt(?:[-\s]?\d\w*|-oss)?|google|deepmind|"
    r"gemini|meta(?:\s+ai)?|facebook|llama|anthropic|claude|microsoft|copilot|mistral|"
    r"deepseek|alibaba|qwen|x\.?ai|grok|nvidia|nemotron|groq|cohere|baidu|ernie|tencent|"
    r"moonshot|kimi|zhipu|glm|perplexity|hugging\s?face)(?![A-Za-z0-9])",
    re.I,
)
# The creator's name as a lane misspells it: "Kabir Kohli", "Kabbir Kohly".
_NAME_SLIP = re.compile(r"(?<![A-Za-z])Kab+i+r+\s+Koh+l(?:i|y|ee)(?![A-Za-z])", re.I)
# "I'm not ChatGPT", "I'm faster than ChatGPT" and "I was able to find that
# the Pixel was designed by Google" all name a maker, and none of them is a
# claim about SYRIX's own origin.
_NOT_A_CLAIM = re.compile(
    r"(?:\b(?:not|never|no|nor|neither|than|like|unlike|similar|compared|versus|vs|"
    r"alternative|rival|competitor|different|that|which|who|where|because|while|able|"
    r"found|find|read|reading|looking|saw|see|think)\b|n't|n’t)", re.I)
_COMPANY = _OTHER_MAKER.pattern
# First-person origin claims. Each match is checked for a negation before it
# counts: "I'm not ChatGPT" and "I wasn't built by OpenAI" are correct.
_CLAIMS = (
    re.compile(r"\bI(?:'m|’m| am| was| have been|'ve been|’ve been)\b[^.!?\n]{0,60}?"
               r"\b(?:made|created|built|developed|trained|designed|programmed|engineered|"
               r"produced)\b[^.!?\n]{0,20}?\bby\s+(?:the\s+)?(?:\w+\s+(?:at|of)\s+)?"
               + _COMPANY, re.I),
    re.compile(_COMPANY + r"(?:'s|’s)?(?:\s+\w+){0,3}?\s+(?:made|created|built|developed|"
               r"trained|designed|programmed)\s+me(?=\s*(?:[.!?,;:]|$|\s+(?:and|but|as|"
               r"from|in|on|to|for|with)\b))", re.I),
    re.compile(r"\bmy\s+(?:creators?|makers?|developers?|owners?|parent company)\s+"
               r"(?:is|are|was|were)\s+(?:\w+\s+){0,2}?" + _COMPANY, re.I),
    re.compile(r"\bI(?:'m|’m| am)\s+(?:\w+\s+){0,3}?(?:ChatGPT|GPT-?\d\w*|Gemini|Claude|"
               r"Llama|Qwen|DeepSeek|Mistral|Grok|Copilot)\b", re.I),
)
_SENTENCE_END = re.compile(r"[.!?。！？\n]")
_BRAND_SLIP = re.compile(r"(?<![A-Za-z])Kab+i+r(?!r)\s+Education\s+Partners\b", re.I)
# An English creator answer addressed to the reader as if THEY were SYRIX:
# "You were built by Kabirr Kohli — he designed and crafted you himself."
_SECOND_PERSON = re.compile(
    r"\byou\s+(?:were|was|are|have\s+been)\s+(?:\w+\s+){0,2}?(?:made|built|created|designed|"
    r"developed|crafted|programmed|engineered)\b|\b(?:built|made|created|designed|crafted|"
    r"developed|programmed)\s+you\b", re.I)


def _claims(text: str) -> List[Tuple[int, int]]:
    spans = []
    for pattern in _CLAIMS:
        for m in pattern.finditer(text):
            if not _NOT_A_CLAIM.search(m.group(0)):
                spans.append((m.start(), m.end()))
    return spans


def _sentence(text: str, start: int, end: int) -> Tuple[int, int]:
    left = max((m.end() for m in _SENTENCE_END.finditer(text, 0, start)), default=0)
    right = _SENTENCE_END.search(text, end)
    return left, (right.end() if right else len(text))


def enforce(question: str, answer: str, lang: Optional[str] = None) -> Tuple[str, str]:
    """The answer with its account of SYRIX's origin made correct.

    Returns the answer and a short note of what changed, empty when nothing
    did. ``lang`` is ``asks(question)`` when the caller already has it.
    """
    text = str(answer or "")
    if lang is None:
        lang = asks(question)
    if lang:
        fixed = _NAME_SLIP.sub(CREATOR, text)
        key = lambda m: re.sub(r"\s", "", m.group(0).lower())  # noqa: E731
        asked_about = {key(m) for m in _OTHER_MAKER.finditer(str(question or ""))}
        others = [m.group(0) for m in _OTHER_MAKER.finditer(fixed) if key(m) not in asked_about]
        if (CREATOR.lower() in fixed.lower() and not others and not _claims(fixed)
                and not _SECOND_PERSON.search(fixed)):
            return fixed, ("creator name spelled correctly" if fixed != text else "")
        return reply(lang), "creator answer replaced with the fixed reply"

    # The owner's app, as lanes misspell it ("Kabir Education Partners").
    branded = _BRAND_SLIP.sub("Kabirr Education Partners", text)
    spans = _claims(branded)
    if not spans:
        return branded, ("ecosystem name spelled correctly" if branded != text else "")
    text = branded
    identity = f"I'm {NAME}, created by {CREATOR}."
    out, cursor, replaced = [], 0, False
    for start, end in sorted(_sentence(text, s, e) for s, e in spans):
        if start < cursor:
            continue
        lead = text[start:end][: len(text[start:end]) - len(text[start:end].lstrip())]
        out.append(text[cursor:start] + lead + ("" if replaced else identity + " "))
        replaced = True
        cursor = end
    out.append(text[cursor:])
    return "".join(out).strip(), "a claim to have been made by another company was corrected"
