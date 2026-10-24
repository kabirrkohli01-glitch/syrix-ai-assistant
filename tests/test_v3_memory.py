"""What SYRIX remembers, and what it refuses to let grow.

Every test here runs against a temporary state directory, so nothing
touches the real store.
"""
from __future__ import annotations

import time

import pytest

from syrix_v3.memory import recall, store
from syrix_v3.memory.store import Fact


@pytest.fixture(autouse=True)
def empty_store(tmp_path, monkeypatch):
    """Point the state directory at a fresh temporary one per test."""
    import syrix_v3.state as state

    def _directory(*parts):
        path = tmp_path.joinpath(*parts)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    monkeypatch.setattr(state, "directory", _directory)
    yield


def test_a_fact_survives_a_round_trip():
    store.remember("He trades the London open.", kind=store.KIND_PREFERENCE)
    facts = store.all_facts()
    assert len(facts) == 1
    assert facts[0].text == "He trades the London open."
    assert facts[0].kind == store.KIND_PREFERENCE
    assert facts[0].learned_at > 0


def test_provenance_is_recorded():
    """A fact without provenance cannot be judged, corrected or aged."""
    fact = store.remember("He is on Windows 11.", source="he asked you to remember it")
    assert fact.source == "he asked you to remember it"
    assert fact.learned_at > time.time() - 5


@pytest.mark.parametrize("second", [
    "He trades the London open",       # no full stop
    "he trades the london open.",      # case
    "He  trades   the London open.",   # spacing
])
def test_the_same_fact_typed_differently_is_one_fact(second):
    store.remember("He trades the London open.")
    store.remember(second)
    assert len(store.all_facts()) == 1


def test_two_different_facts_are_two_facts():
    store.remember("He trades the London open.")
    store.remember("He takes his coffee black.")
    assert len(store.all_facts()) == 2


def test_hearing_a_fact_again_refreshes_it():
    fact = store.remember("He is on Windows 11.")
    original = fact.learned_at
    time.sleep(0.01)
    store.remember("He is on Windows 11.")
    assert store.get(fact.id).learned_at > original
    assert len(store.all_facts()) == 1


def test_forgetting():
    fact = store.remember("He dislikes coriander.")
    assert store.forget(fact.id) is True
    assert store.all_facts() == []
    assert store.forget(fact.id) is False


def test_an_expired_fact_is_hidden_and_prunable():
    store.remember("He is in Dubai this week.", expires_at=time.time() - 60)
    assert store.all_facts() == []
    assert len(store.all_facts(include_stale=True)) == 1
    assert store.prune_stale() == 1
    assert store.all_facts(include_stale=True) == []


def test_replace_folds_several_facts_into_one():
    a = store.remember("He uses a Windows PC.")
    b = store.remember("His machine runs Windows.")
    merged = Fact(text="He works on a Windows 11 PC with an RTX card.",
                  kind=store.KIND_SETUP)
    store.replace([a.id, b.id], merged)
    facts = store.all_facts()
    assert len(facts) == 1
    assert facts[0].text == merged.text


# ── recall ────────────────────────────────────────────────────────────────

def test_recall_finds_the_relevant_fact():
    store.remember("He takes his coffee black.", kind=store.KIND_PREFERENCE)
    store.remember("He is building a trading terminal called VANTAGE.",
                   kind=store.KIND_PROJECT)
    found = [f.text for f, _ in recall.relevant("what am i building")]
    assert any("VANTAGE" in t for t in found)


def test_recall_ignores_the_irrelevant():
    store.remember("He is building a trading terminal called VANTAGE.",
                   kind=store.KIND_PROJECT)
    store.remember("His neighbour has a dog.", kind=store.KIND_OTHER)
    found = [f.text for f, _ in recall.relevant("what am i building")]
    assert not any("dog" in t for t in found)


def test_who_he_is_rides_along_on_every_question():
    """Standing facts apply whether or not the question mentions them."""
    store.remember("He prefers direct answers with no preamble.",
                   kind=store.KIND_PREFERENCE)
    store.remember("Nothing to do with the question.", kind=store.KIND_OTHER)
    found = [f.text for f, _ in recall.relevant("how does tls work")]
    assert any("direct answers" in t for t in found)
    assert not any("Nothing to do" in t for t in found)


def test_the_block_reads_as_notes_and_says_when():
    store.remember("He takes his coffee black.", kind=store.KIND_PREFERENCE)
    body = recall.block("how do i take my coffee")
    assert "coffee black" in body
    assert "today" in body
    assert "contradicted by what he says now" in body


def test_an_empty_store_contributes_nothing():
    assert recall.block("anything at all") == ""
    assert recall.relevant("anything at all") == []


def test_using_a_fact_is_recorded():
    """Use is the only evidence about whether a fact is worth keeping."""
    fact = store.remember("He trades the London open.", kind=store.KIND_PROJECT)
    recall.block("when do i trade")
    assert store.get(fact.id).uses >= 1


def test_a_fact_too_short_to_mean_anything_is_refused():
    assert store.remember("ok") is None
    assert store.all_facts() == []
