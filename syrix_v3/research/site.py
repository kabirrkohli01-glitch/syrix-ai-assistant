"""Going to a website, as opposed to searching for one.

"Go to the Nvidia website and tell me what its About page says" names its
source. Handled as research, it was sent to the search engines as one long
query, and what came back was coverage *about* Nvidia ranked on those words,
never Nvidia's own page. "Can u open nvidia.com and tell me what they do"
fared worse: the capability pattern read "can u open" as a question about
SYRIX, and nothing was fetched at all.

A named destination is served the way a person would serve it: find the
site, open its homepage, follow the links whose words match the section asked
for, and read those. It runs in the prefetch window like any other gathering,
so the model receives the pages with the question and spends no round
finding them.

Three stages, each separately testable:

- ``detect`` — is this a visit, to where, and for which section. In code, no
  model call, because it has to be decided at t=0.
- ``official_site.resolve`` — a named site becomes its homepage, from a
  registry rather than a guess (see that module).
- ``visit`` — open, score the links, follow the best, read.
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from .schemas import Evidence, Source

# ── what a section is called ────────────────────────────────────────────────
# Each section: the phrases a question uses for it, the words a link to it
# carries, and the paths to try when no link on the homepage names it.
_SECTIONS: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "about": {
        "asked": ("about us", "about page", "about section", "who they are", "who we are",
                  "what they do", "our story", "their story", "company info",
                  "company overview", "mission", "what the company does"),
        "link": ("about us", "about", "who we are", "our story", "company", "overview",
                 "our mission", "mission", "story", "corporate", "at a glance", "in brief"),
        "paths": ("/about", "/about-us", "/company"),
    },
    "careers": {
        "asked": ("careers", "career", "jobs", "job openings", "vacancies", "hiring",
                  "work there", "internships", "internship", "apprenticeships",
                  "graduate scheme", "open roles"),
        "link": ("careers", "career", "jobs", "join us", "work with us", "work here",
                 "vacancies", "opportunities", "early careers", "students", "hiring"),
        "paths": ("/careers", "/jobs"),
    },
    "pricing": {
        "asked": ("pricing", "prices", "price list", "plans", "how much it costs",
                  "subscription"),
        "link": ("pricing", "plans", "prices", "plans and pricing", "compare plans"),
        "paths": ("/pricing", "/plans"),
    },
    "contact": {
        "asked": ("contact", "contact details", "phone number", "email address",
                  "get in touch", "address"),
        "link": ("contact us", "contact", "get in touch", "help", "support"),
        "paths": ("/contact", "/contact-us"),
    },
    "leadership": {
        "asked": ("leadership", "executives", "management team", "who runs",
                  "board of directors", "the board", "ceo", "founders", "team page"),
        "link": ("leadership", "executives", "management", "our leaders",
                 "board", "our team", "team", "people", "governance"),
        "paths": ("/leadership", "/about/leadership", "/team"),
    },
    "investors": {
        "asked": ("investor relations", "investors", "annual report", "shareholders",
                  "financial results", "earnings"),
        "link": ("investor relations", "investors", "investor", "shareholders",
                 "financials", "annual report", "results"),
        "paths": ("/investors", "/investor-relations"),
    },
    "news": {
        "asked": ("newsroom", "press releases", "press", "latest news", "their news",
                  "announcements", "blog", "top story", "headlines"),
        "link": ("newsroom", "news", "press", "press releases", "blog", "media",
                 "announcements", "stories"),
        "paths": ("/news", "/newsroom", "/blog"),
    },
    "products": {
        "asked": ("products", "what they sell", "product range", "services",
                  "what they offer", "solutions"),
        "link": ("products", "shop", "store", "solutions", "services", "platform",
                 "what we do", "what we offer"),
        "paths": ("/products", "/services"),
    },
    "history": {
        "asked": ("history", "founded", "timeline", "heritage", "origins"),
        "link": ("history", "our history", "heritage", "timeline", "our story",
                 "story", "about"),
        "paths": ("/history", "/about/history"),
    },
    "sustainability": {
        "asked": ("sustainability", "esg", "climate", "environment", "responsibility",
                  "impact report"),
        "link": ("sustainability", "responsibility", "esg", "impact", "environment",
                 "climate", "corporate responsibility"),
        "paths": ("/sustainability",),
    },
    "privacy": {
        "asked": ("privacy policy", "privacy", "terms of service", "terms and conditions",
                  "cookie policy", "data policy"),
        "link": ("privacy policy", "privacy", "terms", "terms of service", "cookies",
                 "legal"),
        "paths": ("/privacy", "/privacy-policy", "/legal"),
    },
    "admissions": {
        "asked": ("admissions", "entry requirements", "how to apply", "courses",
                  "undergraduate", "postgraduate", "tuition fees", "fees"),
        "link": ("study", "undergraduate", "postgraduate", "courses", "admissions",
                 "apply", "entry requirements", "fees and funding", "prospective students"),
        "paths": ("/study", "/courses", "/admissions"),
    },
}

# Words that stop a link being the section page even when it matches — a
# privacy notice sits under /about/ on half the sites on the web.
_NOT_A_SECTION = ("privacy", "cookie", "terms", "legal", "accessibility", "sitemap",
                  "modern slavery", "log in", "sign in", "subscribe", "cart", "basket")

_TLDS = ("com|org|net|io|ai|co|uk|us|ca|au|de|fr|in|edu|gov|dev|app|me|tv|info|biz|eu|"
         "nl|es|it|jp|ch|se|no|ie|nz|sg|xyz|tech|site|online|store|shop|news|so|gg|ly|fm")
_URL = re.compile(r"\bhttps?://[^\s<>()\"']+", re.I)
_BARE_DOMAIN = re.compile(
    r"(?<![@\w.])((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:" + _TLDS + r"))"
    r"(?![a-z0-9-])(/[^\s,)\"'<>]*)?", re.I)

_SITE_WORD = r"(?:official\s+)?(?:web\s?site|site|home\s?page|web\s?page)"
_NAME = r"(?P<name>[a-z0-9][a-z0-9&.'\- ]{0,40}?)"
_POSS = r"(?:'s|s'|’s)?"
_NAMED_SITE = [
    # "go to nvda website", "head over to the bbc's site"
    re.compile(r"\b(?:go|goto|head|navigate|jump|get)\s+(?:over\s+)?(?:on)?to\s+(?:the\s+)?"
               + _NAME + _POSS + r"\s+" + _SITE_WORD, re.I),
    # "open the nvidia website", "check out tesla's site", "look at apple's homepage"
    re.compile(r"\b(?:open|visit|check(?:\s+out)?|look\s+(?:at|on|through)|pull\s+up|"
               r"browse|scan|read|search|go\s+through|explore)\s+(?:up\s+)?(?:the\s+)?"
               + _NAME + _POSS + r"\s+" + _SITE_WORD, re.I),
    # "what does it say on the nvidia website", "whats on tesla's site"
    re.compile(r"\b(?:on|from|in|at|of)\s+(?:the\s+)?" + _NAME + _POSS + r"\s+" + _SITE_WORD,
               re.I),
    # "the nvidia website", as a subject: "what does the nvidia website say"
    re.compile(r"\bthe\s+" + _NAME + _POSS + r"\s+" + _SITE_WORD, re.I),
]
_SECTION_PAGE = re.compile(
    r"\b" + _NAME + _POSS + r"\s+(?:official\s+)?(?:website'?s?\s+|site'?s?\s+)?"
    r"(?P<section>about(?:\s+us)?|careers?|jobs|pricing|contact(?:\s+us)?|leadership|"
    r"investors?(?:\s+relations)?|newsroom|press|blog|history|mission|team|admissions|"
    r"sustainability|privacy\s+policy)\s+(?:page|section|tab)\b", re.I)

# What cannot be the name of a site: pronouns, determiners and the generic.
_NOT_A_NAME = frozenset("""
a an the my your our their his her its this that these those any some every
which what whose own u you ur yours me us them it one new good best great nice
official company business personal portfolio web site website page home same
other another different free cheap simple basic small online e-commerce ecommerce
school college shop store blog news landing static
""".split())
# Building a website is not visiting one.
_MAKING = re.compile(r"\b(?:build|make|create|design|code|host|develop|launch|set\s+up|"
                     r"write|redesign|deploy)\b[^.?!]{0,40}\b(?:web\s?site|site|home\s?page)",
                     re.I)

_STOP = frozenset("""
a an the is are was were be of in on for to and or with what whats which who how why
when where it its this that there their they them me my tell say says said show find
go open visit look check see read please can could would u you your about us page site
website homepage official give get any some all do does did just now then also into
""".split())


@dataclass
class Visit:
    """A request to go to a site, and what to find there."""

    url: str = ""        # given outright, or "" when only named
    name: str = ""       # the site as named: "nvda", "the bbc"
    section: str = ""    # a key of _SECTIONS, or ""
    words: List[str] = field(default_factory=list)  # the question's own terms

    @property
    def label(self) -> str:
        return host_of(self.url) or self.name


def host_of(url: str) -> str:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def registrable(host: str) -> str:
    """``nvidia.com`` for ``www.nvidia.com``; ``kcl.ac.uk`` for ``www.kcl.ac.uk``."""
    parts = [p for p in (host or "").lower().split(".") if p]
    if len(parts) >= 3 and parts[-2] in ("co", "ac", "gov", "org", "com", "net", "edu", "nhs"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _section_of(text: str) -> str:
    low = text.lower()
    best, best_len = "", 0
    for key, spec in _SECTIONS.items():
        for phrase in spec["asked"]:
            if len(phrase) > best_len and re.search(r"\b" + re.escape(phrase) + r"\b", low):
                best, best_len = key, len(phrase)
    return best


def _clean_name(raw: str) -> str:
    name = re.sub(r"(?:'s|s'|’s)$", "", raw.strip().strip("'’. ")).strip()
    words = name.split()
    # "whats on tesla careers page" captures from the first word; the site is
    # what is left once the question's own lead-in is taken off.
    while words and (words[0].lower() in ("the", "their", "his", "her", "its", "a", "an")
                     or words[0].lower() in _STOP):
        words = words[1:]
    name = " ".join(words)
    if not name or len(words) > 4 or name.lower() in _NOT_A_NAME:
        return ""
    if all(w.lower() in _NOT_A_NAME or w.lower() in _STOP for w in words):
        return ""
    return name


def _explicit_url(text: str) -> str:
    match = _URL.search(text)
    if match:
        return match.group(0).rstrip(".,;:!?)]}'\"")
    match = _BARE_DOMAIN.search(text)
    if match:
        domain = match.group(1)
        # "e.g." and "i.e." and version strings are not domains; a real one
        # has a label of two characters or more before its suffix.
        if len(domain.split(".")[0]) < 2:
            return ""
        return "https://" + domain + (match.group(2) or "").rstrip(".,;:!?")
    return ""


def detect(text: str) -> Optional[Visit]:
    """A visit, when the message names a site to go to. None otherwise."""
    text = " ".join(str(text or "").split())
    if not text or len(text) > 600 or _MAKING.search(text):
        return None
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP]

    url = _explicit_url(text)
    if url:
        return Visit(url=url, section=_section_of(text), words=words)

    for pattern in _NAMED_SITE:
        match = pattern.search(text)
        if match:
            name = _clean_name(match.group("name"))
            if name:
                return Visit(name=name, section=_section_of(text), words=words)

    match = _SECTION_PAGE.search(text)
    if match:
        name = _clean_name(match.group("name"))
        if name:
            section = _section_of(match.group("section")) or _section_of(text)
            if not section:
                raw = match.group("section").lower()
                section = next((k for k, s in _SECTIONS.items()
                                if raw in s["link"] or raw.split()[0] in s["link"]), "")
            return Visit(name=name, section=section, words=words)
    return None


# ── choosing which links to follow ──────────────────────────────────────────
def _segments(url: str) -> List[str]:
    try:
        path = urlsplit(url).path.lower()
    except ValueError:
        return []
    return [s for s in re.split(r"[/\-_.]+", path) if s]


def score_link(text: str, url: str, section: str, words: List[str], *,
               region: str = "body", site: str = "") -> float:
    """How likely this link is the page asked for. Zero or less: not it."""
    if site and registrable(host_of(url)) != site:
        return 0.0
    low = " ".join(text.lower().split())
    segs = _segments(url)
    path = "/".join(segs)
    score = 0.0
    spec = _SECTIONS.get(section)
    if spec:
        for phrase in spec["link"]:
            if low == phrase:
                score = max(score, 6.0)
            elif re.search(r"\b" + re.escape(phrase) + r"\b", low):
                score = max(score, 4.0)
        first = spec["link"][0].split()[0]
        # "/about-nvidia/" and "/aboutthebbc" both carry the section in the
        # address even when the link reads "Learn the NVIDIA Story".
        if any(seg == first or seg.startswith(first) for seg in segs[:2]):
            score += 3.0
        elif any(p.strip("/").split("/")[-1] in segs for p in spec["paths"]):
            score += 2.0
        if section != "privacy" and any(bad in low or bad.replace(" ", "-") in path
                                        for bad in _NOT_A_SECTION):
            return 0.0
    # The question's own words, for a section not in the table ("their
    # graduate scheme") and to break ties inside one that is.
    score += min(3.0, sum(1.0 for w in words if len(w) > 3 and (w in low or w in path)))
    if score <= 0:
        return 0.0
    if region == "nav":
        score += 0.5
    if len(low) > 60:
        score -= 2.0
    if len(segs) > 4:
        score -= 1.0
    if url.lower().endswith(".pdf"):
        score -= 2.0
    return score


def pick_links(links, section: str, words: List[str], site: str, *, limit: int = 2):
    """The ``limit`` best-scoring distinct links for the section."""
    scored = []
    for link in links:
        s = score_link(link.text, link.url, section, words,
                       region=getattr(link, "region", "body"), site=site)
        if s >= 3.0:
            scored.append((s, -len(_segments(link.url)), link))
    scored.sort(key=lambda t: (-t[0], -t[1]))
    out, seen = [], set()
    for _, _, link in scored:
        if link.url not in seen:
            seen.add(link.url)
            out.append(link)
        if len(out) >= limit:
            break
    return out


# ── the visit ───────────────────────────────────────────────────────────────
_PAGE_CHARS = 9000
_NAV_LIST = 10


def _as_source(page, provider: str = "site") -> Source:
    url = page.final_url or page.url
    body = page.text or ""
    if page.about:
        body = page.about + ("\n\n" + body if body else "")
    return Source(url=url, title=page.title or host_of(url), body=body[:_PAGE_CHARS],
                  domain=host_of(url), provider=provider,
                  retrieved_at=time.strftime("%Y-%m-%dT%H:%M:%S"), score=1.0)


def _sections_list(links, site: str, skip: List[str]) -> List[Tuple[str, str]]:
    """The site's own navigation, for a follow-up to go on from."""
    out: List[Tuple[str, str]] = []
    for link in links:
        if (getattr(link, "region", "") == "nav" and link.url not in skip
                and registrable(host_of(link.url)) == site and 2 < len(link.text) <= 40
                and link.text.lower() not in (t.lower() for t, _ in out)):
            out.append((link.text, link.url))
        if len(out) >= _NAV_LIST:
            break
    return out


