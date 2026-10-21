"""Clients for the external position and weather APIs.

Each function returns a dict shaped exactly like its Avro schema in schemas/,
so the producer can hand it straight to the AvroSerializer.
"""

import sys
import time

import requests

from producer.config import Settings, load_aircraft

ADSBX_HOST = "adsbexchange-com1.p.rapidapi.com"
ADSBX_HEX_URL = "https://adsbexchange-com1.p.rapidapi.com/v2/hex/"  # + "<hex>/"
TIMEOUT = 15  # seconds

_session = requests.Session()


def _now_ms() -> int:
    return int(time.time() * 1000)


def get_position(settings: Settings, hex_code: str) -> dict | None:
    """Position for one aircraft from ADS-B Exchange's /v2/hex/<hex>/ endpoint, or None if
    it returns no aircraft (parked with transponder off, out of coverage, or a wrong hex)."""
    resp = _session.get(
        f"{ADSBX_HEX_URL}{hex_code}/",
        headers={"X-RapidAPI-Key": settings.rapidapi_key, "X-RapidAPI-Host": ADSBX_HOST},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    body = resp.json()

    # /v2/hex/ returns the aircraft object itself; some ADSBx endpoints wrap it in {"ac": [...]}.
    if "ac" in body:
        ac = (body.get("ac") or [None])[0]
    else:
        ac = body if "hex" in body else None
    if not ac:
        return None

    # A current fix has lat/lon at the top level. If the aircraft hasn't reported a position
    # recently (e.g. parked), only `lastPosition` is present — use it; `ts` shows how old it is.
    fix = ac if ac.get("lat") is not None else ac.get("lastPosition")
    if not fix or fix.get("lat") is None or fix.get("lon") is None:
        return None

    # alt_baro is an int in feet, or the string "ground" when the plane is on the ground.
    alt = ac.get("alt_baro")
    on_ground = alt == "ground"

    # `now` is the server time (ms); `seen_pos` is how many seconds old the position is.
    observed_ms = int(body.get("now", _now_ms()) - float(fix.get("seen_pos", 0)) * 1000)

    return {
        "hex": ac["hex"].lower(),
        "flight": (ac.get("flight") or "").strip() or None,  # callsigns come space-padded
        "lat": float(fix["lat"]),
        "lon": float(fix["lon"]),
        "altitude_ft": 0 if on_ground else (int(alt) if isinstance(alt, (int, float)) else None),
        "ground_speed_kt": _float_or_none(ac.get("gs")),
        "track_deg": _float_or_none(ac.get("track")),
        "on_ground": on_ground,
        "ts": observed_ms,
    }


def get_weather(settings: Settings, hex_code: str, lat: float, lon: float) -> dict:
    """Current weather at a point, from OpenWeather's free Current Weather API."""
    resp = _session.get(
        "https://api.openweathermap.org/data/2.5/weather",
        params={"lat": lat, "lon": lon, "units": "metric", "appid": settings.openweather_api_key},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    w = resp.json()
    main, wind = w.get("main", {}), w.get("wind", {})

    return {
        "hex": hex_code,
        "lat": lat,
        "lon": lon,
        "temp_c": float(main["temp"]),
        "feels_like_c": _float_or_none(main.get("feels_like")),
        "humidity_pct": main.get("humidity"),
        "description": (w.get("weather") or [{}])[0].get("description", "unknown"),
        "wind_speed_ms": _float_or_none(wind.get("speed")),
        "wind_deg": wind.get("deg"),
        "ts": int(w.get("dt", time.time())) * 1000,  # OpenWeather reports seconds
    }


def _float_or_none(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


if __name__ == "__main__":
    # Manual check: python -m producer.apis <hex>   (defaults to the first aircraft in config/billionaires.csv)
    settings = Settings.from_env()
    hex_code = (sys.argv[1] if len(sys.argv) > 1 else next(iter(load_aircraft()), "")).lower()
    if not hex_code:
        sys.exit("usage: python -m producer.apis <icao_hex>   (or add one to config/billionaires.csv)")

    position = get_position(settings, hex_code)
    print("position:", position)
    if position is None:
        sys.exit(f"/v2/hex/{hex_code}/ returned no aircraft — pick an airborne aircraft on globe.adsbexchange.com")

    print("weather: ", get_weather(settings, hex_code, position["lat"], position["lon"]))
