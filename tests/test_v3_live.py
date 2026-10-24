"""Live readings: which questions are read from a source, and how.

No network. Detection is pure; the formatting tests feed recorded payloads.
The table of questions that must NOT be caught matters more than the one
that must: a wrongly caught question gets a weather report or a share price
instead of an answer.
"""
from __future__ import annotations

import pytest

from syrix_v3 import live
from syrix_v3.live import markets, weather


@pytest.mark.parametrize("question,kind,subjects", [
    ("What's the weather in London today?", live.WEATHER, ["London"]),
    ("weather in paris tomorrow", live.WEATHER, ["paris"]),
    ("london weather", live.WEATHER, ["london"]),
    ("will it rain tomorrow", live.WEATHER, []),
    ("is it raining in manchester", live.WEATHER, ["manchester"]),
    ("What is the weather in London, Ontario?", live.WEATHER, ["London, Ontario"]),
    ("How is NVDA doing today?", live.QUOTE, ["NVDA"]),
    ("price of rolls royce shares", live.QUOTE, ["?rolls royce"]),
    ("tesla stock price", live.QUOTE, ["?tesla"]),
    ("how's the FTSE doing", live.QUOTE, ["^FTSE"]),
    ("how is the stock market doing today", live.QUOTE, ["^GSPC", "^FTSE"]),
    ("bitcoin price", live.CRYPTO, ["BTC-USD"]),
    ("eth price in pounds", live.CRYPTO, ["ETH-GBP"]),
    ("gbp to usd", live.FX, ["GBPUSD=X"]),
    ("how much is 100 usd in gbp", live.FX, ["USDGBP=X"]),
    ("convert £50 to euros", live.FX, ["GBPEUR=X"]),
    ("what time is it in tokyo", live.CLOCK, ["tokyo"]),
])
def test_questions_with_an_exact_answer_are_read_from_the_source(question, kind, subjects):
    ask = live.detect(question)
    assert ask is not None, question
    assert ask.kind == kind
    assert ask.subjects == subjects


@pytest.mark.parametrize("question", [
    "what causes rain",
    "what is bitcoin",
    "tell me about the stock market crash of 1929",
    "how is kabirr doing",
    "is AWS down today",
    "explain how a transformer works",
    "what is the capital of france",
    "can u tell me the top 10 uk unis ranking",
    "best laptops under 1000",
    "why did the weather channel change its logo in 2015",
])
def test_everything_else_is_left_alone(question):
    assert live.detect(question) is None, question


def test_a_conversion_carries_the_amount():
    ask = live.detect("how much is 250 quid in dollars")
    assert ask.kind == live.FX
    assert ask.convert == (250.0, "GBP", "USD")


def test_a_price_question_needs_no_search_but_a_how_is_it_doing_one_does():
    assert live.detect("bitcoin price").complete
    assert not live.detect("How is NVDA doing today?").complete


def test_a_name_that_is_not_the_listing_is_rejected():
    assert live._same_name("rolls royce", {"name": "Rolls-Royce Holdings plc", "symbol": "RR.L"})
    assert live._same_name("apple", {"name": "Apple Inc.", "symbol": "AAPL"})
    assert not live._same_name("kabirr", {"name": "Kabir Mills Ltd", "symbol": "KBM"})


def _weather_payload():
    return {
        "_place": "London, England, United Kingdom",
        "timezone": "Europe/London",
        "current": {"time": "2026-09-23T16:45", "temperature_2m": 22.3,
                    "apparent_temperature": 20.3, "relative_humidity_2m": 51,
                    "weather_code": 2, "wind_speed_10m": 17.3, "wind_direction_10m": 331,
                    "precipitation": 0.0},
        "current_units": {"temperature_2m": "°C"},
        "daily": {"time": ["2026-09-23", "2026-09-24"], "weather_code": [3, 61],
                  "temperature_2m_max": [23.6, 19.0], "temperature_2m_min": [16.4, 12.0],
                  "precipitation_probability_max": [0, 70],
                  "sunrise": ["2026-09-23T06:48", "2026-09-24T06:49"],
                  "sunset": ["2026-09-23T18:56", "2026-09-24T18:54"],
                  "uv_index_max": [3.0, 2.0]},
        "hourly": {"time": ["2026-09-23T16:00", "2026-09-23T17:00"],
                   "temperature_2m": [22.3, 21.8], "precipitation_probability": [0, 5],
                   "weather_code": [2, 2]},
    }


