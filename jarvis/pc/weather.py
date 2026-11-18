"""Weather via Open-Meteo (free, no key)."""
import logging

try:
    import httpx
except ImportError:      # google-genai 3.x ships the same API as httpx2
    import httpx2 as httpx

from jarvis.core.registry import Registry, Tool

log = logging.getLogger(__name__)

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT = 6.0

_geo_cache: dict[str, dict] = {}

_WMO = {
    0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "freezing drizzle", 57: "heavy freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "showers", 82: "heavy showers",
    85: "light snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}


def describe_code(code) -> str:
    try:
        return _WMO.get(int(code), "unknown conditions")
    except (TypeError, ValueError):
        return "unknown conditions"


def _geocode(city: str) -> dict | None:
    key = city.strip().lower()
    if key in _geo_cache:
        return _geo_cache[key]
    r = httpx.get(GEO_URL, params={"name": city, "count": 1, "language": "en", "format": "json"},
                  timeout=TIMEOUT)
    r.raise_for_status()
    results = r.json().get("results") or []
    if not results:
        return None
    g = results[0]
    place = {"name": g.get("name", city), "country": g.get("country", ""),
             "lat": g["latitude"], "lon": g["longitude"]}
    _geo_cache[key] = place
    return place


def make_get_weather(cfg: dict):
    def get_weather(city: str = "", days: int = 1) -> dict:
        city = (city or "").strip() or (cfg.get("home_city") or "").strip()
        if not city:
            return {"error": "No city given and no home city is set. Ask the user for a city, "
                             "or tell them to set their home city in settings."}
        try:
            days = max(1, min(7, int(days or 1)))
        except (TypeError, ValueError):
            days = 1
        try:
            place = _geocode(city)
            if not place:
                return {"error": f"I couldn't find a place called {city}."}
            r = httpx.get(FORECAST_URL, params={
                "latitude": place["lat"], "longitude": place["lon"],
                "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
                "timezone": "auto", "forecast_days": days,
                "temperature_unit": "celsius", "wind_speed_unit": "kmh",
            }, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
            return {"error": "I can't reach the weather service. The internet may be down."}
        except httpx.HTTPError as e:
            log.warning("weather request failed: %s", e)
            return {"error": "The weather service gave an error. Try again in a moment."}

        cur = data.get("current", {})
        d = data.get("daily", {})
        out = {
            "place": f"{place['name']}, {place['country']}".strip(", "),
            "now": {
                "temperature_c": cur.get("temperature_2m"),
                "feels_like_c": cur.get("apparent_temperature"),
                "conditions": describe_code(cur.get("weather_code")),
                "wind_kmh": cur.get("wind_speed_10m"),
            },
            "forecast": [],
        }
        dates = d.get("time", [])
        for i, date in enumerate(dates[:days]):
            def at(k):
                v = d.get(k) or []
                return v[i] if i < len(v) else None
            out["forecast"].append({
                "date": date, "high_c": at("temperature_2m_max"), "low_c": at("temperature_2m_min"),
                "rain_chance_percent": at("precipitation_probability_max"),
                "conditions": describe_code(at("weather_code")),
            })
        return out
    return get_weather


def register(reg: Registry, cfg: dict) -> None:
    reg.register(Tool(
        name="get_weather", side_effect=False,
        description="Get the current weather and a forecast of 1-7 days. Uses the user's home city "
                    "unless a city is given. Temperatures in Celsius, wind in km/h.",
        handler=make_get_weather(cfg),
        parameters={"type": "object", "properties": {
            "city": {"type": "string", "description": "City name; leave empty for the home city."},
            "days": {"type": "integer", "description": "Forecast days, 1 to 7 (default 1)."},
        }},
        module="pc", visual=False,
    ))
