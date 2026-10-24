"""The form an answer was told to take, and whether it took it.

"Answer in one word", "put it in a table", "bullet points", "under fifty
words", "just the number" — an instruction about form outranks every default
about how an answer looks, and it is also the easiest thing to verify. So it
is read from the message here and checked against the reply in code; a reply
in the wrong form earns one repair round with the exact instruction, and a
reply in the right form is never touched.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

_ONE_WORD = re.compile(
    r"\b(?:(?:in|with|using|as) (?:just |only )?(?:one|a single|1) word|one[- ]word(?: answer| reply)?|"
    r"answer in a word|single[- ]word)\b", re.I)
_YES_NO = re.compile(r"\b(?:just|only|simply)\s+(?:say\s+)?(?:yes or no|yes/no|y/n)\b|\byes or no only\b", re.I)
_NUMBER_ONLY = re.compile(
    r"\b(?:just|only)\s+(?:the|a|give me the)?\s*(?:number|figure|answer|value)\b(?!\s+of)|"
    r"\b(?:number|figure) only\b", re.I)
_TABLE = re.compile(r"\b(?:(?:in|as|into|with) a table|tabulate|table format|tabular|make a table|in table form)\b", re.I)
_BULLETS = re.compile(r"\b(?:bullet(?:ed)?(?: points?| list)?|in bullets|as bullets|dot points)\b", re.I)
_NUMBERED = re.compile(r"\b(?:numbered list|numbered steps|as a numbered)\b", re.I)
_WORD_LIMIT = re.compile(
    r"\b(?:in|under|below|less than|fewer than|within|max(?:imum)?(?: of)?|no more than|at most)\s+"
    r"(\d{1,4})\s+words?\b", re.I)
_SENTENCE_LIMIT = re.compile(
    r"\b(?:in|under|within|no more than|at most|max(?:imum)?(?: of)?)\s+(one|two|three|a|1|2|3)\s+sentences?\b|"
    r"\b(?:one|a single)[- ]sentence (?:answer|reply|summary)\b", re.I)
_NO_IMAGES = re.compile(r"\b(?:no|without|don'?t (?:show|include|add))\s+(?:any\s+)?(?:images?|pictures?|photos?|pics?)\b", re.I)

_NUMBERS = {"one": 1, "a": 1, "two": 2, "three": 3}


@dataclass
class Form:
    one_word: bool = False
    yes_no: bool = False
    number_only: bool = False
    table: bool = False
    bullets: bool = False
    numbered: bool = False
    word_limit: int = 0
    sentence_limit: int = 0

    def __bool__(self) -> bool:
        return any((self.one_word, self.yes_no, self.number_only, self.table, self.bullets,
                    self.numbered, self.word_limit, self.sentence_limit))


def requested(question: str) -> Form:
    q = str(question or "")
    form = Form(
        one_word=bool(_ONE_WORD.search(q)),
        yes_no=bool(_YES_NO.search(q)),
        number_only=bool(_NUMBER_ONLY.search(q)),
        table=bool(_TABLE.search(q)),
        bullets=bool(_BULLETS.search(q)),
        numbered=bool(_NUMBERED.search(q)),
    )
    limit = _WORD_LIMIT.search(q)
    if limit:
        form.word_limit = int(limit.group(1))
    sentences = _SENTENCE_LIMIT.search(q)
    if sentences:
        word = (sentences.group(1) or "one").lower()
        form.sentence_limit = _NUMBERS.get(word, int(word) if word.isdigit() else 1)
    return form


def no_images(question: str) -> bool:
    return bool(_NO_IMAGES.search(str(question or "")))


def _plain(answer: str) -> str:
    text = re.sub(r"```.*?```", " ", answer or "", flags=re.S)
    text = re.sub(r"\[\d{1,2}\]", " ", text)
    return re.sub(r"[*_#>`|]", " ", text)


def _words(answer: str) -> List[str]:
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9'’.,%£$€-]*", _plain(answer))


def problems(question: str, answer: str) -> List[str]:
    """What is wrong with the reply's form, as instructions to fix it."""
    form = requested(question)
    if not form:
        return []
    out: List[str] = []
    words = _words(answer)
    if form.one_word and len(words) > 3:
        out.append("One word was asked for. Reply with that single word and nothing "
                   "else — no sentence, no explanation, no sources.")
    if form.yes_no and not re.match(r"^\W*(?:yes|no)\b", answer or "", re.I):
        out.append("Only yes or no was asked for, and the reply does not start with one. "
                   "Start it with Yes or No.")
    if form.number_only and (len(words) > 6 or not re.search(r"\d", answer or "")):
        out.append("Only the number was asked for. Reply with the number (and its unit, "
                   "if it has one) and nothing else.")
    if form.table and not re.search(r"^\s*\|?\s*:?-{3,}", answer or "", re.M):
        out.append("A table was asked for. Put the answer in a Markdown table with a "
                   "header row.")
    if form.bullets and not re.search(r"^\s*[-*•]\s+\S", answer or "", re.M):
        out.append("Bullet points were asked for. Give the answer as a bulleted list.")
    if form.numbered and not re.search(r"^\s*\d+[.)]\s+\S", answer or "", re.M):
        out.append("A numbered list was asked for. Give the answer as a numbered list.")
    if form.word_limit and len(words) > int(form.word_limit * 1.1) + 2:
        out.append(f"It must be at most {form.word_limit} words; it is {len(words)}. "
                   "Cut it to fit, keeping the substance.")
    if form.sentence_limit:
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", _plain(answer).strip()) if s.strip()]
        if len(sentences) > form.sentence_limit:
            out.append(f"It must be {form.sentence_limit} sentence"
                       f"{'s' if form.sentence_limit > 1 else ''} at most; it is "
                       f"{len(sentences)}. Rewrite it to fit.")
    return out


def brief(question: str) -> Optional[str]:
    """A one-line system reminder of the requested form, or None."""
    form = requested(question)
    if not form:
        return None
    parts = []
    if form.one_word:
        parts.append("reply with one word only")
    if form.yes_no:
        parts.append("start with Yes or No")
    if form.number_only:
        parts.append("reply with the number only")
    if form.table:
        parts.append("use a Markdown table")
    if form.bullets:
        parts.append("use bullet points")
    if form.numbered:
        parts.append("use a numbered list")
    if form.word_limit:
        parts.append(f"stay under {form.word_limit} words")
    if form.sentence_limit:
        parts.append(f"use at most {form.sentence_limit} sentence{'s' if form.sentence_limit > 1 else ''}")
    return ("The user set the form of this answer: " + "; ".join(parts) +
            ". That overrides every default about length and layout. Add nothing else.")
