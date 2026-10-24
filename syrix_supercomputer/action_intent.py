"""Classifying what a click or a keystroke would actually *do*.

The permission model has a hole in the middle if it only knows verbs. "Click"
covers following a link and pressing **Send** — treating both as ordinary
browsing lets the agent mail something on its own; treating both as dangerous
means a card per link and a mode nobody can use.

So the gate action is chosen from the *target*, not the verb. Clicking a link
is ``click`` (available, no card). Clicking a control labelled "Send", "Post",
"Delete", "Buy now" or "Confirm payment" is re-labelled to the matching
side-effecting action, which routes it into the capability that is off by
default and raises a card every time.

Matching is deliberately generous: a false positive costs one extra card, a
false negative sends an email the user did not approve. When a label is ambiguous
the consequential reading wins.

Order matters — the patterns are tried most-severe first, so "confirm and pay"
is a payment rather than a form submission.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .capabilities import (
    CAP_ACCOUNT_SETTINGS,
    CAP_AGREE_TO_TERMS,
    CAP_DELETE_ONLINE_CONTENT,
    CAP_PAYMENTS,
    CAP_PUBLISH,
    CAP_SEND_MESSAGES,
    CAP_SUBMIT_FORMS,
    CAP_UPLOADS,
)

# Placing a trade is not "a purchase with a card". It is an irreversible market
# order against the user's own money, at a price that moves while the card is on
# screen, and SYRIX has no trading-execution system that has been designed and
# approved for it. So it is not a permission card — it is refused outright, in
# every safety mode, and the analysis is handed back instead. Deliberately the
# FIRST rule: "confirm buy order" must read as a trade, not as a form.
# Only language that can ONLY mean a trade. A shop's "Buy now" stays a
# purchase — that is already ALWAYS_ASK, so it stops for a card either way, and
# reading every checkout button as a stock order would be wrong about the
# ordinary case to be right about the rare one.
TRADE_ACTION = "place_trade"
_TRADE_RE = re.compile(
    r"\bplace\s+(?:the\s+)?(?:order|trade)\b"
    r"|\b(?:submit|confirm|review|preview)\s+(?:the\s+)?(?:order|trade)\b"
    r"|\b(?:buy|sell)\s+(?:order|shares?|stock)\b"
    r"|\b(?:market|limit|stop|stop[- ]loss)\s+order\b"
    r"|\btrade\s+now\b|\bexecute\s+(?:the\s+)?trade\b"
    r"|\bopen\s+(?:a\s+)?position\b|\bsell\s+short\b|\bgo\s+(?:long|short)\b",
    re.IGNORECASE,
)

# Hosts where a bare "Buy" or "Sell" button is a market order rather than a
# checkout. Only used to reclassify a label that already reads as a purchase.
_BROKER_HOSTS = (
    "tradingview.com", "robinhood.com", "webull.com", "etoro.com", "ig.com",
    "interactivebrokers.", "schwab.com", "fidelity.", "vanguard.",
    "trading212.com", "freetrade.io", "hargreaveslansdown.co.uk", "aj-bell.co.uk",
    "degiro.", "saxobank.com", "plus500.", "cmcmarkets.com", "oanda.com",
    "binance.", "coinbase.com", "kraken.com", "bybit.com", "okx.com",
    "moomoo.com", "tastytrade.com", "thinkorswim.", "etrade.com",
)


def _is_broker(host: str) -> bool:
    h = str(host or "").strip().lower()
    return bool(h) and any(b in h for b in _BROKER_HOSTS)

# (compiled pattern, gate action, human phrase for the card)
_RULES: List[Tuple[re.Pattern, str, str]] = [
    (_TRADE_RE, TRADE_ACTION, "place a trade with your money"),
    (re.compile(r"\b(pay|payment|checkout|buy\s*now|place\s+order|complete\s+(?:order|purchase)"
                r"|subscribe|add\s+card|confirm\s+(?:and\s+)?(?:pay|purchase|order))\b", re.I),
     "purchase", "make a purchase or payment"),
    (re.compile(r"\b(delete|remove|discard|trash|archive|unsend|revoke|deactivate|close\s+account)\b", re.I),
     "delete_online_content", "delete or archive something"),
    (re.compile(r"\b(send|reply|forward|submit\s+reply|send\s+message|send\s+email)\b", re.I),
     "send_message", "send a message"),
    (re.compile(r"\b(post|publish|tweet|share|comment|upload\s+post|go\s+live)\b", re.I),
     "publish", "post or publish something"),
    (re.compile(r"\b(upload|attach\s+file|choose\s+file|browse\s+files)\b", re.I),
     "upload", "upload a file"),
    (re.compile(r"\b(accept|agree|i\s+agree|terms|consent|allow\s+all|opt\s+in)\b", re.I),
     "agree_to_terms", "agree to terms on your behalf"),
    (re.compile(r"\b(save\s+settings|change\s+password|update\s+profile|account\s+settings"
                r"|security\s+settings|privacy\s+settings)\b", re.I),
     "change_account_settings", "change an account setting"),
    (re.compile(r"\b(submit|apply|confirm|continue\s+to\s+pay|book|reserve|register|sign\s+up)\b", re.I),
     "submit_form", "submit a form"),
]

_CAP_FOR_ACTION = {
    TRADE_ACTION: CAP_PAYMENTS,
    "purchase": CAP_PAYMENTS,
    "delete_online_content": CAP_DELETE_ONLINE_CONTENT,
    "send_message": CAP_SEND_MESSAGES,
    "publish": CAP_PUBLISH,
    "upload": CAP_UPLOADS,
    "agree_to_terms": CAP_AGREE_TO_TERMS,
    "change_account_settings": CAP_ACCOUNT_SETTINGS,
    "submit_form": CAP_SUBMIT_FORMS,
}

# Labels that merely *look* consequential on a listing page. "Delete" inside the
# text of an article, or a "Share" affordance next to every row, is not the
# button being pressed — but we only ever see the label of the element actually
# targeted, so this stays small and specific rather than clever.
_BENIGN = re.compile(r"^\s*(share\s+on|share\s+this|send\s+feedback|report)\b", re.I)


# A real control says what it does in a few words: "Send", "Delete", "Buy now".
# A row in a list carries its whole content as its label — an email's subject and
# preview, a post's body — and matching consequential verbs against THAT flags
# every message that happens to contain "confirm" or "apply".
#
# This is not hypothetical: it stopped SYRIX opening any email in Gmail, because
# the row labels tripped the submit-form rule and that capability is off by
# default, so it was a hard refusal rather than a card. Clicking a row is
# navigation. Length is the cheap, reliable signal for telling the two apart.
_MAX_CONTROL_LABEL_CHARS = 32

# Tags that can actually perform something. A link navigates; a list row
# navigates. Only these commit.
_CONTROL_TAGS = frozenset({"button", "input", "select"})
_CONTROL_ROLES = frozenset({"button", "menuitem", "checkbox", "switch", "radio"})


def is_control(tag: str = "", role: str = "") -> bool:
    """Whether an element is a control rather than a link or a list row."""
    return (str(tag or "").strip().lower() in _CONTROL_TAGS
            or str(role or "").strip().lower() in _CONTROL_ROLES)


def classify_click(label: str, *, tag: str = "", role: str = "",
                   host: str = "") -> Optional[Tuple[str, str]]:
    """``(gate_action, human_phrase)`` for a consequential control, else None.

    ``tag``/``role`` are optional: called without them this still classifies on
    the label alone, which is the conservative reading. Called with them — as
    the tool layer does — a link or a list row is never treated as a commit,
    because clicking one cannot send, buy or delete anything by itself.
    """
    text = str(label or "").strip()
    if not text or _BENIGN.match(text):
        return None
    # An explicit non-control (a link, a row) is navigation whatever it says.
    if (tag or role) and not is_control(tag, role):
        return None
    # An overlong label is content, not a control's name.
    if len(text) > _MAX_CONTROL_LABEL_CHARS:
        return None
    for pattern, action, phrase in _RULES:
        if pattern.search(text):
            # On a broker or an exchange, "Buy" is not a checkout — it is a
            # market order against the user's money, and that is refused outright
            # rather than card-gated.
            if action == "purchase" and _is_broker(host):
                return TRADE_ACTION, "place a trade with your money"
            return action, phrase
    return None


def classify_field(label: str, *, submit: bool) -> Optional[Tuple[str, str]]:
    """A typed field that is itself the consequential control.

    Typing is harmless until it goes somewhere, and pressing Enter in a search
    box is how browsing works — so plain submission is not consequential on its
    own. It becomes consequential when the field being submitted is a composer:
    Enter in a chat or comment box sends, and there is no button to catch it at.
    """
    if not submit:
        return None
    if re.search(r"\b(message|comment|reply|tweet|post|chat|dm)\b", str(label or ""), re.I):
        return "send_message", "send a message"
    return None


def capability_for_action(action: str) -> str:
    return _CAP_FOR_ACTION.get(str(action or ""), "")


__all__ = ["classify_click", "classify_field", "capability_for_action"]