# Words that say how to answer rather than what to find on the site.
_NOT_SPECIFIC = frozenset(
    "whats website site page pages tell say says think summarise summarize summary "
    "info information details check open look review there here them they their its "
    "please www com org net co uk https http html latest current new".split())


def _specific_terms(ask: Visit, home: str) -> List[str]:
    """What the question wants from the site beyond the section's own name.

    "computer science entry requirements" on a university's site is a page
    three links deep; "about us" is on the homepage. Only the first is worth
    a search scoped to the site, so the section's own vocabulary and the
    site's name do not count.
    """
    covered = set(_NOT_SPECIFIC) | set(_STOP)
    covered |= set(re.findall(r"[a-z0-9]+", (ask.name or "").lower()))
    covered |= set(re.findall(r"[a-z0-9]+", host_of(home).lower()))
    spec = _SECTIONS.get(ask.section) or {}
    vocab = {w for phrase in spec.get("asked", ()) + spec.get("link", ())
             for w in phrase.split()}
    terms = [w for w in ask.words if w not in covered and len(w) > 1]
    return terms if any(w not in vocab for w in terms) else []


def _site_search(terms: List[str], site: str, *, request_id: str, web_mode: str,
                 until: float) -> List[str]:
    """Pages on ``site`` for ``terms``, best first: the ``site:`` query a
    person types when the page they want is three links deep."""
    from . import gather as _gather

    try:
        found = _gather.search([" ".join(terms) + f" site:{site}"], web_mode=web_mode,
                               request_id=request_id, limit=10, deadline=until)
    except Exception:
        return []
    return [f.url for f in found if f.url and registrable(host_of(f.url)) == site]


