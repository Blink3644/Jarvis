import datetime as dt

from jarvis.core import greeting as g


def at(h):
    return dt.datetime(2026, 1, 1, h, 0)


def test_butler_by_time():
    cfg = {"personality": "butler", "user_title": "sir"}
    assert g.greeting(cfg, at(8)) == "Good morning, sir."
    assert g.greeting(cfg, at(14)) == "Good afternoon, sir."
    assert g.greeting(cfg, at(21)) == "Good evening, sir."
    assert g.greeting(cfg, at(2)) == "Good evening, sir."


def test_title_and_other_personalities():
    assert g.greeting({"user_title": "Miss Smith"}, at(9)) == "Good morning, Miss Smith."
    assert g.greeting({"personality": "direct"}, at(20)) == "Evening."
    assert "evening" in g.greeting({"personality": "casual"}, at(20))


def test_brief():
    cfg = {"home_city": "Leeds"}
    out = g.daily_brief(cfg, at(8), weather_fn=lambda c: f"It is 12 degrees in {c}.",
                        extras=[lambda: "Two events today.", lambda: None])
    assert out == "Good morning, sir. It is 12 degrees in Leeds. Two events today."
    assert g.daily_brief({}, at(8), weather_fn=lambda c: "x") == "Good morning, sir."


def test_brief_survives_errors():
    def boom(city):
        raise RuntimeError
    assert g.daily_brief({"home_city": "X"}, at(8), weather_fn=boom) == "Good morning, sir."


def test_startup_modes():
    assert g.startup_text({"startup_greeting": "off"}, at(8)) == ""
    assert g.startup_text({}, at(8)) == "Good morning, sir."
    assert g.startup_text({"startup_greeting": "brief", "home_city": "L"}, at(8),
                          weather_fn=lambda c: "Sunny.") == "Good morning, sir. Sunny."
