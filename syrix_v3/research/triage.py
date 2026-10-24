"""Does this question need evidence, and what should be searched for?

Decided in code, with no model call, because the decision has to happen at
t=0 — before the first round — for gathering to overlap anything at all. A
model call to decide whether to gather costs as much as the gathering.

**The default is to gather.** This started as the opposite, listing the
cases that obviously needed evidence, and it failed the way a keyword list
always fails: asked to compare two AI models it had never heard of, SYRIX
matched no keyword, searched nothing, and invented the entire comparison —
parameter counts, context windows, per-token pricing, a lineage. Every
figure was wrong and every figure was stated plainly.

A model cannot detect that it is guessing, so the decision cannot be left to
whether the question *looks* current. The exemptions below are the cases
where an answer genuinely cannot be improved by looking: a greeting,
arithmetic, something about the user, something about SYRIX, and a request
to write code. Everything else is worth a few seconds of parallel
gathering, because the alternative is confident invention.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

from .. import creator
from . import social as _social

# Anything that pins the answer to a moment.
_TIME_WORDS = re.compile(
    r"\b("
    r"now|today|tonight|currently|current|latest|newest|recent|recently|"
    r"this (?:week|month|year|morning|afternoon|evening)|"
    r"right now|at the moment|so far|up to date|as of|"
    r"yesterday|tomorrow|upcoming|just (?:announced|released|happened)|"
    r"still|any(?:more| longer)|these days|nowadays|20\d\d"
    r")\b",
    re.I,
)

# Subjects whose answer moves even when the question does not say so.
_VOLATILE = re.compile(
    r"\b("
    r"price|prices|pricing|trading|worth|cost|quote|stock|stocks|shares?|"
    r"market cap|ticker|earnings|dividend|yield|rate|rates|inflation|"
    r"weather|forecast|temperature|rain|snow|wind|"
    r"score|scores|fixture|fixtures|results?|standings?|league|match|game|"
    # "the biggest technology stories today" matched only the time word, so it
    # was ranked at thirty days' recency and answered from Wikipedia pages
    # fetched weeks earlier, plus a film release. A roundup is the most
    # perishable thing there is.
    r"news|headlines?|stor(?:y|ies)|happening|going on|"
    r"announced|released|launch(?:ed|ing)?|"
    r"who is the (?:ceo|president|prime minister|chairman|manager|head)|"
    r"version|release|update|patch|"
    r"election|poll|polls|"
    r"available|in stock|status|outage|down"
    r")\b",
    re.I,
)

# Questions that answer themselves from what the model already knows.
_SELF_CONTAINED = re.compile(
    r"^\s*("
    r"hi|hey|hello|yo|thanks|thank you|cheers|ok|okay|sup|morning|evening"
    r")\b",
    re.I,
)

_ARITHMETIC = re.compile(r"^[^a-zA-Z]*[\d().,+\-*/^%\s]+[^a-zA-Z]*$")
_MATH_PHRASING = re.compile(
    r"\b(compounded|percent of|square root|factorial|convert|how many "
    r"(?:seconds|minutes|hours|days|weeks|grams|metres|meters|miles|km))\b",
    re.I,
)

# Recalling what was already said or stored, which no search can supply.
#
# Narrowed deliberately. This used to exempt any question containing "my ",
# "i am" or "i've", which reads a possessive as a subject: "can u see my
# portfolio and tell me what unis i can get in" was exempted from gathering
# on the strength of the word "my" and then answered with generic advice
# about how to assess university chances. Owning the thing a question is
# about does not make the answer local — the universities are still out in
# the world. Only the phrasings that genuinely point at stored context
# belong here.
_PERSONAL = re.compile(
    r"\b(?:remember(?: that| when| what| this)?|remind me|we agreed|"
    r"you said|i (?:told|asked) you|earlier you|last time|"
    r"this (?:chat|conversation)|what (?:did|have) (?:we|i)|"
    r"my (?:name|birthday|address|preferences?|settings?))\b",
    re.I,
)

# SYRIX's own identity, runtime, abilities and behaviour. Answered from the
# identity registry, the runtime_status tool and its own source — never from
# the web.
#
# This was five phrasings and it let the worst class of failure in the
# session through. Asked whether it was running on core v3, asked why it had
# not answered something on the first attempt, and told that an assistant
# which cannot search the web is pointless, SYRIX searched the WEB for each
# one: it answered a question about its own runtime with a list of articles
# about AI safety discourse, and a question about its own previous turn with
# citations about search-engine visibility. Nothing on the open web knows
# anything about this machine, so retrieval cannot improve one of these
# answers and can only replace it. The turn ends up being about the state of
# AI commentary rather than about SYRIX.
_ABOUT_SYRIX = re.compile(
    r"(?:"
    r"who (?:made|built|created|wrote) (?:you|u)|"
    r"what (?:model|brain|engine|core|version|runtime|architecture) "
    r"(?:are|is) (?:you|u)|"
    r"\b(?:are|r) (?:you|u) (?:running|built|based) on|"
    r"\b(?:are|r) (?:you|u) (?:jarvis|chatgpt|claude|gpt|gemini)|"
    r"what (?:can|could) (?:you|u) do|"
    # Asked what it is capable of. Every one of these searched the web and
    # answered from whatever came back: "are you able to browse the internet"
    # returned browser-troubleshooting articles and SYRIX advised THE USER to check
    # the user's browser settings. Nothing on the web knows what this build can do.
    r"\b(?:can|could) (?:you|u) (?:actually |even |really )?"
    r"(?:search|browse|access|read|see|look|remember|calculate|"
    r"compute|open|view|analyse|analyze|do maths|do math)\b|"
    r"\b(?:are|r) (?:you|u) able to\b|"
    r"\bdo (?:you|u) have (?:live |real[- ]?time |direct |any )?"
    r"(?:internet|web|online|network|browsing|memory|access)\b|"
    r"\b(?:you|u) (?:cant|can't|cannot|dont|don't|do not) "
    r"(?:have |get )?(?:internet|web|access|search|browse|remember)\b|"
    r"\b(?:your|ur) (?:own )?(?:name|creator|identity|runtime|code|source|"
    r"architecture|capabilit|limit|context|token|window|rules|prompt|"
    r"training|problem|dream|feeling|flaw|engine|core|version|brain)|"
    r"(?:tell|explain|say) (?:me|us) about (?:yourself|urself|you|ur)\b|"
    r"about (?:yourself|urself)\b|"
    # How it is built. "What are the core technologies behind your AI model?"
    # went to the web, which found syrix.io — an unrelated company with the
    # same name — and SYRIX described that company's product as itself: layer
    # counts, a vector database, a cloud host, all invented. The possessive
    # nouns are ones only an assistant has; "how were you trained" is past
    # tense about the assistant, unlike "how do you train a model".
    r"\b(?:your|ur) (?:own )?(?:ai |language |underlying )?(?:model|llm|tech|technolog|stack|"
    r"framework|weights|parameters|dataset|training data)|"
    r"\b(?:technolog\w*|tech(?: stack)?|stack|models?|llms?|ai|brain)(?: is| are)? "
    r"(?:behind|powering|powers|power|runs|running) (?:you|u)\b|"
    r"\bhow (?:were|was|are) (?:you|u) (?:made|built|trained|created|designed|developed|coded|programmed)\b|"
    r"\bwhat (?:llm|ai|language model|tech(?: stack)?|technology|stack|framework|models?) "
    r"(?:do|are|did) (?:you|u) (?:use|using|run|running|built|made)\b|"
    # Asking about SYRIX's own conduct, not the impersonal "you". The verb is
    # what separates them: "why could not u ANSWER this the first try" is
    # about SYRIX, while "how do you MAKE sourdough" and "how do you INSTALL
    # the latest cuda toolkit" are ordinary questions that happen to use the
    # generic pronoun — and the second of those needs current documentation,
    # so exempting it would be the original defect pointing the other way.
    r"\b(?:why|how) (?:did|do|does|could|can|cant|can't|couldn't|"
    r"dont|don't|wont|won't)(?: not)? (?:you|u) (?:not )?"
    r"(?:answer|say|said|reply|respond|search|know|give|fail|refuse|"
    r"forget|ignore|miss|choose|pick|decide|behave|work)\b|"
    # Being told what it cannot do. Restricted to the verbs that describe an
    # assistant's own capabilities: "you can't fix a leaking tap without a
    # wrench" is a claim about plumbing, not about SYRIX.
    r"\b(?:you|u) (?:cant|can't|cannot|dont|don't|do not|are not|aren't)\b"
    r"[^.?!]{0,40}\b(?:search|answer|access|edit|change|remember|"
    r"understand)\b|"
    r"\b(?:shut|close|turn off|switch off) (?:yourself|urself)\b|"
    r"\b(?:edit|change|fix|improve|rebuild) (?:yourself|urself)\b|"
    # Asking about its own working. Measured live: "if you could change one
    # thing about how you work, what would it be" was searched for, and
    # answered out of a newspaper column about keeping a gratitude journal.
    r"\bhow (?:you|u) (?:work|operate|run|decide|reason)\b|"
    r"\bhow (?:you|u)(?:'re| are) (?:built|made|wired|set up)\b|"
    r"\bchange (?:one thing|anything|something)?\s*about (?:how )?(?:you|ur|your)\b"
    r")",
    re.I,
)

# The user is pointing at something of the user's, so this is an instruction with an object
# rather than a question about a faculty.
#
# "Can you read a PDF" asks what SYRIX is and needs no search. "Can you read MY
# CV and tell me what's weak" asks it to do the thing, and the answer may well
# need the outside world — that one was being exempted from gathering on the
# strength of "can you read" and answered with generic advice. Kept as its own
# check rather than a lookahead inside the pattern above, because an optional
# group like `look(?: at)?` backtracks to the shorter form and slips past.
# Deliberately NOT a list of nouns. The first version named the things the user might
# point at — cv, portfolio, screenshot — and then failed on a misspelt
# "portfolio", because a closed vocabulary cannot survive a typo and typed
# messages are full of them. Any possessive followed by a word is the user pointing at
# something; the exclusions are the handful of abstract nouns that follow a
# possessive without naming an object, and those are handled below as talk about
# the conversation itself.
# Telling SYRIX what it cannot do, and inviting agreement. A premise, not a
# request — and a false one, which is why it is separated from the object guard
# below rather than being caught by it.
_DENIED_CAPABILITY = re.compile(
    r"\b(?:you|u|ur|you're|youre)\s+"
    r"(?:can'?t|cant|cannot|do\s?n[o']?t|dont|don't|are\s+not\s+able\s+to|"
    r"aren'?t\s+able\s+to|have\s+no|has\s+no)\b"
    r"[^.?!]{0,60}"
    r"\b(?:read|see|access|open|search|browse|look|remember|recall|do|"
    r"calculate|compute|know|understand|internet|web|files?|documents?|"
    r"attachments?|images?|maths?|math|memory)\b"
    r"|\bsince (?:you|u) (?:can'?t|cant|cannot|do\s?n'?t|dont)\b",
    re.I,
)

_POINTS_AT_A_THING = re.compile(
    r"\b(?:my|this|that|these|those|the attached|the uploaded)\s+"
    # A determiner or an abstract noun after the possessive means the user is not
    # naming an object: "why could not u answer THIS THE first try" and "fix
    # THESE THINGS" are both about SYRIX's own conduct, and both were caught by
    # an earlier version of this pattern.
    r"(?!(?:the|a|an|this|that|it|one)\b"
    r"|chats?\b|conversations?\b|threads?\b|messages?\b|questions?\b"
    r"|points?\b|things?\b|stuff\b|ways?\b|minds?\b|times?\b|tries\b|try\b"
    r"|issues?\b|problems?\b|bugs?\b)"
    r"[A-Za-z]{2,}\b",
    re.I,
)

# Material the user pasted INTO the message. The subject is in front of you.
#
# Measured live: the user pasted a 1,900-character cover letter and asked whether it
# was any good. Triage searched the web for the entire letter as a single query,
# got SEO articles about how to write cover letters, and answered "according to
# the sources I've read, a strong cover letter in 2026 should be well-written
# and tailored" — generic advice, about nothing the user had written. A block of text
# the user supplied is evidence, not a query; no index can return it and nothing
# outside the message is a better source for judging it.
#
# Length is the signal because it is the reliable one. A question about the
# world is short even when it is hard: "how did the last two fed decisions
# change the outlook for uk mortgage rates" is seventy-six characters. Past a
# few hundred, the bulk of a message is something the user is showing you.
_PASTED_CHARS = 400


def _looks_pasted(text: str) -> bool:
    """Is most of this message material the user is showing, rather than a question?"""
    if len(text) < _PASTED_CHARS:
        return False
    # Several sentences, or the hard line breaks of a letter, a log or code.
    sentences = len(re.findall(r"[.!?:;]\s", text))
    return sentences >= 3 or "\n" in text


# Asking outright for something to be looked up. This is the only thing that
# sends a pasted-content turn to the web.
#
# The test used to be the ordinary time-and-volatile check, run over the whole
# message — which reads the pasted material as though it were the request. The user's
# cover letter contained the words "Final version" and "I am currently
# completing", so "version" and "currently" matched and the letter was searched
# for anyway. Words inside something the user is showing you are not an instruction
# about it.
_ASKS_TO_LOOK = re.compile(
    r"\b(?:search|look (?:it |this |that )?up|google|"
    r"check (?:online|the web|current|the latest)|"
    r"find (?:me )?(?:the )?(?:latest|current|recent)|"
    r"compare (?:it |this |these |mine )?(?:to|with|against) "
    r"(?:the )?(?:current|latest|market|real|other)|"
    r"what(?:'s| is| are) (?:the )?(?:latest|current)|"
    r"(?:any|the) (?:latest|current|recent) (?:news|guidance|rules|data))\b",
    re.I,
)

# A message that leans on the conversation instead of standing alone.
#
# Measured live: a follow-up asking for an opinion on "him" — the applicant
# whose cover letter had been pasted one turn earlier — was sent to the search
# engine verbatim and answered about an unrelated musician of the same name.
# The clarifying follow-up ("I mean the cover letter you just read") searched
# again and came back with poetry and song lyrics. Nothing on the open web knows
# what was said in this thread, so a search cannot improve one of these answers;
# it can only replace it with something about a stranger.
_LEANS_ON_THE_THREAD = re.compile(
    r"\b(?:i mean|i meant|like i said|as i said|i just (?:said|sent|gave|"
    r"showed|pasted)|(?:you|u) just (?:read|said|saw|gave|wrote)|"
    r"read (?:this|the) (?:chat|thread|conversation)|"
    r"(?:the|that) (?:one )?(?:i|you|u) (?:just )?"
    r"(?:sent|said|wrote|read|showed|mentioned)|"
    r"with the info (?:you|u) (?:have |got )|"
    r"based on (?:what|the) (?:i|you|u|above)|"
    r"(?:what|how) (?:do|d) (?:you|u) (?:thing|think) about "
    r"(?:him|her|them|it|that|this)\b|"
    r"about (?:him|her|them)\b|"
    r"(?:he|she|they) will get in|"
    r"scroll (?:up|back)|earlier in (?:this|the) chat)\b",
    re.I,
)

# Writing code is production, not retrieval. A request that also names a
# library version or a current API falls through to the volatile check.
_WRITE_CODE = re.compile(
    r"\b(write|create|make|build|generate|give me|code|script|function)\b"
    r"[^?]{0,80}\b(code|script|function|class|html|css|js|javascript|python|"
    r"sql|regex|program|file|snippet|app|page)\b",
    re.I,
)

# A league table, a "best of" list, or a position in one. Dated by nature: the
# question means the current edition, never whichever edition a search engine
# happens to surface.
_RANKED = re.compile(
    r"\b(?:rank(?:ing|ings|ed)?|league table|top \d+|top ten|best \d+|"
    r"(?:the )?best (?:uk |british |uni|universit|school|course|phone|laptop|"
    r"broker|bank|card)|worst \d+|highest[- ]?(?:paid|ranked|rated)|"
    r"most (?:popular|valuable|expensive)|"
    r"league position|table of)\b",
    re.I,
)

# A buying decision. What is worth buying depends on what is on sale now:
# measured, "which laptop should i buy for computer science uni under 1000
# pounds" carried no time or ranking word, searched undated, and recommended
# a 2021 ThinkPad from a blog in 2026.
_PURCHASE = re.compile(
    r"\b(?:which|what)\s+(?:\w+\s+){0,3}should\s+i\s+(?:buy|get|choose|pick|go for)\b"
    r"|\bshould\s+i\s+(?:buy|get)\b|\bworth\s+buying\b"
    r"|\bbest\s+(?:\w+\s+){0,2}(?:to\s+buy|for\s+(?:uni|university|students?|school|"
    r"gaming|work|coding|programming|editing|travel))\b",
    re.I,
)

# Two named things measured against each other. The model has no way to know
# whether it knows them, so it must look.
_COMPARISON = re.compile(
    r"\b(compare|comparison|versus|vs\.?|difference between|"
    r"better than|which is better|which one should i|pros and cons)\b",
    re.I,
)

_STOPWORDS = frozenset(
    "a an the is are was were be been being do does did what whats what's who "
    "whom whose which when where why how to of for in on at by with from and "
    "or but if then than that this these those it its me my your you i we us "
    "can could would should will shall may might must about into over under "
    "right now tell give show explain".split()
)


# Named because the caller keys off it: a turn exempted for this reason is
# handed the runtime's own facts instead of web results.
SELF_REASON = "about SYRIX itself"
PROBLEM_REASON = "a problem to work out, not a fact to look up"

# A self-contained problem: every number it needs is in the question, and the
# answer is reasoned to, not found. Searching one of these fetched a page that
# happened to contain a number and the answer repeated it — "how many positive
# integers below 1000 are divisible by 7 but not 11" came back as "115 [1]",
# cited, and wrong (130).
_PURE_MATHS = re.compile(
    r"\b(?:remainder|divisible|divisors?|factorial|prime(?:s| factor)?|integers?|"
    r"last (?:two|three|\d) digits|digits? of|arrangements?|arranged|permutations?|"
    r"combinations?|probability|equation|solve for|simplify|derivative|integral|"
    r"sum of (?:all|the first)|consecutive|modulo|mod \d|gcd|lcm|sequence|"
    r"perimeter|area of|volume of|hypotenuse|triangle|rectangle|circle)\b", re.I)
_WORD_PROBLEM = re.compile(
    r"\b(?:how (?:many|much|long|far|fast|old)|what (?:is|was) (?:the )?(?:average|total|"
    r"speed|area|sum|product|difference|ratio|remainder|value)|compounded|per cent|percent|"
    r"% of|average speed|km/h|mph|each (?:day|night|week|hour)|in total|altogether)\b", re.I)
_PUZZLE = re.compile(
    r"\b(?:taller than|shorter than|older than|younger than|brothers?|sisters?|"
    r"day of the week|days? from now|if today is|who is the (?:second|third|tallest|oldest|"
    r"shortest|youngest))\b", re.I)
_LOOKUP_NOT_PROBLEM = re.compile(
    r"\b(?:vs\.?|versus|compare|population|net worth|salary|price of|how much (?:is|does|do) "
    r"(?:a|an|the)\b|cost of|live|lives)\b", re.I)


# Arithmetic said in words: "17 times 23", "144 divided by 12".
_SPOKEN_ARITHMETIC = re.compile(
    r"\d\s*(?:times|x|multiplied by|divided by|plus|minus|over|to the power of|squared|cubed|"
    r"percent of|% of)\s*\d?", re.I)


def _is_problem(text: str) -> bool:
    numbers = re.findall(r"\d+(?:[.,]\d+)?", text)
    if _SPOKEN_ARITHMETIC.search(text) and numbers:
        return True
    if _LOOKUP_NOT_PROBLEM.search(text):
        return False
    if _PUZZLE.search(text):
        return True
    if _PURE_MATHS.search(text) and numbers:
        return True
    return bool(_WORD_PROBLEM.search(text) and len(numbers) >= 2)
# The subject is the thread, or text the user pasted into the message. Both are
# already in the prompt, and both were being searched for on the open web.
THREAD_REASON = "it refers to this conversation"
PASTED_REASON = "he pasted the thing he is asking about"
SOCIAL_REASON = "conversation, not a question"


@dataclass
class Need:
    """Whether to gather, and what for."""

    wanted: bool
    reason: str
    queries: List[str] = field(default_factory=list)
    # A hint for ranking, not a hard filter. 0 means no recency preference.
    recency_days: int = 0


def _keywords(question: str) -> List[str]:
    words = re.findall(r"[A-Za-z0-9$£€.+#-]{2,}", question)
    return [w for w in words if w.lower() not in _STOPWORDS]


def _queries_for(question: str, recency_days: int, limit: int) -> List[str]:
    """Search strings derived from the question, deterministically.

    Search engines handle natural language, so the question itself is the
    strongest single query and goes first. The variants exist to widen
    coverage, not to out-guess the engine: keywords alone catch pages that
    phrase things differently, and a dated form catches pages that would
    otherwise rank behind years of older coverage.
    """
    text = " ".join(str(question or "").split())
    if not text:
        return []

    out: List[str] = [text]
    keywords = _keywords(text)
    if len(keywords) >= 2:
        joined = " ".join(keywords[:8])
        if joined.lower() != text.lower():
            out.append(joined)
    if recency_days and recency_days <= 400 and keywords:
        import time
        out.append(" ".join(keywords[:6]) + " " + time.strftime("%B %Y"))

    seen = set()
    unique: List[str] = []
    for q in out:
        key = q.lower()
        if key not in seen:
            seen.add(key)
            unique.append(q)
    return unique[: max(1, limit)]


def assess(question: str, *, max_queries: int = 6,
           has_attachment: bool = False, last_reply: str = "") -> Need:
    text = " ".join(str(question or "").split())
    if not text:
        return Need(False, "empty question")

    # Who made SYRIX, asked in any language or order. First, because the
    # patterns below are English and "which company built you" slipped past
    # them to the web, which answered with a company called "We Built You".
    if creator.asks(text):
        return Need(False, SELF_REASON)

    # Thanks, praise, hello, goodbye — conversation, answered from the thread
    # in a line. Not when SYRIX has just asked something: "ok" is then a reply
    # to the question. A request to redo the last answer ("make it shorter",
    # "explain more") is about the thread too. See research.social.
    if _social.is_social(text) and not _social.asked_a_question(last_reply):
        return Need(False, SOCIAL_REASON)
    if last_reply and _social.reworks_last_answer(text):
        return Need(False, THREAD_REASON)

    # The user attached a file and asked about it. The file is the evidence and it
    # is already being read, so searching as well filled the thinking panel
    # with unrelated queries — "why did brentford miss its target and what did
    # it cost", asked of a depot report, searched Brentford FC across goal.com,
    # ESPN, YouTube and TikTok.
    #
    # Only a TIME word overrides this, never the volatile-subject list. That
    # list exists to catch a question whose answer moves, and it is full of
    # exactly the words a document question uses — rate, cost, results, score,
    # status. Reading it here searched the web for almost every attachment
    # turn. "How does my CV compare to jobs posted today" still searches,
    # because "today" is a claim about the present that no file can settle.
    if has_attachment and not _TIME_WORDS.search(text):
        return Need(False, "the answer is in the attachment")

    # The exemptions. Each is a case where looking something up cannot
    # improve the answer, not merely one where it might not be needed.
    if _SELF_CONTAINED.match(text) and len(text) < 40:
        return Need(False, "greeting")
    if _ARITHMETIC.match(text) or _MATH_PHRASING.search(text):
        return Need(False, "arithmetic")
    # "If today is Wednesday..." carries a time word and is still a puzzle.
    if _is_problem(text) and (not _TIME_WORDS.search(text) or _PUZZLE.search(text)):
        return Need(False, PROBLEM_REASON)
    if _DENIED_CAPABILITY.search(text):
        # A negated capability put as a statement — "you cant read my files
        # right" — is a question about SYRIX even though it names an object,
        # so the guard below sent it away as an instruction and the runtime
        # facts were never supplied. Measured on the local lane it answered
        # "Correct, I cannot read or access any of your personal files", which
        # is false: the documents tool is loaded. Checking the premise needs
        # the capability block as evidence, and that is what SELF_REASON does.
        return Need(False, SELF_REASON)
    if _ABOUT_SYRIX.search(text) and not _POINTS_AT_A_THING.search(text):
        return Need(False, SELF_REASON)
    if _LEANS_ON_THE_THREAD.search(text):
        return Need(False, THREAD_REASON)
    if _looks_pasted(text) and not _ASKS_TO_LOOK.search(text):
        return Need(False, PASTED_REASON)
    if _WRITE_CODE.search(text) and not _VOLATILE.search(text):
        return Need(False, "writing code, not looking something up")
    if _PERSONAL.search(text) and not _VOLATILE.search(text):
        return Need(False, "about the user, not the world")

    time_hit = _TIME_WORDS.search(text)
    volatile_hit = _VOLATILE.search(text)

    if time_hit and volatile_hit:
        recency = 7
        reason = f"time-bound and volatile ({time_hit.group(0)}, {volatile_hit.group(0)})"
    elif time_hit:
        recency = 30
        reason = f"time-bound ({time_hit.group(0)})"
    elif volatile_hit:
        recency = 30
        reason = f"volatile subject ({volatile_hit.group(0)})"
    elif _RANKED.search(text):
        # A ranking is dated even when the question does not say so. Asked for
        # the top ten UK universities it matched no time word, so recency was
        # zero, every page ranked on keywords alone and the answer came back
        # from 2018 tables — in 2026, with "these might have changed by 2024"
        # attached. Rankings are published annually, so half a year is the
        # window that keeps the current edition ahead of the archive.
        recency = 180
        reason = "a ranking, which is always dated"
    elif _PURCHASE.search(text):
        recency = 180
        reason = "a buying decision, which depends on what is on sale now"
    elif _COMPARISON.search(text):
        # Two named things measured against each other. This is the case the
        # old keyword list missed and then invented an answer for.
        recency = 90
        reason = "a comparison between named things"
    else:
        # Not obviously current, not obviously stale, and not exempt. Look
        # anyway: the cost is a few seconds in parallel, and the cost of not
        # looking is an answer that reads exactly as confident when it is
        # made up.
        recency = 0
        reason = "not exempt, so worth checking rather than recalling"

    return Need(True, reason, _queries_for(text, recency, max_queries), recency)