def _usable(page) -> bool:
    return bool(getattr(page, "ok", False) and not getattr(page, "suspicious", False)
                and len(getattr(page, "text", "") or "") >= 200)


def visit(
    ask: Visit,
    *,
    request_id: str,
    deadline_s: float,
    web_mode: str = "spark",
    on_step: Optional[Callable[[str, str, str], None]] = None,
    opener: Optional[Callable[..., object]] = None,
    searcher: Optional[Callable[..., List[str]]] = None,
) -> Evidence:
    """Open the site and the pages asked for. Returns evidence, possibly empty.

    Two routes to the page asked for, run together: the homepage's own links
    for a named section, and a search scoped to the site for anything more
    specific than a section. Measured before the scoped search existed, "what
    does kcl.ac.uk say about computer science entry requirements" followed
    "Study" and "Undergraduate", never reached the course page, and was
    answered with the university's league-table position.

    ``opener`` and ``searcher`` are injectable for tests; production opens
    through the gateway's ``open_page``, so every page passes policy, robots
    and the injection scan.
    """
    started = time.monotonic()
    deadline = started + max(1.0, deadline_s)
    step = on_step or (lambda *_: None)
    if opener is None:
        from syrix_web import gateway
        opener = gateway.open_page
    searcher = searcher or _site_search
    # The question's own words travel with the evidence: the pack picks each
    # page's excerpt by them, and a site's name alone matches every line.
    evidence = Evidence(queries=[ask.url or ask.name, " ".join(ask.words)])

    home = ask.url
    if not home:
        step("search", f"Finding {ask.name}'s official website", "")
        budget = min(3.5, max(1.0, deadline_s * 0.45))
        from . import official_site
        found = official_site.find(ask.name, request_id=request_id, web_mode=web_mode,
                                   timeout_s=budget)
        if not found:
            evidence.problems.append(f"could not find an official website for {ask.name!r}")
            return evidence
        home = found.url
        evidence.site_how = found.how
        step("filter_rank", f"Official site: {host_of(home)}", found.how)

    def left() -> float:
        return deadline - time.monotonic()

    def _open(url: str):
        return opener(url, mode=web_mode, request_id=request_id,
                      timeout_s=max(1.5, min(6.0, left() - 0.3)))

    pool = ThreadPoolExecutor(max_workers=5, thread_name_prefix="v3visit")
    try:
        terms = _specific_terms(ask, home)
        scoped = None
        if terms and left() > 2.0:
            domain = registrable(host_of(home))
            step("search", f"Searching {domain} for “{' '.join(terms)[:60]}”", "")
            scoped = pool.submit(searcher, terms, domain, request_id=request_id,
                                 web_mode=web_mode, until=deadline - 1.8)

        step("source", f"Opening {host_of(home)}", home)
        page = _open(home)
        evidence.site = host_of(getattr(page, "final_url", "") or home)
        if getattr(page, "suspicious", False):
            evidence.problems.append(f"{host_of(home)}: ignored, the page contained "
                                     "instructions aimed at the assistant")
            return evidence
        if not getattr(page, "ok", False):
            evidence.problems.append(
                f"{host_of(home)}: {getattr(page, 'reason', '') or 'would not open'}")
            return evidence

        site = registrable(evidence.site)
        targets = []
        if ask.section:
            targets = pick_links(page.links, ask.section, ask.words, site)
            if not targets and left() > 1.5 and scoped is None:
                # Nothing on the page names the section. Its usual address is
                # the next best thing, and it either opens or it does not — a
                # miss is reported, never filled in.
                from syrix_web.links import Link
                landed = urlsplit(page.final_url or home)
                base = f"{landed.scheme}://{landed.netloc}"
                targets = [Link(text=p, url=base + p, region="guess")
                           for p in _SECTIONS[ask.section]["paths"][:2]]
        for t in targets:
            if getattr(t, "region", "") != "guess":
                step("source", f"Following “{t.text[:48]}”", t.url)
        linked = [(t, pool.submit(_open, t.url)) for t in targets] if left() > 1.0 else []

        deeper: List[str] = []
        if scoped is not None:
            try:
                # On this site only, whatever the search returned: a lane that
                # ignores ``site:`` still answers, with pages from anywhere.
                have = {home, page.final_url} | {t.url for t in targets}
                deeper = [u for u in scoped.result(timeout=max(0.2, left() - 1.2))
                          if u not in have and registrable(host_of(u)) == site][:2]
            except Exception:
                deeper = []
        for url in deeper:
            step("source", f"Opening {urlsplit(url).path[:60] or url}", url)
        searched = [pool.submit(_open, url) for url in deeper] if left() > 0.8 else []

        # The most specific page leads: what the scoped search found for the
        # question's own words, then the section, then the homepage.
        opened = []
        for future in searched:
            try:
                sub = future.result(timeout=max(0.3, left()))
            except Exception:
                continue
            if _usable(sub):
                opened.append(sub)
        for t, future in linked:
            try:
                sub = future.result(timeout=max(0.3, left()))
            except Exception:
                continue
            if _usable(sub):
                opened.append(sub)
            elif getattr(t, "region", "") != "guess":
                evidence.problems.append(
                    f"{t.url}: {getattr(sub, 'reason', '') or 'had no readable text'}")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    if ask.section and not opened:
        evidence.problems.append(
            f"no {ask.section} page could be found on {evidence.site}; "
            "the homepage is below")

    if len(opened) > len(deeper) + 1 and deeper:
        # The scoped search found the specific page, so a generic section
        # page ("Study", "Undergraduate") is kept as context once, not twice.
        opened = opened[:len(deeper) + 1]
    evidence.sources = [_as_source(p) for p in opened] + [_as_source(page)]
    skip = [s.url for s in evidence.sources]
    evidence.site_sections = _sections_list(page.links, site, skip)
    evidence.read_ms = round((time.monotonic() - started) * 1000.0, 1)
    step("pack_built", f"Read {len(evidence.sources)} page{'s' if len(evidence.sources) != 1 else ''} "
         f"on {evidence.site}", f"{evidence.read_ms:.0f}ms")
    return evidence
