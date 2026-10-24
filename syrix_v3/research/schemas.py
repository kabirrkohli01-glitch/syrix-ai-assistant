"""What a piece of evidence is.

Its own module so the lanes and the gatherer can both name it without
importing each other.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class Source:
    """One piece of evidence, with everything needed to judge and cite it.

    ``published`` and ``retrieved_at`` are carried separately and both travel
    into the prompt. An undated snippet is how an answer ends up quoting a
    two-year-old closing price as today's, and the model cannot weigh what it
    cannot see.
    """

    url: str
    title: str = ""
    snippet: str = ""
    body: str = ""
    domain: str = ""
    provider: str = ""
    published: str = ""
    retrieved_at: str = ""
    score: float = 0.0
    # The page's own preview image, when the lane reports one. Used for the
    # images shown with an answer; never evidence.
    image: str = ""

    @property
    def opened(self) -> bool:
        return bool(self.body)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url, "title": self.title, "snippet": self.snippet,
            "domain": self.domain, "provider": self.provider,
            "published": self.published, "retrieved_at": self.retrieved_at,
            "image": self.image,
        }


@dataclass
class Evidence:
    """Everything one gathering pass produced."""

    sources: List[Source] = field(default_factory=list)
    queries: List[str] = field(default_factory=list)
    lanes: List[str] = field(default_factory=list)
    searched_ms: float = 0.0
    read_ms: float = 0.0
    truncated: bool = False          # the deadline cut gathering short
    excluded: List[str] = field(default_factory=list)  # subjects the user ruled out
    problems: List[str] = field(default_factory=list)
    # Images a lane found for the query itself: {"url", "title", "page"}.
    images: List[Dict[str, Any]] = field(default_factory=list)
    # Set when the evidence is a visit to one named site rather than a search:
    # the host, how its address was established, and its own navigation as
    # (text, url) so a follow-up can go on from where this turn left off.
    site: str = ""
    site_how: str = ""
    site_sections: List[Any] = field(default_factory=list)
    # Set when the evidence is a public profile: the platform's name.
    profile: str = ""

    @property
    def opened(self) -> List[Source]:
        return [s for s in self.sources if s.opened]

    def __bool__(self) -> bool:
        return bool(self.sources)
