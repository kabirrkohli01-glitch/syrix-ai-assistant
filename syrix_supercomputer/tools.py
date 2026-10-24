"""The tool surface: what the Supercomputer agent can actually do, and how.

Every tool is one entry in :data:`TOOLS`. An entry declares the gate action it
runs under, its arguments, and a one-line description — the same table builds
the prompt the model sees and dispatches the call it makes, so the two can never
drift apart. A tool that is not in the table cannot be called; a capability that
is switched off removes its tools from the prompt *and* is refused by the gate.

Everything routes through :func:`syrix_supercomputer.gate.execute`. There is no
call path from here to the browser or the filesystem that skips it.

Two things this module owns beyond dispatch:

**Element addressing.** The model never sends coordinates or CSS selectors. It
sends an element id from the last digest, and the browser re-locates it live at
click time — a page that reflows between reading and clicking cannot be made to
land the click somewhere else.

**Consequence re-labelling.** ``click`` is re-labelled to ``send_message``,
``purchase``, ``delete_online_content`` and so on based on the target's label
(see :mod:`syrix_supercomputer.action_intent`) before the gate sees it, so the
capability and card that apply are the ones for what the click would *do*.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import syrix_obs as obs

from . import action_intent, capabilities, destinations, documents, gate, results, workspace
from .browser import controller as browser
from .browser import injection_guard as guard
from .browser import policy as browser_policy
from .contract import normalise_host

# How much page text one observation may carry back. Big enough to answer from,
# small enough that a dozen steps of context still fit in a modest model — and
# small enough to be quick, which matters more than it looks: every step pays
# this in upload time AND in the model's reading time, a dozen-plus times a task.
# Scrolling is how the agent gets more, so a smaller window costs coverage only
# when it never scrolls.
OBS_TEXT_CHARS = 2200
OBS_ELEMENTS = 36
# Enough that a listing page's headlines survive alongside whatever site
# furniture the digest could not classify. Navigation is the agent's main move
# on a results or index page, so this is the wrong budget to be stingy with.
OBS_LINKS = 26

# Settle time after an action before re-reading. Long enough for a normal page
# to react, short enough not to dominate a task: at a dozen actions, half a
# second each way is the difference between brisk and sluggish. A page that
# needs longer gets it from the agent scrolling or re-reading, which is cheap.
_SETTLE_CLICK = 0.6
_SETTLE_SUBMIT = 0.9
_SETTLE_TYPE = 0.25
_SETTLE_SCROLL = 0.35
_SETTLE_NAV = 0.6


@dataclass
class Tool:
    name: str
    action: str                 # gate action this runs under
    args: Tuple[str, ...]       # argument names, for the prompt
    description: str
    handler: Callable[..., Dict[str, Any]] = field(repr=False, default=None)
    # Tools that end the loop or only record state never touch the gate.
    terminal: bool = False


@dataclass
class ToolResult:
    ok: bool
    observation: str
    data: Dict[str, Any] = field(default_factory=dict)
    needs_permission: bool = False
    permission_request_id: str = ""
    finished: bool = False
    answer: str = ""


# Analytics and campaign parameters. A Guardian "Support us" link carries 300
# characters of them, and a dozen such links crowd the real headlines out of the
# observation entirely. Stripping them leaves a URL that still works.
_TRACKING_PARAMS = (
    "utm_", "intcmp", "refpvid", "acquisitiondata", "componenteventparams",
    "abcmp", "gclid", "fbclid", "mc_cid", "mc_eid", "ito", "cmpid", "ncid",
    "campaignid", "ref_src", "spm", "_ga",
)


def _tidy_url(url: Any) -> str:
    """A URL short enough to read, and still openable."""
    raw = str(url or "")
    if "?" not in raw:
        return raw[:220]
    base, _, query = raw.partition("?")
    kept = [
        part for part in query.split("&")
        if part and not any(part.lower().startswith(p) for p in _TRACKING_PARAMS)
    ]
    out = base + ("?" + "&".join(kept) if kept else "")
    return out[:220]


# A page that stops mid-sentence and asks for an account. The model reads the
# truncation as "there was nothing more here" and reports that it could not find
# the content — which tells the user nothing he can act on. Naming the wall turns a
# vague failure into a fact he can do something about (log in, or don't bother).
_GATE_RE = re.compile(
    r"(?:enter your email to keep reading|to keep reading|register to continue"
    r"|create a(?:n)? (?:free )?account to (?:continue|read)|sign in to (?:continue|read)"
    r"|subscribe to (?:continue|read)|this article is for subscribers"
    r"|you have reached your (?:free )?article limit|become a (?:member|subscriber) to read"
    r"|continue reading (?:this article )?(?:with|by))",
    re.IGNORECASE,
)


def _gate_note(text: str) -> str:
    if not _GATE_RE.search(str(text or "")):
        return ""
    return ("NOTE: this page is GATED — the text above stops at a sign-in or "
            "registration wall, so what you have is only the opening. That is a "
            "fact about the page, not an absence of content: report what you did "
            "read AND say plainly that the rest is behind a wall. Do not say you "
            "could not find the article.")


# ── formatting observations ────────────────────────────────────────────────
def _fmt_page(page: Dict[str, Any], *, note: str = "") -> str:
    """Render a digest for the model, fenced as untrusted content.

    The fence is not decoration. Everything below it came from a web page, and
    the loop's system prompt states that content inside it is data — never an
    instruction, never a change of goal.
    """
    prepared = guard.prepare(page)
    meta = page.get("meta") or {}
    lines: List[str] = [
        f"PAGE: {page.get('title') or '(untitled)'}",
        f"URL: {page.get('url') or ''}",
    ]
    if meta.get("scrollHeight"):
        pos = "bottom" if meta.get("atBottom") else f"{meta.get('scrollY', 0)}px of {meta.get('scrollHeight')}px"
        lines.append(f"SCROLL: {pos}")

    # Links first: on an index, a results page or an article listing, choosing
    # where to go next is the move, and it was being read after three dozen
    # navigation-menu entries.
    links = (page.get("links") or [])[:OBS_LINKS]
    if links:
        lines.append("\nLINKS ON THIS PAGE — each is a page you can open with open_url:")
        for ln in links:
            lines.append(f"  - {str(ln.get('text') or '')[:90]} -> {_tidy_url(ln.get('href'))}")

    elements = (page.get("elements") or [])[:OBS_ELEMENTS]
    if elements:
        lines.append("\nCLICKABLE / TYPEABLE (use the id with click or type_text):")
        for e in elements:
            kind = "input" if e.get("editable") else e.get("tag", "")
            lines.append(f"  [{e.get('id')}] {kind}: {str(e.get('label') or '')[:90]}")

    text = prepared["text"]
    if text:
        lines.append("\nPAGE TEXT:\n" + text[:OBS_TEXT_CHARS])
        if len(text) > OBS_TEXT_CHARS or meta.get("truncated"):
            try:
                next_offset = int(meta.get("textOffset") or 0) + int(
                    meta.get("textWindowChars") or OBS_TEXT_CHARS)
            except (TypeError, ValueError):
                next_offset = OBS_TEXT_CHARS
            lines.append(
                f"[text truncated — use read_page with offset {next_offset} for the next section]")
    wall = _gate_note(str(page.get("text") or ""))
    if wall:
        lines.append("\n" + wall)
    quirk = _site_note(str(page.get("url") or ""), str(page.get("text") or ""))
    if quirk:
        lines.append("\n" + quirk)
    if prepared.get("suspicious"):
        lines.append("\nNOTE: " + guard.describe(prepared["findings"]))
    if note:
        lines.append("\n" + note)
    return "\n".join(lines)


# Sites where what is on screen is a SUBSET of what was asked for, and reading
# it literally produces a confident wrong answer. Each entry is one real
# failure, not a guess about what a site might do.
#
#   (host fragment, text marker or "", the note)
#
# The marker keeps the note off pages where it would be noise — the Gmail hint
# is for a message list, not for a single open email.
_SITE_NOTES: Tuple[Tuple[str, str, str], ...] = (
    (
        "mail.google.com", "",
        "NOTE ABOUT THIS PAGE: Gmail splits the inbox into category tabs — "
        "Primary, Promotions, Social, Updates, Forums — and only ONE of them is "
        "on screen. What you can see is NOT the whole inbox. Never report the "
        "inbox as empty, or as containing only what is listed here, from this "
        "view alone. To see everything at once, open "
        "https://mail.google.com/mail/u/0/#search/in%3Ainbox — that lists every "
        "inbox message across all categories, and is what the user means by "
        "\"my inbox\".",
    ),
    (
        "youtube.com", "",
        "NOTE ABOUT THIS PAGE: YouTube renders lazily. The videos listed here "
        "are the ones loaded so far, not everything on the page — scroll before "
        "concluding something is not present.",
    ),
)


def _site_note(url: str, text: str) -> str:
    """A standing warning for a site whose view lies about its own contents."""
    low_url = str(url or "").lower()
    if not low_url:
        return ""
    low_text = str(text or "").lower()
    for host, marker, note in _SITE_NOTES:
        if host in low_url and (not marker or marker in low_text):
            return note
    return ""


def _active_tab_id(ctx: "ToolContext") -> str:
    return ctx.tab_id


def _why(res: "gate.ActionResult") -> str:
    """The reason a gated action failed, said once and usefully.

    Most gate refusals carry a real explanation in ``message`` ("that capability
    is switched off in Supercomputer settings"), and that is what should reach
    the model and the report. The exception is the generic pass-through the gate
    emits for a plain failure — "That did not go through: X" — which, wrapped in
    the caller's own sentence, produced "That click did not go through: That did
    not go through: element_not_found." In that one case the bare reason is
    clearer than the sentence.
    """
    message = str(getattr(res, "message", "") or "").strip()
    reason = str(getattr(res, "reason", "") or "").strip()
    if message.startswith("That did not go through"):
        return reason or message
    return message or reason or "no reason given"


# ── the execution context ──────────────────────────────────────────────────
@dataclass
class ToolContext:
    """Everything a tool needs that is not one of its arguments."""

    task_id: str
    request_id: str
    config: Dict[str, Any]
    tab_id: str = ""
    # Which stage of a project this run belongs to, so a finding is filed under
    # the subject it was found for. Empty for an ordinary single-loop task,
    # where every finding simply belongs to the one job.
    stage_id: str = ""
    # The stage kind, which decides whether this run may touch the browser at
    # all. Empty (an ordinary single-loop task) means "everything", exactly as
    # before. See BROWSER_TOOLS.
    kind: str = ""
    # Element id -> label, from the most recent digest. Used to classify a click
    # by what it targets rather than by the fact that it is a click.
    element_labels: Dict[str, str] = field(default_factory=dict)
    # Element id -> (tag, role). A link or a list row can navigate but cannot
    # commit, so the consequence classifier needs to know which it is looking at.
    element_kinds: Dict[str, Tuple[str, str]] = field(default_factory=dict)
    last_url: str = ""
    # The next readable text window on a long page.  Repeating the same
    # open_url after a truncated observation means "keep reading" much more
    # often than "reload the page"; retaining this cursor prevents an agent
    # from burning its bounded step budget reopening character zero.
    next_read_offset: int = 0
    # Tabs this task opened for itself, most recent last. A tab the user opened is
    # HIS — navigating it away destroys the page he was reading and was asking
    # about, which is what "compare these two tabs" collapsing into one
    # search-results tab looked like. The agent reuses its own tabs freely and
    # never takes over one of his.
    owned_tabs: List[str] = field(default_factory=list)

    def kind_of(self, element_id: str) -> Tuple[str, str]:
        return self.element_kinds.get(str(element_id or ""), ("", ""))

    def own(self, tab_id: str) -> None:
        tid = str(tab_id or "").strip()
        if not tid:
            return
        if tid not in self.owned_tabs:
            self.owned_tabs.append(tid)
        self.tab_id = tid

    def nav_tab(self) -> str:
        """The tab this task may navigate, or "" meaning "open your own"."""
        if self.tab_id and self.tab_id in self.owned_tabs:
            return self.tab_id
        return self.owned_tabs[-1] if self.owned_tabs else ""


# ── handlers ───────────────────────────────────────────────────────────────
def _t_open_url(ctx: ToolContext, url: str = "", new_tab: bool = False) -> ToolResult:
    target = str(url or "").strip()
    if not target:
        return ToolResult(False, "open_url needs a url.")
    if not target.lower().startswith(("http://", "https://")):
        target = "https://" + target.lstrip("/")
    if (ctx.next_read_offset > 0
            and target.rstrip("/") == str(ctx.last_url or "").rstrip("/")):
        return _t_read_page(
            ctx,
            note=(f"(continuing the long page at offset {ctx.next_read_offset} instead of reopening it)"),
            offset=ctx.next_read_offset,
        )
    host = normalise_host(target)
    # Opening Gmail, a bank or an account-settings page is not the same act as
    # opening a news site: it uses the user's signed-in session. Running it under
    # `use_account` is what finally makes the "Use a signed-in account" toggle
    # mean something — before this it was a switch with no action behind it, so
    # turning it off changed nothing at all.
    action = "use_account" if browser_policy.is_sensitive(target) else "open_url"
    # The user's tabs are the user's. A task navigates only in a tab it opened itself —
    # otherwise it replaces whatever he had on screen, usually the very page he
    # is asking about.
    own_tab = ctx.nav_tab()
    in_new_tab = bool(new_tab) or not own_tab
    res = gate.execute(
        action, lambda: browser.open_url(target, tab_id=own_tab, new_tab=in_new_tab,
                                         config=ctx.config),
        task_id=ctx.task_id, scope=host, url=target,
        description=f"Open {host}" + (" — this is a signed-in account" if action == "use_account" else ""),
        config=ctx.config, request_id=ctx.request_id,
    )
    if res.needs_permission:
        return ToolResult(False, f"Waiting on your permission to open {host}.",
                          needs_permission=True, permission_request_id=res.permission_request_id)
    if not res.ok:
        return ToolResult(False, f"Could not open {target}: {_why(res)}")
    ctx.last_url = target
    data = res.data if isinstance(res.data, dict) else {}
    if data.get("id"):
        ctx.own(str(data["id"]))
    # `loadURL` completes before late cookie/consent overlays finish mounting.
    # Give the isolated browser one short settle so the first observation is
    # the requested page rather than its consent shell. This affects only an
    # explicit Supercomputer navigation, never normal chat or Spark.
    browser.wait(_SETTLE_NAV)
    return _t_read_page(ctx, note=f"(opened {host})")


def _t_web_search(ctx: ToolContext, query: str = "") -> ToolResult:
    q = str(query or "").strip()
    if not q:
        return ToolResult(False, "web_search needs a query.")
    url = destinations.search_url(q, destinations.engine_for(ctx.config))
    own_tab = ctx.nav_tab()
    res = gate.execute(
        "web_search", lambda: browser.open_url(url, tab_id=own_tab, new_tab=not own_tab,
                                               config=ctx.config),
        task_id=ctx.task_id, scope=normalise_host(url), url=url,
        description=f"Search for: {q[:80]}", config=ctx.config, request_id=ctx.request_id,
    )
    if not res.ok:
        return ToolResult(False, f"The search did not run: {_why(res)}")
    data = res.data if isinstance(res.data, dict) else {}
    if data.get("id"):
        ctx.own(str(data["id"]))
    # Results pages render their list client-side; a beat here is the difference
    # between reading the results and reading an empty shell.
    browser.wait(_SETTLE_SUBMIT)
    # Say what to do next. A results page is a list of places an answer might
    # be, not the answer — and left to itself a model will happily search again
    # rather than open one. Twelve identical searches, then nothing, is what
    # that looks like in practice.
    return _t_read_page(ctx, note=(
        f"(these are SEARCH RESULTS for: {q}. They are not the answer. "
        f"Pick the most promising link above and open_url it, then read that "
        f"page. Do NOT search again for the same thing.)"))


def _t_read_page(ctx: ToolContext, note: str = "", offset: int = 0) -> ToolResult:
    res = gate.execute(
        "read_page", lambda: browser.read(_active_tab_id(ctx), "digest", offset=offset,
                                            config=ctx.config),
        task_id=ctx.task_id, scope=normalise_host(ctx.last_url) or "page",
        description="Read the page", config=ctx.config, request_id=ctx.request_id,
    )
    if not res.ok:
        return ToolResult(False, f"Could not read the page: {_why(res)}")
    page = res.data if isinstance(res.data, dict) else {}
    # Consent managers often paint their copy first and reveal the document a
    # moment later. A short, cookie-heavy shell is not evidence that the page
    # lacks the requested data, so re-read once after the browser's automatic
    # reject/necessary-only pass. It is deliberately narrow to avoid slowing
    # ordinary page reads.
    _shell = str(page.get("text") or "").lower()
    if (len(_shell) < 1400 and "cookie" in _shell
            and ("we use" in _shell or "privacy" in _shell)):
        browser.wait(1.2)
        retry = gate.execute(
            "read_page",
            lambda: browser.read(_active_tab_id(ctx), "digest", offset=offset,
                                config=ctx.config),
            task_id=ctx.task_id, scope=normalise_host(ctx.last_url) or "page",
            description="Re-read page after consent overlay",
            config=ctx.config, request_id=ctx.request_id,
        )
        if retry.ok and isinstance(retry.data, dict):
            page = retry.data
    ctx.last_url = str(page.get("url") or ctx.last_url)
    meta = page.get("meta") if isinstance(page.get("meta"), dict) else {}
    try:
        start = max(0, int(meta.get("textOffset") or offset or 0))
        window = max(1, int(meta.get("textWindowChars") or 0))
        ctx.next_read_offset = start + window if meta.get("truncated") else 0
    except (TypeError, ValueError):
        ctx.next_read_offset = 0
    ctx.element_labels = {
        str(e.get("id")): str(e.get("label") or "")
        for e in (page.get("elements") or []) if e.get("id")
    }
    ctx.element_kinds = {
        str(e.get("id")): (str(e.get("tag") or ""), str(e.get("role") or ""))
        for e in (page.get("elements") or []) if e.get("id")
    }
    return ToolResult(True, _fmt_page(page, note=note), data={"url": ctx.last_url})


def _t_find(ctx: ToolContext, query: str = "") -> ToolResult:
    """Find something on the page — a control OR a piece of text.

    It used to search only clickable elements, so looking for "Specs" on a spec
    page reported "nothing matches" while the specs were sitting in the body
    text. The agent then concluded the page was empty and, from there, that the
    product did not exist. Text is what people mean most of the time.
    """
    q = str(query or "").strip()
    if not q:
        return ToolResult(False, "find needs something to look for.")
    res = gate.execute(
        "find_element", lambda: browser.find(_active_tab_id(ctx), q, config=ctx.config),
        task_id=ctx.task_id, scope=q[:60],
        description=f"Find '{q}'", config=ctx.config, request_id=ctx.request_id,
    )
    matches = (res.data or {}).get("matches") or [] if res.ok else []
    for m in matches:
        if m.get("id"):
            ctx.element_labels[str(m["id"])] = str(m.get("label") or "")
            ctx.element_kinds[str(m["id"])] = (str(m.get("tag") or ""), "")

    # Then the page's own text, around each hit, so a fact that is written but
    # not clickable is still findable.
    snippets: List[str] = []
    page = browser.read(_active_tab_id(ctx), "text", config=ctx.config)
    if page.get("ok"):
        body = str((page.get("data") or {}).get("text") or "")
        low, needle = body.lower(), q.lower()
        start = 0
        while len(snippets) < 5:
            at = low.find(needle, start)
            if at < 0:
                break
            snippets.append(guard.sanitise(body[max(0, at - 140):at + 220]).replace("\n", " ").strip())
            start = at + max(1, len(needle))

    if not matches and not snippets:
        return ToolResult(False, f"'{q}' does not appear on this page — not as a "
                                 f"control and not in the text. Scroll for more, or "
                                 f"try a different source.")
    out: List[str] = []
    if matches:
        out.append("CONTROLS:")
        out += [f"  [{m.get('id')}] {m.get('tag')}: {str(m.get('label') or '')[:90]}"
                for m in matches]
    if snippets:
        out.append("IN THE PAGE TEXT:")
        out += [f"  …{s}…" for s in snippets]
    return ToolResult(True, "\n".join(out))


def _t_click(ctx: ToolContext, element_id: str = "") -> ToolResult:
    eid = str(element_id or "").strip()
    if not eid:
        return ToolResult(False, "click needs an element_id from the last page read.")
    label = ctx.element_labels.get(eid, "")
    host = normalise_host(ctx.last_url) or "page"

    # What would this click DO? A link is a link; "Send" is a send. The tag and
    # role matter: an email row's label is its whole subject line, and matching
    # verbs against that flagged every message containing "confirm" as a form
    # submission — which blocked opening any email at all.
    tag, role = ctx.kind_of(eid)
    verdict = action_intent.classify_click(label, tag=tag, role=role, host=host)
    action, description = ("click", f"Click '{label[:60] or eid}' on {host}")
    if verdict:
        action, phrase = verdict
        description = f"{phrase.capitalize()} — '{label[:60]}' on {host}"

    res = gate.execute(
        action, lambda: browser.click(_active_tab_id(ctx), eid, config=ctx.config),
        task_id=ctx.task_id, scope=host,
        description=description, config=ctx.config, request_id=ctx.request_id,
        why=(f"The control is labelled '{label[:80]}', which would {verdict[1]}."
             if verdict else ""),
    )
    if res.needs_permission:
        return ToolResult(False, f"Waiting on your permission: {description}.",
                          needs_permission=True, permission_request_id=res.permission_request_id)
    if not res.ok:
        # A live page re-renders between reading it and clicking it — YouTube
        # replaces whole subtrees, taking the element ids with them. A bare
        # "element_not_found" leaves the model guessing and it tends to retry
        # the same dead id, so hand back a FRESH digest with the failure noted:
        # the recovery it needs is new ids, and now it has them.
        if "element_not_found" in str(res.reason or res.message or ""):
            fresh = _t_read_page(ctx, note=(
                f"(the element '{label[:50] or eid}' is gone — the page changed "
                f"since the last read. Pick a NEW id from the list above; do not "
                f"reuse '{eid}'.)"))
            fresh.ok = False
            return fresh
        return ToolResult(False, f"That click did not go through: {_why(res)}")
    browser.wait(_SETTLE_CLICK)
    # A link with target=_blank — which is most headlines, and every "open in a
    # new tab" on a listing — lands in a NEW tab. Reading ctx.tab_id then reads
    # the page the agent just left, so it opened the article and reported that
    # the article was not available. Follow the browser.
    note = f"(clicked '{label[:60] or eid}')"
    moved = _follow_new_tab(ctx)
    if moved:
        note = f"(clicked '{label[:60] or eid}' — it opened in a new tab, now on screen)"
    out = _t_read_page(ctx, note=note)
    out.data["performed_action"] = action
    return out


def _follow_new_tab(ctx: ToolContext) -> bool:
    """Adopt the active tab when an action moved the browser to a new one."""
    res = browser.tabs(config=ctx.config)
    if not res.get("ok"):
        return False
    data = res.get("data") or {}
    active = str(data.get("active") or "")
    if not active or active == ctx.tab_id:
        return False
    # The agent's own click created it, so the agent owns it and may navigate it.
    ctx.own(active)
    for tab in data.get("tabs") or []:
        if str(tab.get("id") or "") == active:
            ctx.last_url = str(tab.get("url") or ctx.last_url)
            break
    return True


def _t_type(ctx: ToolContext, element_id: str = "", text: str = "",
            submit: bool = False) -> ToolResult:
    eid = str(element_id or "").strip()
    if not eid:
        return ToolResult(False, "type_text needs an element_id from the last page read.")
    label = ctx.element_labels.get(eid, "")
    host = normalise_host(ctx.last_url) or "page"

    verdict = action_intent.classify_field(label, submit=bool(submit))
    action = "type_text" if not verdict else verdict[0]
    description = (f"{verdict[1].capitalize()} in '{label[:50]}' on {host}" if verdict
                   else f"Type into '{label[:50] or eid}' on {host}")

    res = gate.execute(
        action,
        lambda: browser.type_text(_active_tab_id(ctx), eid, str(text or ""),
                                  submit=bool(submit), config=ctx.config),
        task_id=ctx.task_id, scope=host, description=description,
        config=ctx.config, request_id=ctx.request_id,
    )
    if res.needs_permission:
        return ToolResult(False, f"Waiting on your permission: {description}.",
                          needs_permission=True, permission_request_id=res.permission_request_id)
    if not res.ok:
        if "element_not_found" in str(res.reason or res.message or ""):
            fresh = _t_read_page(ctx, note=(
                f"(the field '{label[:50] or eid}' is gone — the page changed. "
                f"Pick a NEW id from the list above.)"))
            fresh.ok = False
            return fresh
        return ToolResult(False, f"Could not type there: {_why(res)}")
    browser.wait(_SETTLE_SUBMIT if submit else _SETTLE_TYPE)
    out = _t_read_page(ctx, note=f"(typed into '{label[:50] or eid}'"
                                 f"{' and submitted' if submit else ''})")
    out.data["performed_action"] = action
    return out


def _t_scroll(ctx: ToolContext, dy: int = 700) -> ToolResult:
    # The digest is a document-level clone, so a physical scroll used to return
    # character zero again on long static pages.  When a text window remains,
    # interpret the agent's natural "scroll for more" action as the next
    # readable window; interactive/infinite-scroll pages still use the real
    # browser scroll once the current document has been fully consumed.
    if ctx.next_read_offset > 0:
        return _t_read_page(
            ctx,
            note=f"(continued the long page at offset {ctx.next_read_offset})",
            offset=ctx.next_read_offset,
        )
    res = gate.execute(
        "scroll", lambda: browser.scroll(_active_tab_id(ctx), int(dy), config=ctx.config),
        task_id=ctx.task_id, scope=normalise_host(ctx.last_url) or "page",
        description="Scroll", config=ctx.config, request_id=ctx.request_id,
    )
    if not res.ok:
        return ToolResult(False, f"Could not scroll: {_why(res)}")
    browser.wait(_SETTLE_SCROLL)
    return _t_read_page(ctx, note="(scrolled)")


def _t_back(ctx: ToolContext) -> ToolResult:
    res = gate.execute(
        "back", lambda: browser.back(_active_tab_id(ctx), config=ctx.config),
        task_id=ctx.task_id, scope=normalise_host(ctx.last_url) or "page",
        description="Go back", config=ctx.config, request_id=ctx.request_id,
    )
    if not res.ok:
        return ToolResult(False, f"Could not go back: {_why(res)}")
    browser.wait(_SETTLE_NAV)
    return _t_read_page(ctx, note="(went back)")


def _t_new_tab(ctx: ToolContext, url: str = "") -> ToolResult:
    if url:
        return _t_open_url(ctx, url=url, new_tab=True)
    res = gate.execute(
        "new_tab", lambda: browser.new_tab(config=ctx.config),
        task_id=ctx.task_id, scope="browser", description="Open a new tab",
        config=ctx.config, request_id=ctx.request_id,
    )
    if not res.ok:
        return ToolResult(False, f"Could not open a tab: {_why(res)}")
    tid = str((res.data or {}).get("id") or "")
    if tid:
        ctx.own(tid)
    return ToolResult(True, f"Opened a new tab ({tid or 'active'}).")


def _t_list_tabs(ctx: ToolContext) -> ToolResult:
    res = gate.execute(
        "read_page", lambda: browser.tabs(config=ctx.config),
        task_id=ctx.task_id, scope="browser", description="List tabs",
        config=ctx.config, request_id=ctx.request_id,
    )
    if not res.ok:
        return ToolResult(False, f"Could not list tabs: {_why(res)}")
    tabs = (res.data or {}).get("tabs") or []
    if not tabs:
        return ToolResult(True, "No tabs are open.")
    lines = [f"  [{t.get('id')}] {str(t.get('title') or '')[:60]} — {t.get('url')}" for t in tabs]
    return ToolResult(True, "OPEN TABS:\n" + "\n".join(lines))


def _t_switch_tab(ctx: ToolContext, tab_id: str = "") -> ToolResult:
    tid = str(tab_id or "").strip()
    if not tid:
        return ToolResult(False, "switch_tab needs a tab_id from list_tabs.")
    res = gate.execute(
        "switch_tab", lambda: browser.focus(tid, config=ctx.config),
        task_id=ctx.task_id, scope="browser", description=f"Switch to tab {tid}",
        config=ctx.config, request_id=ctx.request_id,
    )
    if not res.ok:
        return ToolResult(False, f"Could not switch tab: {_why(res)}")
    ctx.tab_id = tid
    return _t_read_page(ctx, note=f"(switched to tab {tid})")


def _t_screenshot(ctx: ToolContext, question: str = "") -> ToolResult:
    """Look at the page with the vision model rather than through the DOM.

    Only worth it when the DOM genuinely cannot answer — a chart, a canvas, an
    image. Without a vision model configured this says so plainly instead of
    pretending to have looked.
    """
    from . import models

    res = gate.execute(
        "screenshot", lambda: browser.screenshot(_active_tab_id(ctx), config=ctx.config),
        task_id=ctx.task_id, scope=normalise_host(ctx.last_url) or "page",
        description="Capture the page", config=ctx.config, request_id=ctx.request_id,
    )
    if not res.ok:
        return ToolResult(False, f"Could not capture the page: {_why(res)}")
    png = str((res.data or {}).get("png_base64") or "")
    if not png:
        return ToolResult(False, "The capture came back empty.")
    results.screenshot(ctx.task_id, f"Screenshot of {normalise_host(ctx.last_url) or 'the page'}",
                       location=ctx.last_url)
    if not models.model_for(models.ROLE_VISION, ctx.config):
        return ToolResult(
            True,
            "Captured the page, but no vision model is configured, so I cannot describe "
            "the image. Read the page text instead, or set a vision model in "
            "Supercomputer settings.",
        )
    described, _ = models.vision(
        str(question or "Describe what is shown on this page, precisely and factually."),
        png, config=ctx.config,
    )
    if not described:
        return ToolResult(True, "Captured the page, but the vision model returned nothing.")
    # A description of a page is still page-derived: text rendered as an image
    # is a known way to smuggle instructions past a DOM-only reader.
    return ToolResult(True, guard.wrap(described, url=ctx.last_url, title="screenshot description"))


def _t_create_document(ctx: ToolContext, name: str = "", format: str = "md",
                       content: Any = "", title: str = "", subtitle: str = "",
                       template: str = "", theme: str = "", photo: str = "") -> ToolResult:
    if not str(name or "").strip():
        return ToolResult(False, "create_document needs a name.")

    # A photo comes from the user's own folders and therefore through the same
    # allowlist and card as any other file of his — a document must not become
    # a side door into the filesystem.
    photo_path = ""
    if str(photo or "").strip():
        from . import local_files

        resolved, reason = local_files.attachable(str(photo), ctx.config)
        if resolved is None:
            return ToolResult(False, f"I could not use that image: "
                                     f"{local_files.describe_reason(reason, ctx.config)}.")
        picture = gate.execute(
            "file_read", lambda: {"ok": True, "data": {"path": resolved}},
            task_id=ctx.task_id, scope=os.path.basename(resolved),
            description=f"Use {os.path.basename(resolved)} in {name}",
            why="This puts your own picture into the document.",
            config=ctx.config, request_id=ctx.request_id,
        )
        if picture.needs_permission:
            return ToolResult(False, f"Waiting on your permission to use "
                                     f"{os.path.basename(resolved)} in the document.",
                              needs_permission=True,
                              permission_request_id=picture.permission_request_id)
        if not picture.ok:
            return ToolResult(False, f"I could not use that image: {_why(picture)}")
        photo_path = resolved

    res = gate.execute(
        "create_document",
        lambda: documents.create(ctx.task_id, str(name), str(format or "md"), content,
                                 title=str(title or name), subtitle=str(subtitle or ""),
                                 template=str(template or ""), theme=str(theme or ""),
                                 photo=photo_path, config=ctx.config),
        task_id=ctx.task_id, scope=str(name)[:60],
        description=f"Create {documents.describe(format)}: {name}",
        config=ctx.config, request_id=ctx.request_id, timeout_s=180.0,
    )
    if not res.ok:
        return ToolResult(False, f"Could not create that document: {_why(res)}")
    data = res.data if isinstance(res.data, dict) else {}
    path = str(data.get("path") or "")
    rel = documents.filename_for(str(name), documents.normalise_format(format))
    # Record the workspace-relative name too, so the Results row can open the
    # file in the browser. A document the user cannot look at before deciding what
    # to do with it is not much of a deliverable.
    created = results.file_created(ctx.task_id, path, detail=documents.describe(format))
    created.meta["relative"] = rel
    chosen = ""
    if data.get("template"):
        chosen = f" Layout: {data.get('template')}, colour: {data.get('theme')}."
    return ToolResult(True, f"Created {documents.describe(format)} at {path} "
                            f"({data.get('bytes', 0)} bytes).{chosen} "
                            f"Now call preview_document('{rel}') so the user can SEE it "
                            f"before you save it anywhere.",
                      data={"path": path, "relative": rel})


def _t_preview_document(ctx: ToolContext, file: str = "") -> ToolResult:
    """Put a document on screen so the user can look at it before it is saved.

    This is the half of the flow that was missing. The agent could write a CV
    and then ask to export it, and the first time the user saw the thing was after
    it landed in Downloads — by which point "use a different template" means
    starting again. Now the document appears in the browser beside the chat, and
    the export card that follows is a decision made while looking at it.

    The three buttons on that card already mean the right things:
    Allow = save it, "Chat about this" = change something, Decline = bin it.
    """
    from . import preview

    rel = str(file or "").strip()
    if not rel:
        return ToolResult(False, "preview_document needs the file you made.")
    # PDFs and HTML render in the browser; a spreadsheet does not, so say so
    # rather than opening a download prompt over the workspace.
    viewable = rel.lower().endswith((".pdf", ".html", ".htm", ".png", ".jpg", ".jpeg", ".txt", ".md"))

    def _show() -> Dict[str, Any]:
        started = preview.start(ctx.task_id)
        if not started.get("ok"):
            return {"ok": False, "reason": f"preview_unavailable:{started.get('reason')}"}
        base = str((started.get("data") or {}).get("url") or "").rstrip("/")
        return browser.open_url(f"{base}/{rel}", new_tab=True, config=ctx.config)

    if not viewable:
        return ToolResult(True, f"{rel} is not something a browser can display. "
                                f"Tell the user what is in it, then use save_to_computer.")
    res = gate.execute(
        "preview_document", _show,
        task_id=ctx.task_id, scope=rel[:60], description=f"Show {rel} to the user",
        config=ctx.config, request_id=ctx.request_id, timeout_s=60.0,
    )
    if not res.ok:
        return ToolResult(False, f"Could not show that: {_why(res)}")
    data = res.data if isinstance(res.data, dict) else {}
    if data.get("id"):
        ctx.own(str(data["id"]))
    return ToolResult(True, f"{rel} is now on screen in the browser — the user can see it. "
                            f"Tell him what you made and what layout and colour you "
                            f"chose, then call save_to_computer to put it in his "
                            f"Downloads. He can answer that card with Allow to save it, "
                            f"“Chat about this” to change something, or Decline.",
                      data={"relative": rel})




def _t_write_file(ctx: ToolContext, path: str = "", content: str = "") -> ToolResult:
    if not str(path or "").strip():
        return ToolResult(False, "write_file needs a path.")
    res = gate.execute(
        "workspace_write", lambda: workspace.write(ctx.task_id, str(path), str(content or "")),
        task_id=ctx.task_id, scope=str(path)[:60], description=f"Write {path}",
        config=ctx.config, request_id=ctx.request_id,
    )
    if not res.ok:
        return ToolResult(False, f"Could not write {path}: {_why(res)}")
    data = res.data if isinstance(res.data, dict) else {}
    results.code_change(ctx.task_id, str(path), added=int(data.get("lines") or 0),
                        detail=f"{data.get('bytes')} bytes")
    created = results.file_created(ctx.task_id, str(data.get("path") or path))
    created.meta["relative"] = str(path)
    return ToolResult(True, f"Wrote {path} ({data.get('lines', 0)} lines).",
                      data={"path": data.get("path", "")})


def _t_read_file(ctx: ToolContext, path: str = "") -> ToolResult:
    res = gate.execute(
        "workspace_read", lambda: workspace.read(ctx.task_id, str(path or "")),
        task_id=ctx.task_id, scope=str(path)[:60], description=f"Read {path}",
        config=ctx.config, request_id=ctx.request_id,
    )
    if not res.ok:
        return ToolResult(False, f"Could not read {path}: {_why(res)}")
    content = str((res.data or {}).get("content") or "")
    return ToolResult(True, f"{path}:\n{content[:OBS_TEXT_CHARS]}")


def _t_list_files(ctx: ToolContext) -> ToolResult:
    res = gate.execute(
        "workspace_list", lambda: {"ok": True, "data": {"files": workspace.listing(ctx.task_id)}},
        task_id=ctx.task_id, scope="workspace", description="List workspace files",
        config=ctx.config, request_id=ctx.request_id,
    )
    files = (res.data or {}).get("files") or []
    if not files:
        return ToolResult(True, "The workspace is empty.")
    return ToolResult(True, "WORKSPACE FILES:\n"
                      + "\n".join(f"  {f['relative']} ({f['bytes']} bytes)" for f in files))


def _t_preview(ctx: ToolContext) -> ToolResult:
    from . import preview

    res = gate.execute(
        "start_preview", lambda: preview.start(ctx.task_id),
        task_id=ctx.task_id, scope="workspace", description="Start the local preview",
        config=ctx.config, request_id=ctx.request_id,
    )
    if not res.ok:
        return ToolResult(False, f"Could not start the preview: {_why(res)}")
    url = str((res.data or {}).get("url") or "")
    opened = browser.open_url(url, new_tab=True, config=ctx.config)
    if not opened.get("ok"):
        return ToolResult(True, f"Preview is running at {url}, but I could not open it "
                                f"({opened.get('reason')}).", data={"url": url})
    ctx.last_url = url
    if (opened.get("data") or {}).get("id"):
        ctx.own(str(opened["data"]["id"]))
    page = _t_read_page(ctx, note=f"(opened the local preview at {url})")
    # Carry the url out so the report can offer it — agent.format_report has
    # rendered a "**Preview:**" line since Phase 7 and never had one to show.
    page.data = dict(page.data or {}, url=url)
    return page


_UNSAFE_DOWNLOAD_RE = re.compile(
    r"\.(exe|msi|bat|cmd|com|scr|cpl|ps1|psm1|vbs|vbe|js|jse|wsf|wsh|hta|dll|sys"
    r"|reg|inf|lnk|jar|apk|app|dmg|pkg|deb|rpm)(\?|$)", re.IGNORECASE)


def _t_download(ctx: ToolContext, url: str = "", why: str = "") -> ToolResult:
    """Save a file from the web onto this machine.

    Three checks before anything is armed, then a card, then a single-use
    arming that the browser consumes on the next download and never again:

    1. the address must survive transport policy (https, public host, no
       embedded credentials) — the same rules as any navigation;
    2. the file must not be executable, whatever the user approves. "Yes, save that
       PDF" is not consent to save an installer, so this is checked here on the
       URL and again in the browser on the real filename, which is the only
       place it is actually known;
    3. it lands in the task's working folder — the one the user chose, if he chose
       one — never anywhere the agent picked for itself.
    """
    from . import paths

    target = str(url or "").strip()
    if not target:
        return ToolResult(False, "download needs a url.")
    if not target.lower().startswith(("http://", "https://")):
        target = "https://" + target.lstrip("/")

    ok, reason = browser_policy.check_url(target)
    if not ok:
        return ToolResult(False, f"I will not download from that address: "
                                 f"{browser_policy.describe_block(reason)}.")
    if _UNSAFE_DOWNLOAD_RE.search(target):
        return ToolResult(False, "That is a program, not a document. I do not "
                                 "download executables — no approval changes that.")

    host = normalise_host(target)
    folder = paths.task_workspace(ctx.task_id)
    res = gate.execute(
        "download", lambda: browser.arm_download(folder, config=ctx.config),
        task_id=ctx.task_id, scope=host, url=target,
        description=f"Download a file from {host} into {folder}",
        why=str(why or "") or f"It will be saved into this task's folder, not run.",
        config=ctx.config, request_id=ctx.request_id,
    )
    if res.needs_permission:
        return ToolResult(False, f"Waiting on your permission to download from {host}.",
                          needs_permission=True,
                          permission_request_id=res.permission_request_id)
    if not res.ok:
        return ToolResult(False, f"The download was not allowed: {_why(res)}")

    opened = browser.open_url(target, new_tab=True, config=ctx.config)
    browser.wait(2.0)
    status = browser.download_status(config=ctx.config)
    data = (status.get("data") or {}) if status.get("ok") else {}
    if data.get("ok"):
        path = str(data.get("path") or "")
        results.file_created(ctx.task_id, path, detail=f"downloaded from {host}")
        return ToolResult(True, f"Saved {data.get('name')} to {path}.", data={"path": path})
    if data.get("reason") == "unsafe_file_type":
        return ToolResult(False, f"That turned out to be an executable "
                                 f"({data.get('name')}), so I refused it.")
    return ToolResult(False, f"The download did not complete "
                             f"({data.get('reason') or opened.get('reason') or 'no response'}).")


def _t_save_to_computer(ctx: ToolContext, file: str = "", to: str = "downloads") -> ToolResult:
    """Copy something SYRIX made out of the sandbox and onto the machine.

    The card comes first (``export`` is side-effecting), then the copy — and the
    copy itself is narrow: out of this task's workspace only, into Downloads or
    the working folder only, never over an existing file.
    """
    from . import export as _export

    rel = str(file or "").strip()
    if not rel:
        return ToolResult(False, "save_to_computer needs the name of a file you made.")
    dest = str(to or _export.DEST_DOWNLOADS).strip().lower()
    folder, why = _export.resolve_destination(dest)
    if folder is None:
        known = ", ".join(_export.destinations())
        return ToolResult(False, f"I cannot save there ({why}). Choose one of: {known}.")

    res = gate.execute(
        "export", lambda: _export.save(ctx.task_id, rel, to=dest),
        task_id=ctx.task_id, scope=folder,
        description=f"Save {rel} to {folder}",
        why="This copies the file out of my working area and onto your machine, "
            "where it stays after this task ends.",
        config=ctx.config, request_id=ctx.request_id,
    )
    if res.needs_permission:
        return ToolResult(False, f"Waiting on your permission to save {rel} to {folder}.",
                          needs_permission=True,
                          permission_request_id=res.permission_request_id)
    if not res.ok:
        if res.reason == "refused_executable":
            return ToolResult(False, "That is a program, not a document. I do not "
                                     "save executables onto your machine.")
        return ToolResult(False, f"I could not save that: {_why(res)}")
    data = res.data if isinstance(res.data, dict) else {}
    path = str(data.get("path") or "")
    results.file_created(ctx.task_id, path, detail="saved to your computer")
    return ToolResult(True, f"Saved to {path}.", data={"path": path})


def _t_press_key(ctx: ToolContext, keys: str = "") -> ToolResult:
    combo = str(keys or "").strip()
    if not combo:
        return ToolResult(False, "press_key needs a key, like 'Enter' or 'Ctrl+F'.")
    res = gate.execute(
        "press_key",
        lambda: browser.press_key(_active_tab_id(ctx), combo, config=ctx.config),
        task_id=ctx.task_id, scope=combo[:40], description=f"Press {combo}",
        config=ctx.config, request_id=ctx.request_id,
    )
    if res.needs_permission:
        return ToolResult(False, f"Waiting on your permission to press {combo}.",
                          needs_permission=True, permission_request_id=res.permission_request_id)
    if not res.ok:
        return ToolResult(False, f"Could not press {combo}: {_why(res)}")
    browser.wait(_SETTLE_CLICK)
    return _t_read_page(ctx, note=f"(pressed {combo})")


def _t_hover(ctx: ToolContext, element_id: str = "") -> ToolResult:
    eid = str(element_id or "").strip()
    if not eid:
        return ToolResult(False, "hover needs an element_id from the last page read.")
    res = gate.execute(
        "hover", lambda: browser.hover(_active_tab_id(ctx), eid, config=ctx.config),
        task_id=ctx.task_id, scope=ctx.element_labels.get(eid, eid)[:60],
        description=f"Hover over {ctx.element_labels.get(eid, eid)[:60]}",
        config=ctx.config, request_id=ctx.request_id,
    )
    if not res.ok:
        return ToolResult(False, f"Could not hover there: {_why(res)}")
    browser.wait(_SETTLE_CLICK)
    return _t_read_page(ctx, note="(hovered — a menu may have opened)")


def _t_select_option(ctx: ToolContext, element_id: str = "", value: str = "") -> ToolResult:
    eid = str(element_id or "").strip()
    want = str(value or "").strip()
    if not eid or not want:
        return ToolResult(False, "select_option needs an element_id and a value.")
    res = gate.execute(
        "select_option",
        lambda: browser.select_option(_active_tab_id(ctx), eid, want, config=ctx.config),
        task_id=ctx.task_id, scope=want[:60],
        description=f"Choose “{want}” in {ctx.element_labels.get(eid, eid)[:40]}",
        config=ctx.config, request_id=ctx.request_id,
    )
    if not res.ok:
        options = ((res.data or {}).get("options") or []) if isinstance(res.data, dict) else []
        if options:
            return ToolResult(False, f"There is no “{want}” in that dropdown. It offers: "
                                     + ", ".join(str(o) for o in options[:16]))
        return ToolResult(False, f"Could not choose that: {_why(res)}")
    browser.wait(_SETTLE_SUBMIT)
    chose = str((res.data or {}).get("chose") or want)
    return _t_read_page(ctx, note=f"(chose {chose})")


def _t_drag(ctx: ToolContext, from_element: str = "", to_element: str = "",
            from_x: Any = None, from_y: Any = None,
            to_x: Any = None, to_y: Any = None) -> ToolResult:
    """Drag between two elements, or between two points on the page.

    Coordinates exist for canvases — a trend line or a fib retracement is drawn
    across a chart that has no elements to aim at, only pixels.
    """
    def _pt(x: Any, y: Any) -> Optional[Dict[str, Any]]:
        try:
            if x is None or y is None:
                return None
            return {"x": int(float(x)), "y": int(float(y))}
        except (TypeError, ValueError):
            return None

    start, end = _pt(from_x, from_y), _pt(to_x, to_y)
    if not (from_element or start) or not (to_element or end):
        return ToolResult(False, "drag needs a start and an end: either "
                                 "from_element/to_element, or from_x,from_y and to_x,to_y.")
    res = gate.execute(
        "drag",
        lambda: browser.drag(_active_tab_id(ctx), from_element=str(from_element or ""),
                             to_element=str(to_element or ""), from_xy=start, to_xy=end,
                             config=ctx.config),
        task_id=ctx.task_id, scope="drag",
        description="Drag on the page", config=ctx.config, request_id=ctx.request_id,
    )
    if not res.ok:
        return ToolResult(False, f"Could not drag: {_why(res)}")
    browser.wait(_SETTLE_SUBMIT)
    return _t_read_page(ctx, note="(dragged)")


def _t_wait_for(ctx: ToolContext, text: str = "", element_id: str = "",
                seconds: Any = 15) -> ToolResult:
    try:
        budget = max(1.0, min(60.0, float(seconds)))
    except (TypeError, ValueError):
        budget = 15.0
    res = gate.execute(
        "wait_for",
        lambda: browser.wait_for(_active_tab_id(ctx), text=str(text or ""),
                                 element_id=str(element_id or ""), timeout_s=budget,
                                 config=ctx.config),
        task_id=ctx.task_id, scope=(text or element_id or "load")[:60],
        description="Wait for the page to be ready",
        config=ctx.config, request_id=ctx.request_id, timeout_s=budget + 15.0,
    )
    if not res.ok:
        # A timeout here is information, not a failure to hide: read anyway and
        # let the agent see what DID load.
        page = _t_read_page(ctx, note=f"(waited {budget:.0f}s and it did not appear)")
        return ToolResult(page.ok, page.observation, data=page.data)
    return _t_read_page(ctx, note="(the page is ready)")


def _t_go_forward(ctx: ToolContext) -> ToolResult:
    res = gate.execute(
        "forward", lambda: browser.forward(_active_tab_id(ctx), config=ctx.config),
        task_id=ctx.task_id, scope="history", description="Go forward",
        config=ctx.config, request_id=ctx.request_id,
    )
    if not res.ok:
        return ToolResult(False, f"Could not go forward: {_why(res)}")
    browser.wait(_SETTLE_NAV)
    return _t_read_page(ctx, note="(went forward)")


def _t_reload(ctx: ToolContext) -> ToolResult:
    res = gate.execute(
        "reload", lambda: browser.reload(_active_tab_id(ctx), config=ctx.config),
        task_id=ctx.task_id, scope="page", description="Reload the page",
        config=ctx.config, request_id=ctx.request_id,
    )
    if not res.ok:
        return ToolResult(False, f"Could not reload: {_why(res)}")
    browser.wait(_SETTLE_NAV)
    return _t_read_page(ctx, note="(reloaded)")


def _t_close_tab(ctx: ToolContext, tab_id: str = "") -> ToolResult:
    target = str(tab_id or "").strip() or _active_tab_id(ctx)
    res = gate.execute(
        "close_tab", lambda: browser.close(target, config=ctx.config),
        task_id=ctx.task_id, scope=target, description="Close a tab",
        config=ctx.config, request_id=ctx.request_id,
    )
    if not res.ok:
        return ToolResult(False, f"Could not close that tab: {_why(res)}")
    if target in ctx.owned_tabs:
        ctx.owned_tabs.remove(target)
    if target == ctx.tab_id:
        ctx.tab_id = ""
    return ToolResult(True, "Closed that tab.")


# ── the user's own files ───────────────────────────────────────────────────────
def _t_list_local_folder(ctx: ToolContext, folder: str = "") -> ToolResult:
    from . import local_files

    name = str(folder or "").strip()
    res = gate.execute(
        "folder_list", lambda: local_files.list_folder(name, ctx.config),
        task_id=ctx.task_id, scope=name or "my folders",
        description=f"List {name or 'your readable folders'}",
        why="This looks at the names of your files. It does not open them.",
        config=ctx.config, request_id=ctx.request_id,
    )
    if res.needs_permission:
        return ToolResult(False, f"Waiting on your permission to look in {name or 'your folders'}.",
                          needs_permission=True, permission_request_id=res.permission_request_id)
    if not res.ok:
        return ToolResult(False, f"I could not list that: "
                                 f"{local_files.describe_reason(res.reason, ctx.config)}.")
    data = res.data if isinstance(res.data, dict) else {}
    entries = data.get("entries") or []
    if not entries:
        return ToolResult(True, f"{data.get('folder') or 'That folder'} is empty.")
    lines = []
    for entry in entries[:60]:
        if entry.get("is_dir"):
            lines.append(f"  [dir] {entry.get('name')}")
        else:
            lines.append(f"  {entry.get('name')}  ({entry.get('bytes', 0)} bytes)")
    head = f"FILES IN {data.get('folder') or 'your folders'} (newest first):"
    return ToolResult(True, head + "\n" + "\n".join(lines), data={"entries": entries})


def _t_read_local_file(ctx: ToolContext, path: str = "") -> ToolResult:
    from . import local_files

    target = str(path or "").strip()
    if not target:
        return ToolResult(False, "read_local_file needs a file name or path.")
    res = gate.execute(
        "file_read", lambda: local_files.read_file(target, ctx.config),
        task_id=ctx.task_id, scope=target[:80],
        description=f"Read {target}",
        why="This opens one of your own files and reads what is in it.",
        config=ctx.config, request_id=ctx.request_id, timeout_s=90.0,
    )
    if res.needs_permission:
        return ToolResult(False, f"Waiting on your permission to read {target}.",
                          needs_permission=True, permission_request_id=res.permission_request_id)
    if not res.ok:
        return ToolResult(False, f"I could not read that: "
                                 f"{local_files.describe_reason(res.reason, ctx.config)}.")
    data = res.data if isinstance(res.data, dict) else {}
    body = str(data.get("text") or "")
    note = " (truncated)" if data.get("truncated") else ""
    # A document the user owns is still DATA. It can carry instructions written by
    # whoever produced it, so it is fenced exactly like a web page.
    return ToolResult(True, guard.wrap(body, title=f"YOUR FILE: {data.get('path')}{note}"),
                      data={"path": data.get("path", "")})


def _t_attach_file(ctx: ToolContext, element_id: str = "", path: str = "") -> ToolResult:
    """Put one of the user's files into a file input on the page.

    Always asks — ``upload`` is in :data:`safety_mode.ALWAYS_ASK`, so this stops
    for a card in every safety mode including "don't ask". The card names the
    exact file and the exact site, because this is the moment something of his
    leaves the machine.
    """
    from . import local_files

    eid = str(element_id or "").strip()
    target = str(path or "").strip()
    if not eid or not target:
        return ToolResult(False, "attach_file needs the file input's element_id and a file path.")
    resolved, reason = local_files.attachable(target, ctx.config)
    if resolved is None:
        return ToolResult(False, f"I will not attach that: "
                                 f"{local_files.describe_reason(reason, ctx.config)}.")
    host = normalise_host(ctx.last_url) or "this site"
    res = gate.execute(
        "upload",
        lambda: browser.attach_file(_active_tab_id(ctx), eid, resolved, config=ctx.config),
        task_id=ctx.task_id, scope=f"{os.path.basename(resolved)} → {host}",
        description=f"Attach {os.path.basename(resolved)} to the form on {host}",
        why=f"This sends a copy of {resolved} to {host}. It leaves your machine.",
        config=ctx.config, request_id=ctx.request_id,
    )
    if res.needs_permission:
        return ToolResult(False, f"Waiting on your permission to attach "
                                 f"{os.path.basename(resolved)} to {host}.",
                          needs_permission=True, permission_request_id=res.permission_request_id)
    if not res.ok:
        return ToolResult(False, f"Could not attach that file: {_why(res)}")
    return ToolResult(True, f"Attached {os.path.basename(resolved)} to the form. "
                            f"It is not submitted — that is a separate step, and it asks first.",
                      data={"path": resolved})


# ── computation ────────────────────────────────────────────────────────────
def _t_run_python(ctx: ToolContext, code: str = "", why: str = "") -> ToolResult:
    from . import sandbox_exec

    body = str(code or "").strip()
    if not body:
        return ToolResult(False, "run_python needs some code.")
    res = gate.execute(
        "run_python", lambda: sandbox_exec.run(ctx.task_id, body, config=ctx.config),
        task_id=ctx.task_id, scope=(why or "calculation")[:60],
        description=f"Run a calculation: {why or 'compute'}",
        config=ctx.config, request_id=ctx.request_id,
        timeout_s=float(ctx.config.get("supercomputer_sandbox_timeout_s", 30.0)) + 20.0,
    )
    data = res.data if isinstance(res.data, dict) else {}
    made = [str(p) for p in (data.get("files") or [])]
    for rel in made:
        with obs.swallow("supercomputer.tools.run_python_result", path=rel):
            full = workspace.resolve(ctx.task_id, rel)[0]
            if full:
                results.file_created(ctx.task_id, full, detail="made by a calculation")
    if not res.ok:
        detail = str(data.get("stderr") or "").strip()
        return ToolResult(False,
                          f"That code did not run: {sandbox_exec.describe_reason(res.reason)}."
                          + (f"\n{detail[-800:]}" if detail else ""),
                          data={"paths": made})
    out = str(data.get("stdout") or "").strip()
    parts = [out or "(it ran and printed nothing)"]
    if made:
        parts.append("New files: " + ", ".join(made))
    return ToolResult(True, "RESULT OF YOUR CODE:\n" + "\n".join(parts),
                      data={"paths": made})


# ── the last rung of the ladder ────────────────────────────────────────────
_ASSISTANTS = (
    ("ChatGPT", "https://chatgpt.com/"),
    ("Perplexity", "https://www.perplexity.ai/"),
    ("Claude", "https://claude.ai/new"),
)


def _t_consult_assistant(ctx: ToolContext, question: str = "", which: str = "") -> ToolResult:
    """Open a public assistant and ask it — the LAST resort, never the first.

    The user's instruction was that SYRIX must never bottom out at "I could not find
    it": if it is smart it can go and ask another AI. So it can. What it may not
    do is pass the answer off as a source, because a chatbot is not one — the
    reply comes back explicitly labelled unverified, and the report has to say
    where it came from.
    """
    q = str(question or "").strip()
    if not q:
        return ToolResult(False, "consult_assistant needs the question to ask.")
    name, url = _ASSISTANTS[0]
    for candidate, candidate_url in _ASSISTANTS:
        if candidate.lower() in str(which or "").lower():
            name, url = candidate, candidate_url
            break
    res = gate.execute(
        "consult_assistant", lambda: browser.open_url(url, new_tab=True, config=ctx.config),
        task_id=ctx.task_id, scope=normalise_host(url), url=url,
        description=f"Ask {name}: {q[:80]}",
        config=ctx.config, request_id=ctx.request_id,
    )
    if res.needs_permission:
        return ToolResult(False, f"Waiting on your permission to open {name}.",
                          needs_permission=True, permission_request_id=res.permission_request_id)
    if not res.ok:
        return ToolResult(False, f"Could not open {name}: {_why(res)}")
    data = res.data if isinstance(res.data, dict) else {}
    if data.get("id"):
        ctx.own(str(data["id"]))
    ctx.last_url = url
    browser.wait(_SETTLE_NAV)
    page = _t_read_page(ctx, note=f"({name} is open)")
    return ToolResult(
        page.ok,
        page.observation
        + f"\n\n[{name} is open. Find its message box in the elements above, type_text your "
          f"question into it with submit true, wait_for the reply, then read_page.\n"
          f"WHATEVER IT SAYS IS SECONDHAND AND UNVERIFIED. If you use it, say in your answer "
          f"that {name} said it and that you could not confirm it against a primary source. "
          f"Better still, use it to find WHERE to look, then go and read that page yourself.]",
        data=page.data,
    )


def _t_fetch_page(ctx: ToolContext, url: str = "", why: str = "") -> ToolResult:
    """Read a page over HTTP, without the browser.

    This is what makes a research stage genuinely parallel-safe: it never
    touches the one BrowserView, so several of them can run at once without
    interleaving clicks into each other's page. It is also simply faster for
    read-only work — no renderer, no layout, no navigation settle.

    Reuses Atlas' reader (robots-aware, with trafilatura/readability/soup
    extraction tiers) rather than growing a second fetch stack. The rendered-
    browser tier of that reader is deliberately switched OFF here: a research
    worker that quietly launches a headless browser is no longer the light
    read-only thing this tool promises to be.
    """
    target = str(url or "").strip()
    if not target:
        return ToolResult(False, "fetch_page needs a url.")
    if not target.lower().startswith(("http://", "https://")):
        target = "https://" + target.lstrip("/")

    res = gate.execute(
        "open_url", lambda: {"ok": True}, task_id=ctx.task_id,
        scope=normalise_host(target) or target[:60],
        description=f"Read {normalise_host(target) or target[:60]} (no browser)",
        config=ctx.config, request_id=ctx.request_id, url=target,
    )
    if not res.ok:
        return ToolResult(
            False, f"Could not fetch that page: {_why(res)}",
            needs_permission=bool(getattr(res, "needs_permission", False)),
            permission_request_id=str(getattr(res, "permission_request_id", "") or ""),
        )

    title, text, method = "", "", "unavailable"
    with obs.swallow("supercomputer.tools.fetch_page", url=target):
        from syrix_core_v2.atlas import reader as _reader

        cfg = dict(ctx.config or {})
        cfg["atlas_browser_enabled"] = False
        title, text, method = _reader.read_page(target, cfg=cfg, timeout_s=12.0)

    if not str(text or "").strip():
        # 2026-07-28 — plain HTTP got nothing, so this tool was about to fail.
        # Only now is a headless render worth its cost, and only because the
        # alternative is telling the user no about a page that is perfectly
        # readable. Escalating here keeps the promise in the docstring above:
        # the light path stays light, and the browser is the last thing tried,
        # never the first. It reads server-side and never touches the one
        # BrowserView, so parallel research stages stay safe.
        with obs.swallow("supercomputer.tools.fetch_page_gateway", url=target):
            from syrix_web import gateway as _gateway

            rendered = _gateway.read(
                target, mode="supercomputer", config=ctx.config,
                request_id=ctx.request_id,
            )
            if rendered.usable():
                title = rendered.title or title
                text = rendered.text
                method = rendered.method or "browser"

    if not str(text or "").strip():
        return ToolResult(False, (
            f"Nothing came back from {target} over plain HTTP ({method}). If the "
            f"page needs a real browser, use open_url instead."))

    ctx.last_url = target
    results.source(ctx.task_id, str(title or target)[:120], target)
    prepared = guard.prepare({"text": text, "url": target, "title": title})
    body = str(prepared.get("text") or text)
    lines = [f"PAGE: {title or '(untitled)'}", f"URL: {target}",
             f"(fetched over HTTP, no browser — {method})"]
    quirk = _site_note(target, text)
    if quirk:
        lines.append("\n" + quirk)
    if prepared.get("suspicious"):
        lines.append("\nNOTE: " + guard.describe(prepared["findings"]))
    lines.append("\nPAGE TEXT:\n" + body[:OBS_TEXT_CHARS])
    if len(body) > OBS_TEXT_CHARS:
        lines.append("[text truncated]")
    return ToolResult(True, "\n".join(lines), data={"url": target, "method": method})


def _t_note(ctx: ToolContext, title: str = "", text: str = "", url: str = "") -> ToolResult:
    """Record a finding so it survives into Results and the final report."""
    if url:
        results.source(ctx.task_id, str(title or url)[:120], str(url))
    else:
        results.summary(ctx.task_id, str(title or "Finding")[:120], str(text or ""))
    return ToolResult(True, "Noted.")


# ── the table ──────────────────────────────────────────────────────────────
TOOLS: Dict[str, Tool] = {
    t.name: t for t in [
        Tool("web_search", "web_search", ("query",),
             "Search the web and read the results page. Use this when you do not "
             "already know the exact URL.", _t_web_search),
        Tool("open_url", "open_url", ("url", "new_tab"),
             "Open a URL in the browser and read the page.", _t_open_url),
        Tool("fetch_page", "open_url", ("url", "why"),
             "Read a page WITHOUT opening it in the browser — faster, and it "
             "does not disturb the tabs. Use this whenever you only need to "
             "read a page you already have the URL for. Use open_url instead "
             "when you need to click, type, or the page needs signing in.",
             _t_fetch_page),
        Tool("read_page", "read_page", ("offset",),
             "Re-read the page currently on screen. If it says text is truncated, "
             "call this again with the shown offset to read the next section.", _t_read_page),
        Tool("find", "find_element", ("query",),
             "Search this page for a word or phrase — returns matching controls "
             "AND the surrounding page text. Use it to check whether a page "
             "actually mentions something.", _t_find),
        Tool("click", "click", ("element_id",),
             "Click an element by the id shown in the last page read.", _t_click),
        Tool("type_text", "type_text", ("element_id", "text", "submit"),
             "Type into a field. Set submit true to press Enter afterwards.", _t_type),
        Tool("scroll", "scroll", ("dy",),
             "Scroll the page down (or up with a negative dy) and re-read it.", _t_scroll),
        Tool("go_back", "back", (),
             "Go back one page in this tab.", _t_back),
        Tool("go_forward", "forward", (),
             "Go forward one page in this tab.", _t_go_forward),
        Tool("reload_page", "reload", (),
             "Reload the current page.", _t_reload),
        Tool("press_key", "press_key", ("keys",),
             "Press a key or a combination — 'Enter', 'Escape', 'Ctrl+F', "
             "'Alt+T'. Many web apps have shortcuts that are far more reliable "
             "than hunting for a button (e.g. chart tools and indicators).",
             _t_press_key),
        Tool("hover", "hover", ("element_id",),
             "Move the pointer over an element without clicking. Use it to open "
             "a menu that only appears on hover.", _t_hover),
        Tool("select_option", "select_option", ("element_id", "value"),
             "Choose a value in a dropdown. Clicking a dropdown does NOT work — "
             "always use this for a <select>.", _t_select_option),
        Tool("drag", "drag",
             ("from_element", "to_element", "from_x", "from_y", "to_x", "to_y"),
             "Drag from one place to another. Give element ids, or x/y "
             "coordinates for a canvas — this is how you draw a trend line or a "
             "fib retracement on a chart.", _t_drag),
        Tool("wait_for", "wait_for", ("text", "element_id", "seconds"),
             "Wait until some text or an element appears before reading. Use it "
             "after anything slow instead of reading an empty page and "
             "concluding the data is not there.", _t_wait_for),
        Tool("close_tab", "close_tab", ("tab_id",),
             "Close a tab you are finished with.", _t_close_tab),
        Tool("new_tab", "new_tab", ("url",),
             "Open a new tab, optionally at a url.", _t_new_tab),
        Tool("list_tabs", "read_page", (),
             "List the open tabs with their ids and urls.", _t_list_tabs),
        Tool("switch_tab", "switch_tab", ("tab_id",),
             "Switch to another open tab and read it.", _t_switch_tab),
        Tool("look_at_screen", "screenshot", ("question",),
             "Capture the page and describe it visually. Only for things the page "
             "text cannot answer, like an image or a chart.", _t_screenshot),
        Tool("create_document", "create_document",
             ("name", "format", "content", "title", "subtitle", "template", "theme", "photo"),
             "Create a real file. format: md, txt, html, csv, xlsx, docx, pptx, "
             "pdf. For csv/xlsx pass rows as a list of lists or a markdown "
             "table. For pdf/html pick a `template` and a `theme` — a CV must "
             "not look like a memo. `photo` is a picture from the user's folders to "
             "put in it (asks first). Write REAL content: headings, **bold** "
             "where it matters, tables, and the actual detail asked for.",
             _t_create_document),
        Tool("preview_document", "preview_document", ("file",),
             "Show a document you made in the browser so the user can SEE it before "
             "it is saved. Always do this for anything he asked you to make, "
             "before save_to_computer.", _t_preview_document),
        Tool("write_file", "workspace_write", ("path", "content"),
             "Write a code or text file into the task workspace.", _t_write_file),
        Tool("read_file", "workspace_read", ("path",),
             "Read a file back from the task workspace.", _t_read_file),
        Tool("list_files", "workspace_list", (),
             "List the files in the task workspace.", _t_list_files),
        Tool("preview_site", "start_preview", (),
             "Serve the workspace locally and open it in a tab, to check a site "
             "you built actually renders.", _t_preview),
        Tool("save_to_computer", "export", ("file", "to"),
             "Save a file you made onto the user's computer so it stays after this "
             "task. `to` is 'downloads' or 'working_folder'. Asks first. Use "
             "this whenever the user asks for a document, PDF or spreadsheet to "
             "keep — do not just tell him where the working copy is.",
             _t_save_to_computer),
        Tool("download", "download", ("url", "why"),
             "Save a file from the web onto this machine. Always asks first. "
             "Executables are refused whatever the answer.", _t_download),
        Tool("list_my_files", "folder_list", ("folder",),
             "List the user's own files. `folder` is downloads, documents, desktop "
             "or pictures — leave it empty to see which folders you may read. "
             "Asks first.", _t_list_local_folder),
        Tool("read_my_file", "file_read", ("path",),
             "Read one of the user's own files — a PDF, Word document, spreadsheet, "
             "image or text file. Use 'downloads/cv.pdf' or a full path. Asks "
             "first.", _t_read_local_file),
        Tool("attach_file", "upload", ("element_id", "path"),
             "Attach one of the user's files to a file input on the page — this is "
             "how you upload a CV or a document. ALWAYS asks first, in every "
             "safety mode. Attaching is not submitting.", _t_attach_file),
        Tool("run_python", "run_python", ("code", "why"),
             "Run Python to calculate, analyse data, or draw a chart. pandas, "
             "numpy, matplotlib, openpyxl and Pillow are available. It runs in "
             "this task's folder with no internet, and any file it writes lands "
             "there. USE THIS for arithmetic and comparisons rather than working "
             "them out in your head.", _t_run_python),
        Tool("consult_assistant", "consult_assistant", ("question", "which"),
             "LAST RESORT ONLY, when every real source has failed: open another "
             "AI assistant and ask it. Its answer is secondhand and unverified — "
             "say so, and prefer using it to find where to look.",
             _t_consult_assistant),
        Tool("note", "", ("title", "text", "url"),
             "Record a finding or a source so it appears in Results.", _t_note,
             terminal=False),
    ]
}

# Tools the loop implements itself rather than dispatching here.
CONTROL_TOOLS = ("finish", "ask_user")


# ── connectors ─────────────────────────────────────────────────────────────
# Connected services are available here as tools, on top of the browser. Reading
# a mailbox through the Gmail API is faster, more reliable and less invasive
# than driving the Gmail web UI with a mouse — the browser is for the things a
# connector cannot do, which is most of the web.
#
# These route through syrix_connectors.manager.execute, the connector
# subsystem's own choke point, so the permission, confirmation and audit rules
# are the ones that already govern connectors everywhere else. Supercomputer
# adds no second path and relaxes nothing.
def connector_tools(config: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Schemas for every connected+allowed connector action, or [] when none."""
    cfg = config or {}
    if not bool(cfg.get("connectors_enabled", True)):
        return []
    if not bool(cfg.get("supercomputer_connectors_enabled", True)):
        return []
    try:
        from syrix_connectors import tool_surface
    except Exception as exc:
        obs.report("supercomputer.tools.connector_import", exc, level="debug")
        return []
    with obs.swallow("supercomputer.tools.connector_schemas"):
        return tool_surface.list_tool_schemas(cfg) or []
    return []


