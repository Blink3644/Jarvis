from jarvis.pc.weather import httpx

from jarvis.core.registry import Registry
from jarvis.pc import weather

GEO = {"results": [{"name": "London", "country": "United Kingdom", "latitude": 51.5, "longitude": -0.12}]}
FC = {"current": {"temperature_2m": 12.3, "apparent_temperature": 10.1, "weather_code": 61, "wind_speed_10m": 15},
      "daily": {"time": ["2026-11-17", "2026-11-18"], "temperature_2m_max": [13, 11],
                "temperature_2m_min": [7, 5], "precipitation_probability_max": [80, 20],
                "weather_code": [61, 3]}}


class Resp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def setup(monkeypatch, calls):
    weather._geo_cache.clear()

    def fake_get(url, params=None, timeout=None):
        calls.append((url, params))
        return Resp(GEO if "geocoding" in url else FC)
    monkeypatch.setattr(weather.httpx, "get", fake_get)


def test_forecast_and_geocode_cache(monkeypatch):
    calls = []
    setup(monkeypatch, calls)
    reg = Registry()
    weather.register(reg, {"home_city": "London"})
    r = reg.call("get_weather", {"days": 2})
    assert r["now"]["conditions"] == "light rain" and len(r["forecast"]) == 2
    assert r["forecast"][1]["conditions"] == "overcast"
    assert calls[1][1]["timezone"] == "auto" and calls[1][1]["forecast_days"] == 2
    reg.call("get_weather", {})
    assert sum("geocoding" in c[0] for c in calls) == 1


def test_no_city(monkeypatch):
    setup(monkeypatch, [])
    reg = Registry()
    weather.register(reg, {"home_city": ""})
    assert "home city" in reg.call("get_weather", {})["error"]


def test_unknown_city(monkeypatch):
    monkeypatch.setattr(weather.httpx, "get", lambda *a, **k: Resp({}))
    weather._geo_cache.clear()
    reg = Registry()
    weather.register(reg, {"home_city": ""})
    assert "couldn't find" in reg.call("get_weather", {"city": "Zzzz"})["error"]


def test_offline(monkeypatch):
    weather._geo_cache.clear()

    def boom(*a, **k):
        raise httpx.ConnectError("no net")
    monkeypatch.setattr(weather.httpx, "get", boom)
    reg = Registry()
    weather.register(reg, {"home_city": "London"})
    assert "internet" in reg.call("get_weather", {})["error"]


def test_codes():
    assert weather.describe_code(95) == "thunderstorm"
    assert weather.describe_code(None) == "unknown conditions"
