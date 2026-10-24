"""Messages that are conversation, not questions.

Measured live: "thx bro apprecate it", sent after a good answer, was
explained. Four words is short enough to be read as a follow-up, so the
rewrite call turned it into "what does 'thx bro appreciate it' mean", triage
searched that, and the reply was a usage note on the abbreviation "thx",
cited. A person hearing thanks says "any time".

Two shapes are recognised here, in code and before any model call:

- **Social** — thanks, acknowledgement, praise, a greeting or a goodbye, and
  nothing else. Answered in a line, from the conversation.
- **Reworking the last answer** — "make it shorter", "explain more", "give me
  an example", "put that in a table". The subject is the answer above, so
  the thread is the only evidence; searching replaced it with strangers'
  pages and the rewrite cost most of a second first.

Both are deliberately narrow. A message with any word outside the social
vocabulary is not social — "thanks, now do the same for Paris" is a request
— and reworking is recognised only when it points at what was said. Anything
else is a question and goes through triage exactly as before.
"""
from __future__ import annotations

import difflib
import re
from typing import List

_WORD = re.compile(r"[a-z']+|\d+")

# Words that make a message social on their own.
_ANCHORS = frozenset("""
thanks thank thx thnx thanx thnks tnx ty tysm tyvm cheers appreciate appreciated
appreciation grateful legend goat lifesaver
nice cool great perfect awesome amazing brilliant excellent fantastic sweet lovely
wonderful superb solid fire lit dope sick insane goated
lol lmao haha hahaha lmfao rofl heh love loved worked fixed np
ok okay k kk alright aight noted understood gotcha bet
hi hey hello yo hiya sup wassup morning evening afternoon gm
bye goodbye cya later gn goodnight night peace
wow woah damn
""".split())

# Words that may sit around the anchors without making it a request.
_FILLER = frozenset("""
bro bruh man mate dude fam g boss king sir buddy pal guys syrix
so very really much a lot it that this you u ya ye ur your you're youre ure
the is was are were be been am i i'm im me my we us
for all of to and just too as always again once
job work done well one answer help helped helping helps worked works working
got get it's its good best better the thing stuff
mate bud no nah worries problem prob
see soon tomorrow later have nice day great weekend like liked
""".split())
# "Yes", "yeah" and "sure" are left out on purpose: after a question they are
# consent, and consent is an instruction to carry on, not small talk.

# Common two- and three-word social phrases whose words are not anchors on
# their own. Each is folded into an anchor before the words are checked.
_PHRASES = re.compile(
    r"\b(?:good (?:job|work|stuff|one|shout|call)|well done|sounds good|makes sense|"
    r"got it|fair enough|no (?:worries|problem|prob)|all good|my bad|"
    r"you(?:'re| are|re) (?:the best|a legend|a lifesaver|great|amazing|awesome))\b")

# Longer anchors that typos reach, matched by similarity: "apprecate",
# "appriciate", "thnaks", "awsome", "perfecto".
_FUZZY = ("appreciate", "appreciated", "thanks", "awesome", "perfect", "amazing",
          "brilliant", "excellent", "fantastic", "legend", "goodnight", "understood",
          "alright", "lifesaver", "wonderful", "hello")

_MAX_WORDS = 12


def _is_anchor(word: str) -> bool:
    if word in _ANCHORS:
        return True
    if len(word) >= 5:
        return any(difflib.SequenceMatcher(None, word, target).ratio() >= 0.8
                   for target in _FUZZY)
    return False


def asked_a_question(last_reply: str) -> bool:
    """Did SYRIX's last reply end by asking something?

    Then a short reply is an answer to it: "ok" after "want me to draft it?"
    means draft it.
    """
    tail = str(last_reply or "").rstrip(" \n*_)\"'”").rstrip()
    return tail.endswith("?")


def last_reply(history) -> str:
    for turn in reversed(list(history or [])):
        if isinstance(turn, dict) and turn.get("role") == "assistant":
            return str(turn.get("content") or "")
    return ""


def words(text: str) -> List[str]:
    return _WORD.findall(str(text or "").lower().replace("’", "'"))


