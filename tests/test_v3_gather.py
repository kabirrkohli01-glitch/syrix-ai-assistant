"""The search phase: which lanes it waits for, and for how long.

Exa and Tavily return page text and pictures with their results, and their
latency ranges from half a second to six. A fixed search window cut both off
on a travel question, which was then answered from result snippets with no
images. They now get a later line, used only while neither has answered.
"""
from __future__ import annotations

import time

from syrix_v3.research import gather, lanes
from syrix_v3.research.schemas import Source


def _lane(name, delay, n=1):
    def run(query, *, timeout):
        time.sleep(min(delay, timeout))
        if delay > timeout:
            raise TimeoutError("read timed out")
        return [Source(url=f"https://{name}.example/{query[:8]}/{i}", title=f"{name} {i}",
                       snippet="", domain=f"{name}.example", provider=name, score=0.6)
                for i in range(n)]
    return run


def _fake_lanes(monkeypatch, delays):
    table = {name: _lane(name, d) for name, d in delays.items()}
    monkeypatch.setattr(lanes, "ALL", table)
    monkeypatch.setattr(lanes, "choose", lambda q, **kw: list(delays))
    monkeypatch.setattr(gather, "_log", lambda entry: None)


def test_a_slow_keyed_lane_is_waited_for_when_nothing_keyed_has_answered(monkeypatch):
    _fake_lanes(monkeypatch, {"exa": 0.9, "duckduckgo": 0.05})
    now = time.monotonic()
    found = gather.search(["lisbon"], web_mode="spark", request_id="t", limit=10,
                          deadline=now + 0.4, keyed_until=now + 1.6)
    assert {s.provider for s in found} == {"exa", "duckduckgo"}


def test_without_a_later_line_the_window_closes_on_time(monkeypatch):
    _fake_lanes(monkeypatch, {"exa": 0.9, "duckduckgo": 0.05})
    now = time.monotonic()
    found = gather.search(["lisbon"], web_mode="spark", request_id="t", limit=10,
                          deadline=now + 0.4)
    assert time.monotonic() - now < 0.8
    assert {s.provider for s in found} == {"duckduckgo"}


def test_once_one_keyed_lane_has_answered_the_other_is_not_waited_for(monkeypatch):
    _fake_lanes(monkeypatch, {"exa": 0.05, "tavily": 1.5, "duckduckgo": 0.05})
    now = time.monotonic()
    found = gather.search(["lisbon"], web_mode="spark", request_id="t", limit=10,
                          deadline=now + 0.4, keyed_until=now + 2.0)
    assert time.monotonic() - now < 0.9
    assert "tavily" not in {s.provider for s in found}


def test_sponsored_redirects_are_not_sources():
    assert lanes._source("https://www.bing.com/aclick?ld=abc&u=xyz", "Book tours", "",
                         "duckduckgo", 0.5) is None
    assert lanes._source("https://duckduckgo.com/y.js?ad_domain=viator.com", "Ad", "",
                         "duckduckgo", 0.5) is None
    kept = lanes._source("https://www.visitlisboa.com/en", "Visit Lisboa", "", "duckduckgo", 0.5)
    assert kept is not None and kept.domain.endswith("visitlisboa.com")


class _Response:
    def __init__(self, status, text=""):
        self.status_code = status
        self.text = text


class _HTTPError(Exception):
    def __init__(self, status, text=""):
        super().__init__(f"{status}")
        self.response = _Response(status, text)


def _isolated_health(monkeypatch):
    from syrix_v3.research import health
    store = {}
    monkeypatch.setattr(health.state, "read", lambda name, *p, default=None: dict(store))
    monkeypatch.setattr(health.state, "write", lambda payload, name, *p: store.update(payload) or True)
    monkeypatch.setattr(health, "_cache", {})
    monkeypatch.setattr(health, "_loaded", False)
    return health


def test_a_spent_allowance_or_refused_key_is_recorded_and_a_timeout_is_not(monkeypatch):
    health = _isolated_health(monkeypatch)
    assert health.note_failure("tavily", _HTTPError(432, "usage limit exceeded")) == "quota"
    assert health.note_failure("exa", _HTTPError(401)) == "key"
    assert health.note_failure("duckduckgo", TimeoutError("read timed out")) == ""
    assert health.note_failure("wikipedia", _HTTPError(503)) == ""
    assert health.down("tavily") and health.down("exa")
    assert not health.down("duckduckgo") and not health.down("wikipedia")


def test_tavily_is_held_in_reserve_until_exa_is_out(monkeypatch):
    health = _isolated_health(monkeypatch)
    monkeypatch.setattr(lanes.config, "secret", lambda name: "set")
    assert lanes.choose("kyoto") == ["exa", "duckduckgo", "wikipedia"]
    health.note_failure("exa", _HTTPError(402, "insufficient credits"))
    assert lanes.choose("kyoto") == ["tavily", "duckduckgo", "wikipedia"]
    health.note_failure("tavily", _HTTPError(432, "usage limit exceeded"))
    assert lanes.choose("kyoto") == ["duckduckgo", "wikipedia"]


def test_without_keys_the_keyless_lanes_still_search(monkeypatch):
    _isolated_health(monkeypatch)
    monkeypatch.setattr(lanes.config, "secret", lambda name: "")
    assert lanes.choose("kyoto") == ["duckduckgo", "wikipedia"]


def test_an_adult_result_for_an_ordinary_question_is_dropped(monkeypatch):
    def adult(query, *, timeout):
        return [Source(url="https://wildhardsex.mobi/videos/1", title="video", snippet="",
                       domain="wildhardsex.mobi", provider="duckduckgo", score=0.6),
                Source(url="https://clashofclans.fandom.com/wiki/Siege_Machines",
                       title="Siege Machines", snippet="", domain="clashofclans.fandom.com",
                       provider="duckduckgo", score=0.6)]
    monkeypatch.setattr(lanes, "ALL", {"duckduckgo": adult})
    monkeypatch.setattr(lanes, "choose", lambda q, **kw: ["duckduckgo"])
    monkeypatch.setattr(gather, "_log", lambda entry: None)
    found = gather.search(["how to fill siege machine coc"], web_mode="spark", request_id="t",
                          limit=10, deadline=time.monotonic() + 1.0)
    assert [s.domain for s in found] == ["clashofclans.fandom.com"]


def test_a_query_dated_by_the_model_is_redated_and_one_dated_by_the_user_is_not():
    from syrix_v3.research import rewrite
    year = time.localtime().tm_year
    assert rewrite.current_year(f"best phones {year - 2}", "best phones") == f"best phones {year}"
    assert rewrite.current_year(f"iphone sales {year - 2}", f"iphone sales in {year - 2}") \
        == f"iphone sales {year - 2}"
    assert rewrite.current_year("ww2 1944 battles", "ww2 battles") == "ww2 1944 battles"