def test_the_weather_evidence_and_card_are_one_reading():
    block, card = weather.as_fact(_weather_payload())
    assert "22.3°C" in block and "London" in block and "Open-Meteo" in block
    assert "Tomorrow: light rain" in block and "chance of rain 70%" in block
    assert card["type"] == "weather" and card["temp_c"] == 22.3
    assert card["condition"] == "Partly cloudy" and card["wind_from"] == "NNW"
    assert card["daily"][1]["rain_pct"] == 70


def test_a_london_price_in_pence_is_reported_in_pounds(monkeypatch):
    def fake_chart(symbol, range_, interval, timeout_s):
        return {"meta": {"symbol": "RR.L", "currency": "GBp", "regularMarketPrice": 1503.0,
                         "chartPreviousClose": 1482.0, "longName": "Rolls-Royce Holdings plc",
                         "exchangeName": "LSE", "regularMarketTime": 1790178666},
                "indicators": {"quote": [{"close": [1482.0, 1490.0, 1503.0]}]}}
    monkeypatch.setattr(markets, "_chart", fake_chart)
    q = markets.quote("RR.L")
    assert q["currency"] == "GBP"
    assert q["price"] == pytest.approx(15.03)
    block, card = markets.as_fact(q)
    assert "£15.03" in block and card["currency"] == "£"


def test_a_conversion_is_computed_not_left_to_the_model(monkeypatch):
    def fake_chart(symbol, range_, interval, timeout_s):
        return {"meta": {"symbol": "GBPUSD=X", "currency": "USD", "regularMarketPrice": 1.33,
                         "chartPreviousClose": 1.32, "instrumentType": "CURRENCY",
                         "regularMarketTime": 1790178666},
                "indicators": {"quote": [{"close": [1.32, 1.33]}]}}
    monkeypatch.setattr(markets, "_chart", fake_chart)
    block, card = markets.as_fact(markets.quote("GBPUSD=X"), convert=(250.0, "GBP", "USD"))
    assert "250.00 GBP = 332.50 USD" in block
    assert card["conversion"]["result"] == 332.5
    # A rate is a ratio; it is not priced in dollars.
    assert "$1.33" not in block


def test_the_card_carries_every_range_for_its_tabs(monkeypatch):
    asked = []

    def fake_chart(symbol, range_, interval, timeout_s):
        asked.append((range_, interval))
        closes = [100.0 + i for i in range(300)] if range_ == "6mo" else [100.0, 101.0, 102.0]
        return {"meta": {"symbol": "NVDA", "currency": "USD", "regularMarketPrice": 102.0,
                         "chartPreviousClose": 100.0, "regularMarketTime": 1790178666,
                         "gmtoffset": -14400},
                "timestamp": [1790150000 + 300 * i for i in range(len(closes))],
                "indicators": {"quote": [{"close": closes, "open": [99.5] + closes[1:]}]}}
    monkeypatch.setattr(markets, "_chart", fake_chart)
    _, card = markets.as_fact(markets.quote("NVDA"))
    assert sorted(asked) == sorted([("1d", "5m"), ("5d", "15m"), ("1mo", "1d"),
                                    ("6mo", "1d"), ("1y", "1wk")])
    assert list(card["ranges"]) == ["1D", "5D", "1M", "6M", "1Y"]
    # Long series are thinned for the card but keep their last point.
    six = card["ranges"]["6M"]
    assert len(six["series"]) <= 131 and six["series"][-1] == 399.0
    # Times are the exchange's local time.
    assert card["ranges"]["1D"]["times"][0] == 1790150000 - 14400
    assert card["open"] == 99.5 and card["year_change_pct"] == pytest.approx(2.0)


def test_a_missing_range_does_not_lose_the_quote(monkeypatch):
    def fake_chart(symbol, range_, interval, timeout_s):
        if range_ == "1y":
            raise ValueError("no chart")
        return {"meta": {"symbol": "NVDA", "currency": "USD", "regularMarketPrice": 102.0,
                         "chartPreviousClose": 100.0, "regularMarketTime": 1790178666},
                "timestamp": [1, 2, 3],
                "indicators": {"quote": [{"close": [100.0, 101.0, 102.0]}]}}
    monkeypatch.setattr(markets, "_chart", fake_chart)
    _, card = markets.as_fact(markets.quote("NVDA"))
    assert card["price"] == 102.0 and "1Y" not in card["ranges"] and "1D" in card["ranges"]
