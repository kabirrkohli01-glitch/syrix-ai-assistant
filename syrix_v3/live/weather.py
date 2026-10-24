"""Weather from Open-Meteo: exact figures, a named place, a timestamp.

Free, keyless and fast (about 0.3s for geocoding and 0.4s for a forecast).
Read directly rather than scraped from a weather site, because a scraped page
gives the model two sites' readings to reconcile — measured, it answered
"23 °C (Euronews) vs 22 °C (Ventusky)" with the wind "north-bound", while the
card under it showed a third reading from a third source.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from .. import config
import syrix_obs

_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST = "https://api.open-meteo.com/v1/forecast"
_SOURCE = {"url": "https://open-meteo.com", "title": "Open-Meteo", "provider": "open-meteo.com"}

# WMO weather interpretation codes, as Open-Meteo reports them.
_CODES = {
    0: ("Clear sky", "☀️"), 1: ("Mainly clear", "🌤️"), 2: ("Partly cloudy", "⛅"),
    3: ("Overcast", "☁️"), 45: ("Fog", "🌫️"), 48: ("Freezing fog", "🌫️"),
    51: ("Light drizzle", "🌦️"), 53: ("Drizzle", "🌦️"), 55: ("Heavy drizzle", "🌧️"),
    56: ("Freezing drizzle", "🌧️"), 57: ("Freezing drizzle", "🌧️"),
    61: ("Light rain", "🌦️"), 63: ("Rain", "🌧️"), 65: ("Heavy rain", "🌧️"),
    66: ("Freezing rain", "🌧️"), 67: ("Freezing rain", "🌧️"),
    71: ("Light snow", "🌨️"), 73: ("Snow", "🌨️"), 75: ("Heavy snow", "❄️"),
    77: ("Snow grains", "🌨️"), 80: ("Light showers", "🌦️"), 81: ("Showers", "🌧️"),
    82: ("Violent showers", "⛈️"), 85: ("Snow showers", "🌨️"), 86: ("Heavy snow showers", "❄️"),
    95: ("Thunderstorm", "⛈️"), 96: ("Thunderstorm with hail", "⛈️"),
    99: ("Thunderstorm with heavy hail", "⛈️"),
}

_COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def describe(code: Any) -> Tuple[str, str]:
    try:
        return _CODES.get(int(code), ("Unknown", "🌡️"))
    except (TypeError, ValueError):
        return ("Unknown", "🌡️")


def compass(degrees: Any) -> str:
    try:
        return _COMPASS[int((float(degrees) % 360) / 22.5 + 0.5) % 16]
    except (TypeError, ValueError):
        return ""


def geocode(place: str, timeout_s: float = 3.0) -> Optional[Dict[str, Any]]:
    """The most likely place for a name, or None.

    "London, Ontario" narrows by the part after the comma; a bare "London"
    takes the most populous match, which is the one people mean.
    """
    name, _, qualifier = str(place or "").partition(",")
    name, qualifier = name.strip(), qualifier.strip().lower()
    if not name:
        return None
    resp = requests.get(_GEOCODE, params={"name": name, "count": 10, "language": "en",
                                          "format": "json"}, timeout=timeout_s)
    resp.raise_for_status()
    results = resp.json().get("results") or []
    if qualifier:
        narrowed = [r for r in results if qualifier in " ".join(
            str(r.get(k) or "") for k in ("country", "admin1", "admin2", "country_code")).lower()]
        results = narrowed or results
    if not results:
        return None
    results.sort(key=lambda r: -(r.get("population") or 0))
    return results[0]


def _place_label(hit: Dict[str, Any]) -> str:
    parts = [hit.get("name"), hit.get("admin1"), hit.get("country")]
    seen: List[str] = []
    for part in parts:
        if part and part not in seen:
            seen.append(str(part))
    return ", ".join(seen)


def _home() -> Optional[Dict[str, Any]]:
    lat, lon = config.get("weather_lat"), config.get("weather_lon")
    city = str(config.get("weather_city") or "").strip()
    if city:
        try:
            hit = geocode(city)
            if hit:
                return hit
        except requests.RequestException as exc:
            syrix_obs.report("v3.live.weather._home", exc, level="debug")
    if lat is not None and lon is not None:
        return {"name": city or "Home", "latitude": float(lat), "longitude": float(lon),
                "timezone": "auto"}
    return None


def fetch(place: str, *, days: int = 3, timeout_s: float = 4.0) -> Optional[Dict[str, Any]]:
    """Current conditions and a short forecast. Raises on network failure."""
    hit = geocode(place, timeout_s) if place else _home()
    if not hit:
        return None
    params = {
        "latitude": hit["latitude"], "longitude": hit["longitude"],
        "current": ("temperature_2m,apparent_temperature,relative_humidity_2m,"
                    "weather_code,wind_speed_10m,wind_direction_10m,precipitation,is_day"),
        "hourly": "temperature_2m,precipitation_probability,weather_code",
        "daily": ("weather_code,temperature_2m_max,temperature_2m_min,"
                  "precipitation_probability_max,sunrise,sunset,uv_index_max"),
        "forecast_days": max(1, min(7, int(days))),
        "timezone": "auto",
    }
    resp = requests.get(_FORECAST, params=params, timeout=timeout_s)
    resp.raise_for_status()
    data = resp.json()
    data["_place"] = _place_label(hit)
    return data


def _daily_rows(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    daily = data.get("daily") or {}
    rows = []
    for i, day in enumerate(daily.get("time") or []):
        cond, icon = describe((daily.get("weather_code") or [None])[i])
        rows.append({
            "date": day,
            "day": time.strftime("%a", time.strptime(day, "%Y-%m-%d")),
            "high_c": (daily.get("temperature_2m_max") or [None])[i],
            "low_c": (daily.get("temperature_2m_min") or [None])[i],
            "rain_pct": (daily.get("precipitation_probability_max") or [None])[i],
            "condition": cond, "icon": icon,
            "sunrise": str((daily.get("sunrise") or [""])[i])[-5:],
            "sunset": str((daily.get("sunset") or [""])[i])[-5:],
            "uv": (daily.get("uv_index_max") or [None])[i],
        })
    return rows


def _hourly_rows(data: Dict[str, Any], hours: int = 12) -> List[Dict[str, Any]]:
    hourly = data.get("hourly") or {}
    now = str((data.get("current") or {}).get("time") or "")[:13]
    times = hourly.get("time") or []
    start = next((i for i, t in enumerate(times) if t[:13] >= now), 0)
    rows = []
    for i in range(start, min(len(times), start + hours)):
        cond, icon = describe((hourly.get("weather_code") or [None])[i])
        rows.append({
            "time": times[i][-5:],
            "temp_c": (hourly.get("temperature_2m") or [None])[i],
            "rain_pct": (hourly.get("precipitation_probability") or [None])[i],
            "icon": icon,
        })
    return rows


def as_fact(data: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """The evidence block and the card, from one reading."""
    current = data.get("current") or {}
    units = data.get("current_units") or {}
    cond, icon = describe(current.get("weather_code"))
    place = data.get("_place") or "the requested place"
    days = _daily_rows(data)
    hours = _hourly_rows(data)
    wind = current.get("wind_speed_10m")
    wind_from = compass(current.get("wind_direction_10m"))

    lines = [
        f"LIVE WEATHER — {place} (Open-Meteo, reading at {current.get('time', '?')} "
        f"local time, timezone {data.get('timezone', '?')})",
        f"Now: {current.get('temperature_2m')}{units.get('temperature_2m', '°C')}, "
        f"feels like {current.get('apparent_temperature')}°C, {cond.lower()}, "
        f"humidity {current.get('relative_humidity_2m')}%, wind {wind} km/h from the "
        f"{wind_from}, precipitation {current.get('precipitation')} mm in the last interval.",
    ]
    for i, row in enumerate(days):
        label = "Today" if i == 0 else ("Tomorrow" if i == 1 else f"{row['day']} {row['date']}")
        lines.append(
            f"{label}: {row['condition'].lower()}, high {row['high_c']}°C, low {row['low_c']}°C, "
            f"chance of rain {row['rain_pct']}%, UV index {row['uv']}, sunrise {row['sunrise']}, "
            f"sunset {row['sunset']}.")
    if hours:
        lines.append("Next hours: " + "; ".join(
            f"{h['time']} {h['temp_c']}°C rain {h['rain_pct']}%" for h in hours[:8]) + ".")
    block = "\n".join(lines)

    card = {
        "type": "weather",
        "location": place,
        "temp_c": current.get("temperature_2m"),
        "feels_c": current.get("apparent_temperature"),
        "condition": cond,
        "icon": icon,
        "humidity": current.get("relative_humidity_2m"),
        "wind_kmh": wind,
        "wind_from": wind_from,
        "high_c": days[0]["high_c"] if days else None,
        "low_c": days[0]["low_c"] if days else None,
        "hourly": hours[:8],
        "daily": days,
        "observed": current.get("time"),
        "sources": [_SOURCE],
    }
    return block, card


def source() -> Dict[str, str]:
    return dict(_SOURCE)
