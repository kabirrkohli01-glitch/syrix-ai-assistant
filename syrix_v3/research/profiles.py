"""Reading a public profile: TikTok, YouTube, GitHub, LinkedIn and the rest.

A profile page is almost all application shell. Read as prose it yields a
cookie banner and "Log in to follow", which is why a request to review a
TikTok account came back as a review of TikTok. The facts a review needs —
the handle, the bio, followers, likes, what was posted recently — are in data
the page embeds for its own scripts, and each platform embeds it differently,
so there is one small reader per platform.

Measured on 2026-09-24, without an account, one request each:

- TikTok ``/@handle`` — 200 with the profile in the page's rehydration JSON.
- YouTube ``/@handle`` and ``/@handle/videos`` — 200 with ``ytInitialData``.
- GitHub — its public REST API, 200.
- LinkedIn — ``/in/…`` answers 999, a login wall. ``/company/…`` answers 200,
  but LinkedIn's robots.txt is ``User-agent: * / Disallow: /``, and every
  read here honours robots.txt, so LinkedIn is not opened at all.
- Instagram's profile API — 401, ``require_login``. X/Twitter and Facebook
  show nothing signed out either.

For the walled ones nothing is invented: the evidence says the profile needs
a login, and the turn falls back to what search engines index publicly, which
the answer then attributes as such.

Every fetch goes through ``syrix_web.gateway.fetch_raw`` — policy, robots,
public-address pinning — and anything a person wrote (a bio, a video title)
is scanned for instructions aimed at the assistant before it is passed on.
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from .schemas import Evidence, Source

TIKTOK = "TikTok"
YOUTUBE = "YouTube"
GITHUB = "GitHub"
LINKEDIN = "LinkedIn"
INSTAGRAM = "Instagram"
X = "X"
FACEBOOK = "Facebook"
THREADS = "Threads"

# Platforms that cannot be read, and why — said in the evidence so the answer
# states it rather than inventing the profile or declining to look at all.
_LOGIN = "{p} shows {what} only to signed-in accounts"
_WALLED = {
    (LINKEDIN, "person"): ("LinkedIn's robots.txt refuses all automated readers, and its "
                           "member profiles need a signed-in account as well"),
    (LINKEDIN, "company"): "LinkedIn's robots.txt refuses all automated readers",
    (INSTAGRAM, "person"): _LOGIN.format(p=INSTAGRAM, what="profiles"),
    (X, "person"): _LOGIN.format(p=X, what="profiles and posts"),
    (FACEBOOK, "person"): _LOGIN.format(p=FACEBOOK, what="profiles"),
    (THREADS, "person"): _LOGIN.format(p=THREADS, what="profiles"),
}

_URL_PATTERNS: List[Tuple[str, str, re.Pattern]] = [
    (TIKTOK, "person", re.compile(r"tiktok\.com/@([\w.]{2,24})", re.I)),
    (YOUTUBE, "channel", re.compile(
        r"youtube\.com/(@[\w.\-]{2,100}|channel/UC[\w\-]{20,}|c/[\w.\-]+|user/[\w.\-]+)", re.I)),
    (LINKEDIN, "company", re.compile(r"linkedin\.com/company/([\w%\-]{2,100})", re.I)),
    (LINKEDIN, "person", re.compile(r"linkedin\.com/in/([\w%\-]{2,100})", re.I)),
    (INSTAGRAM, "person", re.compile(
        r"instagram\.com/(?!p/|reel/|reels/|explore/|stories/)([\w.]{1,30})", re.I)),
    (THREADS, "person", re.compile(r"threads\.(?:net|com)/@([\w.]{1,30})", re.I)),
    (X, "person", re.compile(
        r"(?:twitter|x)\.com/(?!i/|home\b|search\b|intent/|share\b)(\w{1,15})\b", re.I)),
    (FACEBOOK, "person", re.compile(r"facebook\.com/(?!share|groups/|events/)([\w.]{3,50})", re.I)),
    # A user, not a repository: exactly one path segment.
    (GITHUB, "person", re.compile(r"github\.com/([A-Za-z0-9](?:[A-Za-z0-9\-]{0,38}))/?(?![\w/.\-])",
                                  re.I)),
]

_PLATFORM_WORDS = {
    "tiktok": TIKTOK, "tik tok": TIKTOK, "youtube": YOUTUBE, "yt": YOUTUBE,
    "github": GITHUB, "linkedin": LINKEDIN, "instagram": INSTAGRAM, "insta": INSTAGRAM,
    "ig": INSTAGRAM, "twitter": X, "x": X, "threads": THREADS, "facebook": FACEBOOK,
}
_PLATFORM_ALT = r"(?P<p>tik\s?tok|youtube|yt|github|linkedin|instagram|insta|ig|twitter|x|threads|facebook)"
_NAMED = [
    # "@mrbeast on tiktok"
    re.compile(r"@(?P<h>[\w.]{2,30})\s+on\s+" + _PLATFORM_ALT + r"\b", re.I),
    # "tiktok account @mrbeast", "the github user torvalds"
    re.compile(_PLATFORM_ALT + r"\s+(?:account|profile|page|channel|handle|user(?:name)?)\s+"
               r"(?:of\s+|for\s+|called\s+|named\s+)?@?(?P<h>[\w.]{2,30})\b", re.I),
    # "tiktok @mrbeast"
    re.compile(_PLATFORM_ALT + r"\s+@(?P<h>[\w.]{2,30})\b", re.I),
    # "mrbeast's tiktok", "torvalds's github" — the apostrophe is required, or
    # "his tiktok" reads as the account "hi".
    re.compile(r"(?P<h>[\w.]{2,30})(?:'s|’s|')\s+" + _PLATFORM_ALT +
               r"(?:\s+(?:account|profile|page|channel))?\b", re.I),
]
_NOT_A_HANDLE = frozenset(
    "my your his her their our its the a an this that me him them it u you ur "
    "account profile page channel user".split())


@dataclass
class ProfileAsk:
    platform: str
    handle: str
    kind: str = "person"   # "person", "company" or "channel"
    url: str = ""

    @property
    def walled(self) -> str:
        return _WALLED.get((self.platform, self.kind), "")


def _profile_url(platform: str, kind: str, handle: str) -> str:
    h = handle.lstrip("@")
    return {
        TIKTOK: f"https://www.tiktok.com/@{h}",
        YOUTUBE: (f"https://www.youtube.com/{handle}" if "/" in handle
                  else f"https://www.youtube.com/@{h}"),
        GITHUB: f"https://github.com/{h}",
        LINKEDIN: (f"https://www.linkedin.com/company/{h}" if kind == "company"
                   else f"https://www.linkedin.com/in/{h}"),
        INSTAGRAM: f"https://www.instagram.com/{h}/",
        X: f"https://x.com/{h}",
        THREADS: f"https://www.threads.net/@{h}",
        FACEBOOK: f"https://www.facebook.com/{h}",
    }[platform]


def detect(text: str) -> Optional[ProfileAsk]:
    """The profile a message points at, by link or by handle."""
    text = " ".join(str(text or "").split())
    if not text:
        return None
    for platform, kind, pattern in _URL_PATTERNS:
        match = pattern.search(text)
        if match:
            # A YouTube channel/UC…, c/… or user/… address is kept whole: the
            # path is the identifier.
            handle = match.group(1)
            if handle.lower() in _NOT_A_HANDLE:
                continue
            return ProfileAsk(platform, handle, kind, _profile_url(platform, kind, handle))
    for pattern in _NAMED:
        match = pattern.search(text)
        if not match:
            continue
        handle = match.group("h").strip(".")
        platform = _PLATFORM_WORDS.get(re.sub(r"\s+", " ", match.group("p").lower()))
        if not platform or handle.lower() in _NOT_A_HANDLE:
            continue
        # "x" alone is too common a word to trust without an @.
        if platform == X and "@" not in match.group(0):
            continue
        kind = "channel" if platform == YOUTUBE else "person"
        return ProfileAsk(platform, handle, kind, _profile_url(platform, kind, handle))
    return None


# ── helpers ────────────────────────────────────────────────────────────────
def _n(value: Any) -> str:
    try:
        return f"{int(str(value).replace(',', '')):,}"
    except (TypeError, ValueError):
        return str(value or "?")


def _safe(text: str) -> str:
    """Text a person wrote, or nothing if it tries to instruct the assistant."""
    text = " ".join(str(text or "").split())
    if not text:
        return ""
    try:
        from syrix_web import injection
        if injection.scan(text):
            return "[withheld: the text contained instructions aimed at an assistant]"
    except Exception:
        return ""
    return text


def _json_after(marker: str, html: str) -> Optional[Any]:
    """The JSON object assigned after ``marker`` in a script tag."""
    at = html.find(marker)
    if at < 0:
        return None
    start = html.find("{", at)
    if start < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(html[start:])
        return obj
    except ValueError:
        return None


Fetch = Callable[..., Dict[str, Any]]


def _default_fetch() -> Fetch:
    from syrix_web import gateway
    return gateway.fetch_raw


# ── per-platform readers: each returns (title, facts, problem) ──────────────
def _tiktok(ask: ProfileAsk, fetch: Fetch, timeout_s: float, rid: str) -> Tuple[str, str, str]:
    got = fetch(ask.url, timeout_s=timeout_s, request_id=rid)
    if not got.get("ok"):
        return "", "", f"TikTok did not return the profile ({got.get('reason') or 'no answer'})"
    html = got["body"].decode("utf-8", errors="replace")
    match = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
                      html, re.S)
    try:
        scope = json.loads(match.group(1))["__DEFAULT_SCOPE__"] if match else {}
    except (ValueError, KeyError):
        scope = {}
    detail = scope.get("webapp.user-detail") or {}
    info = detail.get("userInfo") or {}
    user, stats = info.get("user") or {}, info.get("statsV2") or info.get("stats") or {}
    if not user.get("uniqueId"):
        code = detail.get("statusCode")
        if code in (10221, 10202):
            return "", "", f"TikTok reports that no account @{ask.handle.lstrip('@')} exists"
        return "", "", "TikTok returned the page without the profile data"
    lines = [f"TIKTOK PROFILE @{user['uniqueId']} — {user.get('nickname') or ''}".strip(" —")]
    lines.append(f"Link: https://www.tiktok.com/@{user['uniqueId']}")
    lines.append("Verified account: " + ("yes" if user.get("verified") else "no"))
    if user.get("privateAccount"):
        # A private account's counts read as zero to a visitor; reporting
        # them as zero would say the account has no followers.
        lines.append("PRIVATE ACCOUNT: TikTok shows its follower, like and video counts "
                     "only to approved followers, so they are not known — not zero.")
    else:
        lines.append(f"Followers: {_n(stats.get('followerCount'))} · Following: "
                     f"{_n(stats.get('followingCount'))} · Likes: {_n(stats.get('heartCount') or stats.get('heart'))}"
                     f" · Videos: {_n(stats.get('videoCount'))}")
        derived = _tiktok_ratios(stats, user.get("createTime"))
        if derived:
            lines.append("Worked out exactly from those figures: " + derived)
    # The public profile page carries no video list. Saying so stops a review
    # describing "recent posts" it never saw — measured, it did exactly that.
    lines.append("Recent videos: not listed on TikTok's public profile page, so their "
                 "topics, views and dates are not known here.")
    bio = _safe(user.get("signature"))
    lines.append(f"Bio: {bio}" if bio else "Bio: (empty)")
    link = ((user.get("bioLink") or {}).get("link") or "")
    if link:
        lines.append(f"Link in bio: {_safe(link)}")
    if user.get("createTime"):
        made = datetime.fromtimestamp(int(user["createTime"]), tz=timezone.utc)
        lines.append(f"Account created: {made:%B %Y}")
    if user.get("commerceUserInfo", {}).get("commerceUser"):
        lines.append("Business account: yes")
    return f"@{user['uniqueId']} on TikTok", "\n".join(lines), ""


def _count(value: Any) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _tiktok_ratios(stats: Dict[str, Any], created: Any) -> str:
    """Likes per video, followers per account followed, and posting rate.

    Computed here rather than left to the model. Measured: asked to review a
    profile, a lane put 476 videos over eight years at "about 1.5 videos per
    day"; the true figure is under one a week.
    """
    likes = _count(stats.get("heartCount") or stats.get("heart"))
    videos = _count(stats.get("videoCount"))
    followers = _count(stats.get("followerCount"))
    following = _count(stats.get("followingCount"))
    parts: List[str] = []
    if videos:
        parts.append(f"{likes / videos:,.0f} likes per video on average")
    if following:
        parts.append(f"{followers / following:,.0f} followers per account followed")
    if videos and created:
        try:
            days = max(1.0, (time.time() - int(created)) / 86400.0)
        except (TypeError, ValueError):
            days = 0.0
        if days:
            per_week = videos / (days / 7.0)
            parts.append(f"account {days / 365.25:.1f} years old, "
                         f"{per_week:.2f} videos a week ({videos / (days / 30.44):.1f} a month) "
                         "over its lifetime")
    return "; ".join(parts)


def _yt_text(node: Any) -> str:
    if isinstance(node, dict):
        if "simpleText" in node:
            return str(node["simpleText"])
        if "content" in node and isinstance(node["content"], str):
            return node["content"]
        if "runs" in node:
            return "".join(str(r.get("text") or "") for r in node["runs"])
    return ""


def _youtube(ask: ProfileAsk, fetch: Fetch, timeout_s: float, rid: str) -> Tuple[str, str, str]:
    base = ask.url.rstrip("/")
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="v3yt") as pool:
        home_f = pool.submit(fetch, base, timeout_s=timeout_s, request_id=rid)
        vids_f = pool.submit(fetch, base + "/videos", timeout_s=timeout_s, request_id=rid)
        home, vids = home_f.result(), vids_f.result()
    if not home.get("ok"):
        return "", "", f"YouTube did not return the channel ({home.get('reason') or 'no answer'})"
    data = _json_after("var ytInitialData", home["body"].decode("utf-8", errors="replace")) or {}
    meta = (data.get("metadata") or {}).get("channelMetadataRenderer") or {}
    if not meta.get("title"):
        return "", "", "YouTube returned the page without the channel data"
    header = json.dumps(data.get("header") or {})
    counts = re.findall(r'"content": "([^"]*?(?:subscribers?|videos?))"', header)
    lines = [f"YOUTUBE CHANNEL — {meta['title']}",
             f"Link: {meta.get('vanityChannelUrl') or base}"]
    if counts:
        lines.append("Counts shown on the channel: " + " · ".join(dict.fromkeys(counts)))
    desc = _safe(meta.get("description"))
    if desc:
        lines.append(f"Description: {desc[:700]}")
    recent: List[str] = []
    if vids.get("ok"):
        vdata = _json_after("var ytInitialData", vids["body"].decode("utf-8", errors="replace")) or {}

        def walk(node: Any) -> None:
            if len(recent) >= 8:
                return
            if isinstance(node, dict):
                lockup = node.get("lockupViewModel")
                if isinstance(lockup, dict):
                    md = (lockup.get("metadata") or {}).get("lockupMetadataViewModel") or {}
                    title = _yt_text(md.get("title"))
                    parts = []
                    for row in (((md.get("metadata") or {}).get("contentMetadataViewModel") or {})
                                .get("metadataRows") or []):
                        parts += [_yt_text(p.get("text")) for p in row.get("metadataParts") or []]
                    if title:
                        recent.append(f"- {_safe(title)} ({', '.join(p for p in parts if p)})")
                    return
                renderer = node.get("videoRenderer")
                if isinstance(renderer, dict):
                    title = _yt_text(renderer.get("title"))
                    if title:
                        recent.append(f"- {_safe(title)} ({_yt_text(renderer.get('viewCountText'))}, "
                                      f"{_yt_text(renderer.get('publishedTimeText'))})")
                    return
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)
        walk(vdata)
    if recent:
        lines.append("Most recent uploads (newest first):")
        lines += recent
    return f"{meta['title']} on YouTube", "\n".join(lines), ""


def _github(ask: ProfileAsk, fetch: Fetch, timeout_s: float, rid: str) -> Tuple[str, str, str]:
    h = ask.handle.lstrip("@")
    accept = "application/vnd.github+json"
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="v3gh") as pool:
        user_f = pool.submit(fetch, f"https://api.github.com/users/{h}", timeout_s=timeout_s,
                             request_id=rid, accept=accept)
        repos_f = pool.submit(fetch, f"https://api.github.com/users/{h}/repos?sort=pushed&per_page=8",
                              timeout_s=timeout_s, request_id=rid, accept=accept)
        user_r, repos_r = user_f.result(), repos_f.result()
    if not user_r.get("ok"):
        if user_r.get("status") == 404:
            return "", "", f"GitHub reports no user {h}"
        return "", "", f"GitHub did not return the profile ({user_r.get('reason') or 'no answer'})"
    try:
        user = json.loads(user_r["body"])
    except ValueError:
        return "", "", "GitHub returned an unreadable profile"
    lines = [f"GITHUB PROFILE {user.get('login')} — {user.get('name') or ''}".strip(" —"),
             f"Link: {user.get('html_url')}"]
    for key, label in (("bio", "Bio"), ("company", "Company"), ("location", "Location"),
                       ("blog", "Website")):
        if user.get(key):
            lines.append(f"{label}: {_safe(user[key])}")
    lines.append(f"Followers: {_n(user.get('followers'))} · Following: {_n(user.get('following'))}"
                 f" · Public repositories: {_n(user.get('public_repos'))}")
    if user.get("created_at"):
        lines.append(f"Joined: {str(user['created_at'])[:10]}")
    try:
        repos = json.loads(repos_r["body"]) if repos_r.get("ok") else []
    except ValueError:
        repos = []
    if isinstance(repos, list) and repos:
        lines.append("Most recently pushed repositories:")
        for repo in repos[:8]:
            lines.append(f"- {repo.get('name')}: {_safe(repo.get('description')) or 'no description'}"
                         f" ({repo.get('language') or 'no language'}, "
                         f"{_n(repo.get('stargazers_count'))} stars, last push "
                         f"{str(repo.get('pushed_at') or '')[:10]})")
    return f"{user.get('login')} on GitHub", "\n".join(lines), ""


_READERS = {
    (TIKTOK, "person"): _tiktok,
    (YOUTUBE, "channel"): _youtube,
    (GITHUB, "person"): _github,
}


def search_queries(ask: ProfileAsk) -> List[str]:
    """What to search for when the profile itself cannot be read.

    The profile's own address first: search engines index a walled profile
    under it, with the headline and location in the snippet. Measured, the
    handle alone ("williamhgates LinkedIn") returned scraping services and
    API documentation that mention LinkedIn profiles in general.
    """
    h = ask.handle.lstrip("@")
    readable = re.sub(r"[-_]+", " ", re.sub(r"-[0-9a-f]{6,}$", "", h)).strip()
    return [ask.url.split("://", 1)[-1].rstrip("/"), f"{readable} {ask.platform}"]


def about_the_profile(ask: ProfileAsk, sources: List[Source]) -> List[Source]:
    """The search results that are about this profile, when any are.

    Kept only when they name the handle in their address or title; if none
    do, all are returned and the answer says how little was found.
    """
    h = ask.handle.lstrip("@").lower()
    words = [w for w in re.split(r"[-_.\s]+", re.sub(r"-[0-9a-f]{6,}$", "", h)) if len(w) >= 3]
    def matches(source: Source) -> bool:
        where = f"{source.url} {source.title}".lower()
        return h in where or (bool(words) and all(w in where for w in words))
    kept = [s for s in sources if matches(s)]
    return kept or sources


def read(
    ask: ProfileAsk,
    *,
    request_id: str,
    deadline_s: float,
    on_step: Optional[Callable[[str, str, str], None]] = None,
    fetch: Optional[Fetch] = None,
) -> Evidence:
    """The profile as evidence. A walled or failed read says so in ``problems``."""
    started = time.monotonic()
    step = on_step or (lambda *_: None)
    fetch = fetch or _default_fetch()
    evidence = Evidence(queries=[ask.url], profile=ask.platform)
    label = f"{ask.handle if ask.handle.startswith('@') else '@' + ask.handle} on {ask.platform}"

    if ask.walled:
        # Stated in the evidence so the answer can say it plainly, instead of
        # either inventing the profile or declining to look at all.
        evidence.problems.append(
            f"{ask.walled}, so the profile at {ask.url} was not opened; anything "
            "else below is what search engines index publicly")
        return evidence

    reader = _READERS.get((ask.platform, ask.kind))
    if reader is None:
        return evidence
    step("source", f"Opening {label}", ask.url)
    try:
        title, facts, problem = reader(ask, fetch, max(1.5, min(6.0, deadline_s - 0.3)),
                                       request_id)
    except Exception as exc:
        title, facts, problem = "", "", f"the {ask.platform} reader failed ({type(exc).__name__})"
    if problem:
        evidence.problems.append(problem)
    if facts:
        evidence.sources = [Source(
            url=ask.url, title=title or label, body=facts,
            domain=re.sub(r"^www\.", "", ask.url.split("/")[2]), provider="profile",
            retrieved_at=time.strftime("%Y-%m-%dT%H:%M:%S"), score=1.0)]
    evidence.read_ms = round((time.monotonic() - started) * 1000.0, 1)
    step("pack_built", f"Read {label}" if facts else f"Could not read {label}",
         f"{evidence.read_ms:.0f}ms")
    return evidence
