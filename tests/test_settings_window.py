import pytest

from jarvis.core import config, secrets
from jarvis.core.ui import settings_window as sw


def test_parse_helpers():
    assert sw.parse_list("a, b\nc,, a") == ["a", "b", "c"]
    assert sw.parse_pairs([("Browser ", " Chrome"), ("", "x"), ("y", "")]) == {"browser": "Chrome"}
    assert sw.parse_args_text("level=40, name=chrome") == ({"level": 40, "name": "chrome"}, None)
    assert sw.parse_args_text('{"a": 1}') == ({"a": 1}, None)
    assert sw.parse_args_text("oops")[1]
    assert sw.parse_args_text("") == ({}, None)


def test_build_cfg_valid_and_base_untouched():
    base = dict(config.DEFAULTS)
    form = {"wake_sensitivity": "0.7", "followup_seconds": "8", "personality": "casual",
            "daily_limits": {"gemini-3.8-flash": "300"}, "app_aliases": [("web", "Chrome")],
            "theme": "minimal", "start_with_windows": True, "mic_device": ""}
    cfg, errors = sw.build_cfg(base, form)
    assert errors == []
    assert cfg["wake_sensitivity"] == 0.7 and cfg["personality"] == "casual"
    assert cfg["daily_limits"]["gemini-3.8-flash"] == 300
    assert cfg["daily_limits"]["gemini-3.5-flash-lite"] == 1000
    assert cfg["app_aliases"] == {"web": "Chrome"} and cfg["start_with_windows"] is True
    assert base["wake_sensitivity"] == 0.5


@pytest.mark.parametrize("form", [
    {"wake_sensitivity": "2"}, {"wake_sensitivity": "abc"}, {"followup_seconds": "-1"},
    {"personality": "pirate"}, {"model_main": " "}, {"daily_limits": {"m": "x"}},
    {"listen_default_s": "50", "listen_max_s": "20"},
])
def test_build_cfg_rejects(form):
    _, errors = sw.build_cfg(dict(config.DEFAULTS), form)
    assert errors


def test_mask_and_usage():
    assert "1234" in sw.mask_key("abcd1234") and "abcd" not in sw.mask_key("abcd1234")
    assert sw.mask_key(None).startswith("No key")
    assert sw.format_usage([("m", 50, 100)]) == ["m: 50 of 100 today (50%)"]


def test_window_smoke(monkeypatch):
    store = {}
    monkeypatch.setattr(secrets, "get_api_key", lambda: store.get("k"))
    monkeypatch.setattr(secrets, "set_api_key", lambda k: store.__setitem__("k", k))
    try:
        win = sw.SettingsWindow(None, dict(config.DEFAULTS), {"mic_level": lambda: 0.3}, hidden=True)
    except Exception as e:  # no display / Tk problem
        pytest.skip(f"Tk unavailable: {e}")
    try:
        form = win.collect_form()
        cfg, errors = sw.build_cfg(dict(config.DEFAULTS), form)
        assert errors == []
        for k in ("personality", "reply_mode", "history_mode", "auto_look", "model_main", "wake_sensitivity",
                  "theme", "startup_greeting", "mic_device", "tts_voice"):
            assert cfg[k] == {**config.DEFAULTS, **sw.NEW_DEFAULTS}[k], k
        applied = []
        win.h._h["apply"] = applied.append
        win.save()
        assert win.result is not None and applied
        assert config.load()["theme"] == "hud"
    finally:
        win.close()