def describe_connector_tools(config: Optional[Dict[str, Any]] = None) -> str:
    lines = []
    for schema in connector_tools(config):
        fn = schema.get("function") or {}
        name = str(fn.get("name") or "")
        if not name:
            continue
        params = ((fn.get("parameters") or {}).get("properties") or {})
        args = ", ".join(sorted(params)) or "no arguments"
        lines.append(f"- {name}({args}): {str(fn.get('description') or '')[:150]}")
    return "\n".join(lines)


# Connector tool name -> the Supercomputer gate action it counts as.
#
# Connectors have their own permission system and it still runs — this is a
# SECOND gate in front of it, not a replacement, and the more restrictive of the
# two wins. It exists because the Supercomputer capability toggles for "read
# email or private documents", "write drafts" and "send emails and messages"
# had nothing behind them: the connector path did not consult them, so switching
# "send emails" off in Supercomputer settings left Gmail sending enabled as far
# as this mode was concerned.
#
# Matched on the verb in the tool name rather than a per-connector table, so a
# connector added later is covered the day it appears instead of silently
# defaulting to ungated.
_CONNECTOR_ACTION_HINTS = (
    ("delete", "delete_online_content"),
    ("archive", "delete_online_content"),
    ("trash", "delete_online_content"),
    ("send", "send_email"),
    ("reply", "send_email"),
    ("forward", "send_email"),
    ("post", "publish"),
    ("publish", "publish"),
    ("draft", "draft_message"),
    ("create", "draft_message"),
    ("update", "draft_message"),
)


