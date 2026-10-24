"""Images, product cards and follow-ups: which answers get them, and safely.

No network: the fetch seam and the search seam are replaced where a test
needs them.
"""
from __future__ import annotations

import os

import pytest

from syrix_v3 import polish
from syrix_v3.media import Media, attach, cache, images, products


@pytest.mark.parametrize("question,count", [
    ("show me pictures of the eiffel tower at night", 8),
    ("what does a capybara look like", 8),
    ("who is jensen huang", 3),
    ("things to do in lisbon", 4),
    ("tell me about the new iphone", 0),
    ("how does a transformer model work", 0),
    ("write a python function to reverse a list", 0),
    ("why is the sky blue", 0),
    ("what is 15% of 240", 0),
])
def test_pictures_go_with_things_you_can_see(question, count):
    assert images.wanted(question) == count


def test_a_card_or_an_attachment_means_no_picture_strip():
    assert images.wanted("who is jensen huang", has_card=True) == 0
    assert images.wanted("show me this", has_attachment=True) == 0
    # Asking outright still wins over a card.
    assert images.wanted("show me photos of london", has_card=True) == 8


@pytest.mark.parametrize("url,ok", [
    ("https://cdn.example.com/photo.jpg", True),
    ("http://cdn.example.com/photo.jpg", False),
    ("https://example.com/assets/logo.png", False),
    ("https://example.com/favicon.ico", False),
    ("https://example.com/drawing.svg", False),
])
def test_logos_icons_and_svg_are_never_candidates(url, ok):
    assert images.usable(url) is ok


def test_only_real_image_bytes_are_accepted():
    assert cache.sniff(b"\xff\xd8\xff\xe0" + b"0" * 20) == "jpg"
    assert cache.sniff(b"\x89PNG\r\n\x1a\n" + b"0" * 20) == "png"
    assert cache.sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "webp"
    assert cache.sniff(b"<svg xmlns='http://www.w3.org/2000/svg'>") == ""
    assert cache.sniff(b"<!doctype html><html>") == ""


def test_a_page_that_is_not_an_image_is_never_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "_directory", lambda: str(tmp_path))
    monkeypatch.setattr(cache, "_fetch", lambda url, timeout_s: b"<html>" + b"x" * 5000)
    assert cache.localize("https://example.com/not-really.jpg") is None
    assert os.listdir(tmp_path) == []


def test_an_image_is_served_as_a_local_file(monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "_directory", lambda: str(tmp_path))
    monkeypatch.setattr(cache, "_fetch", lambda url, timeout_s: b"\xff\xd8\xff\xe0" + b"x" * 5000)
    local = cache.localize("https://example.com/photo.jpg")
    assert local and local.startswith("file:///") and local.endswith(".jpg")


def test_insecure_urls_are_refused_before_any_fetch(monkeypatch):
    def boom(url, timeout_s):
        raise AssertionError("must not fetch")
    monkeypatch.setattr(cache, "_fetch", boom)
    assert cache.localize("http://example.com/a.jpg") is None
    assert cache.localize("file:///C:/Windows/win.ini") is None


@pytest.mark.parametrize("question,shopping", [
    ("best wireless headphones under 200 pounds", True),
    ("which laptop should i buy for coding", True),
    ("cheapest air fryer that is any good", True),
    ("how do headphones cancel noise", False),
    ("best way to learn python", False),
])
def test_shopping_questions_are_recognised(question, shopping):
    assert products.wanted(question) is shopping


def test_product_names_come_from_the_recommendations_not_the_subheadings():
    answer = (
        "| Rank | Model | Strengths | Price |\n|---|---|---|---|\n"
        "| 1 | **Sony WH-CH520** – on-ear | good | £147 |\n"
        "| 2 | **Anker Soundcore Life Q30** – over-ear | ANC | £79 |\n\n"
        "**Sound quality vs. price** matters. **Feature set** too."
    )
    assert products.names_from(answer) == ["Sony WH-CH520", "Anker Soundcore Life Q30"]


def test_media_rides_on_the_existing_card_or_makes_its_own():
    media = Media(images=[{"src": "file:///x.jpg"}])
    assert attach(None, media) == {"type": "media", "media": {"images": [{"src": "file:///x.jpg"}]}}
    merged = attach({"type": "stock", "symbol": "NVDA"}, media)
    assert merged["type"] == "stock" and merged["media"]["images"]
    assert attach({"type": "weather"}, Media()) == {"type": "weather"}


def test_image_links_the_model_writes_are_removed():
    body, removed = polish.clean("Here:\n\n![Tower](https://images.unsplash.com/photo-1)\nDone.")
    assert "unsplash" not in body and removed


def test_a_misspelt_request_for_pictures_is_still_a_request():
    from syrix_v3.media import images
    q = ("can u show me iamges of the best th 12 base and tell me why is ti good "
         "and how to make it with iamge guide me")
    assert images.wanted(q) == 8
    assert images.subject_of(q) == "best th 12 base"
    assert images.wanted("imges of tokyo") == 8


def test_explicit_results_are_not_used_as_pictures():
    from syrix_v3.media import images
    assert not images.usable("https://example-porn-site.com/a.jpg")
    assert images.usable("https://upload.wikimedia.org/kyoto.jpg")