# Asking after SYRIX itself — "how are you", "what's up", "how's it going",
# "you good bro?" — is conversation too. It is phrased as a question, so the
# rule below never saw it, and "how are you" was answered with citations to
# grammar sites. Matched only as the whole message.
_SMALL_TALK = re.compile(
    r"^\s*(?:(?:hi+|hey+|hello+|yo+|sup|ok(?:ay)?|so|and|bro|man|mate)[\s,!.]+)*"
    r"(?:how\s+(?:are|r)\s+(?:you|u|ya)(?:\s+(?:doing|going|holding up|keeping|feeling))?"
    r"|how(?:'s|s|\s+is)\s+(?:it\s+going|things|life|your\s+day|ur\s+day|everything|you)"
    r"|how\s+(?:have|ve)\s+(?:you|u)\s+been|how\s+(?:you|u)\s+(?:doing|been)"
    r"|what(?:'s|s|\s+is)\s+up|wassup|wyd"
    r"|(?:are\s+)?(?:you|u)\s+(?:good|ok(?:ay)?|alright|there|awake))"
    r"(?:\s+(?:today|tonight|now|then|bro|man|mate|buddy|dude|syrix))*[\s?!.]*$",
    re.I,
)


def is_small_talk(text: str) -> bool:
    """"How are you" and its relatives, said to SYRIX, and nothing more."""
    return bool(_SMALL_TALK.match(" ".join(str(text or "").replace("’", "'").split())))


def is_social(text: str) -> bool:
    """Thanks, acknowledgement, praise, hello or goodbye — and nothing more."""
    raw = " ".join(str(text or "").split())
    if raw and is_small_talk(raw):
        return True
    if not raw or "?" in raw:
        return False
    found = words(_PHRASES.sub(" cheers ", raw.lower().replace("’", "'")))
    if not found or len(found) > _MAX_WORDS:
        return False
    anchors = [w for w in found if _is_anchor(w)]
    if not anchors:
        return False
    return all(_is_anchor(w) or w in _FILLER for w in found)


# The reply below was about something the last answer said, and asks for it
# again in another form. Each alternative names the thing it points at — "it",
# "that", "this", "more", "again" — or is a bare instruction that can only mean
# the answer above ("continue", "go on"). "Explain quantum computing" names a
# subject of its own and is not matched.
_REWORK = re.compile(
    r"^\s*(?:ok(?:ay)?|so|now|and|pls|please|can (?:you|u)|could (?:you|u)|"
    r"(?:bro|man|mate)|just)?[\s,]*(?:pls|please)?[\s,]*(?:"
    r"(?:make|keep) (?:it|that|this|them|the answer)\s+(?:shorter|longer|simpler|clearer|"
    r"easier|more (?:formal|casual|detailed|concise|professional|simple)|less \w+|"
    r"a bit \w+|in \w+)"
    r"|(?:shorten|simplify|expand|rephrase|reword|rewrite|summari[sz]e|translate|"
    r"condense|redo|reformat|fix) (?:it|that|this|them|the (?:answer|last one|above))\b"
    r"|(?:explain|elaborate|expand|go|say) (?:it |that |this )?(?:more|further|again|"
    r"in (?:more )?detail|simpler|in simpler (?:words|terms)|like i'?m (?:5|five|a kid)|"
    r"on (?:it|that|this))\b"
    r"|(?:explain|elaborate on) (?:it|that|this)\b"
    r"|elaborate$|continue$|go on$|keep going$|carry on$|more$|tl;?dr$|eli5$"
    r"|(?:give|show) (?:me )?(?:an|another|a (?:real|simple|quick)|some) examples?"
    r"(?: (?:of|for) (?:it|that|this|them))?(?: pls| please)?$"
    r"|(?:put|turn|show|do|write|format|make) (?:it|that|this|them|the (?:answer|above)) "
    r"(?:as|in|into) (?:a |an )?(?:table|list|bullets?|bullet points|points|steps|"
    r"paragraphs?|one line|one sentence|\w+ words|simple english|plain english)"
    r"|what (?:do|did) (?:you|u) mean(?: by (?:that|this|it))?$"
    r"|in (?:simpler|plain|simple) (?:words|english|terms)$"
    r"|say (?:that|it) again\b|repeat (?:that|it)\b"
    r")",
    re.I,
)
_REWORK_MAX_WORDS = 14


def reworks_last_answer(text: str) -> bool:
    """A request to redo, extend or reshape what was just said."""
    raw = " ".join(str(text or "").split())
    if not raw or len(raw.split()) > _REWORK_MAX_WORDS:
        return False
    return bool(_REWORK.search(raw.rstrip(" ?!.")))


# The note handed to the model for a social turn. Given only the persona,
# a fast lane sometimes answered thanks as though it were a question about
# the word.
SOCIAL_NOTE = (
    "THIS MESSAGE IS SOCIAL — thanks, an acknowledgement, praise, a greeting "
    "or a goodbye. Reply the way a friend who helped would: one short, warm, "
    "natural line, in the same register. Do not explain or define any of its "
    "words, do not summarise the conversation, do not offer further help.")