def connector_gate_action(name: str) -> str:
    """Which Supercomputer capability a connector call falls under.

    Deny-unknown in spirit: anything not obviously a write is treated as reading
    private data, which is itself gated and asks — never as a free action.
    """
    lowered = str(name or "").lower()
    for token, action in _CONNECTOR_ACTION_HINTS:
        if token in lowered:
            return action
    return "read_private_document"


def _dispatch_connector(name: str, args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    from syrix_connectors import tool_surface

    action = connector_gate_action(name)
    service = str(name or "").split("_", 1)[0] or "the service"

    def _call() -> Dict[str, Any]:
        # tool_surface speaks {success, content, error}; the gate speaks
        # {ok, reason, data}. Translating here keeps both contracts intact —
        # returning the raw dict made every connector call read as ok=False.
        out = tool_surface.execute_tool(name, dict(args or {}),
                                        config=ctx.config, request_id=ctx.request_id)
        return {
            "ok": bool(out.get("success")),
            "reason": str(out.get("error") or "") if not out.get("success") else "",
            "data": out,
        }

    res = gate.execute(
        action, _call,
        task_id=ctx.task_id, scope=service,
        description=f"Use the {name} connector",
        why=f"This reaches your {service} account through its connector.",
        config=ctx.config, request_id=ctx.request_id, timeout_s=60.0,
    )
    if res.needs_permission:
        return ToolResult(False, f"Waiting on your permission to use {name}.",
                          needs_permission=True,
                          permission_request_id=res.permission_request_id)
    if not res.ok:
        return ToolResult(False, f"{name} did not run: {_why(res)}")

    inner = res.data if isinstance(res.data, dict) else {}
    if not inner.get("success"):
        return ToolResult(False, f"{name} did not run: "
                                 f"{inner.get('content') or inner.get('error') or 'no reason given'}")
    body = str(inner.get("content") or "")
    results.add(ctx.task_id, results.KIND_SUMMARY, f"Used the {name} connector",
                detail=body[:400])
    # Connector output is data from an external service, same as a web page.
    return ToolResult(True, guard.wrap(body[:OBS_TEXT_CHARS * 2], title=name))


# Every tool that drives the one real BrowserView. A stage that may run
# alongside another stage must not have any of these — not by agreement, but by
# being handed a tool set that does not contain them.
#
# Until 2026-07-28 project.py documented this table and nothing enforced it:
# `kind` reached the stage runner and was used only to decide what context to
# show. A "research" stage had the full browser surface, which is why the
# parallel scheduler could not safely be switched on.
BROWSER_TOOLS = frozenset({
    "web_search", "open_url", "read_page", "find", "click", "type_text",
    "scroll", "go_back", "go_forward", "reload_page", "press_key", "hover",
    "select_option", "drag", "wait_for", "close_tab", "new_tab", "list_tabs",
    "switch_tab", "look_at_screen", "preview_site", "preview_document",
    "download", "attach_file",
})

# Stage kinds that must never touch the browser, and what they keep instead.
# Mirrors the table in project.py's module docstring — that table is now this
# code's documentation rather than an aspiration.
_NO_BROWSER_KINDS = frozenset({"research", "compute"})


def available(config: Optional[Dict[str, Any]] = None,
              kind: str = "") -> List[Tool]:
    """Tools whose capability is currently switched on.

    A disabled capability removes the tool from the prompt entirely, so the
    model is not invited to attempt something that would only be refused.

    ``kind`` is the project stage kind. For a stage that may run in parallel
    with another, every browser-driving tool is removed — the promise that a
    research worker keeps off the browser is kept by not giving it the door.
    """
    cfg = config or {}
    stage_kind = str(kind or "").strip().lower()
    drop = BROWSER_TOOLS if stage_kind in _NO_BROWSER_KINDS else frozenset()
    out: List[Tool] = []
    for tool in TOOLS.values():
        if tool.name in drop:
            continue
        if not tool.action or capabilities.is_enabled(tool.action, cfg):
            out.append(tool)
    return out


def describe_tools(config: Optional[Dict[str, Any]] = None,
                   kind: str = "") -> str:
    lines = []
    usable = available(config, kind=kind)
    if str(kind or "").strip().lower() in _NO_BROWSER_KINDS:
        lines.append(
            "NOTE: this step runs alongside others, so it has NO browser. Read "
            "pages with fetch_page(url), which fetches over HTTP. There is no "
            "clicking, typing or signing in from here — if the answer genuinely "
            "needs that, finish and say so.\n")
    for tool in usable:
        args = ", ".join(tool.args) if tool.args else "no arguments"
        lines.append(f"- {tool.name}({args}): {tool.description}")
    lines.append("- finish(answer): stop and give the user the answer or the report. "
                 "Only when the goal is genuinely met or you cannot go further.")
    lines.append("- ask_user(question): stop and ask the user one question, when the "
                 "task cannot proceed without their input.")
    # Templates and themes only matter if the model knows they exist.
    if any(t.name == "create_document" for t in available(config)):
        lines.append("\nWHEN YOU MAKE A DOCUMENT — " + documents.describe_options())
    connectors = describe_connector_tools(config)
    if connectors:
        lines.append(
            "\nCONNECTED SERVICES — prefer these over the browser when they can "
            "answer, because they read the account directly instead of clicking "
            "through a web page:\n" + connectors)
    return "\n".join(lines)


def dispatch(name: str, args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Run one tool call. Never raises."""
    key = str(name or "").strip()
    tool = TOOLS.get(key)
    if tool is None:
        # A connected service's action, run through the connector subsystem's
        # own choke point rather than anything here.
        with obs.swallow("supercomputer.tools.connector_probe", tool=key):
            from syrix_connectors import tool_surface

            if tool_surface.is_connector_tool(key):
                return _dispatch_connector(key, args, ctx)
        known = ", ".join(sorted(t.name for t in available(ctx.config, kind=ctx.kind)))
        return ToolResult(False, f"There is no tool called '{key}'. Available: {known}, finish, ask_user.")
    # The browser bar for a stage that may run alongside another. Checked HERE
    # and not only in the prompt: leaving it out of the tool list is a hint, and
    # a hint is not a safety model. Two stages interleaving clicks into the one
    # BrowserView is the exact failure this prevents.
    if (str(ctx.kind or "").strip().lower() in _NO_BROWSER_KINDS
            and key in BROWSER_TOOLS):
        return ToolResult(False, (
            f"'{key}' is not available in this step — it runs alongside other "
            f"steps, so it must not touch the browser. Use fetch_page(url) to "
            f"read a page over HTTP instead."))
    if tool.action and not capabilities.is_enabled(tool.action, ctx.config):
        cap = capabilities.capability_for(tool.action)
        return ToolResult(False, f"'{key}' is unavailable: \"{cap.label if cap else tool.action}\" "
                                 f"is switched off in Supercomputer settings.")
    clean = {k: v for k, v in (args or {}).items() if k in tool.args}
    started = time.monotonic()
    try:
        res = tool.handler(ctx, **clean)
    except TypeError as exc:
        return ToolResult(False, f"{key} was called with the wrong arguments ({exc}). "
                                 f"It takes: {', '.join(tool.args) or 'nothing'}.")
    except Exception as exc:
        obs.report("supercomputer.tools.dispatch", exc, tool=key, task_id=ctx.task_id)
        return ToolResult(False, f"{key} failed with {type(exc).__name__}.")
    res.data = dict(res.data or {})
    res.data["latency_ms"] = int((time.monotonic() - started) * 1000)
    return res


__all__ = ["Tool", "ToolResult", "ToolContext", "TOOLS", "CONTROL_TOOLS",
           "available", "describe_tools", "dispatch", "OBS_TEXT_CHARS",
           "connector_tools", "describe_connector_tools"]
