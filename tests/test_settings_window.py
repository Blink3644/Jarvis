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


def test_move_item_for_provider_order():
    assert sw.move_item(["a", "b", "c"], 2, -1) == ["a", "c", "b"]
    assert sw.move_item(["a", "b"], 0, -1) == ["a", "b"]
    assert sw.move_item(["a", "b"], 1, 1) == ["a", "b"]


def test_build_cfg_live_mode():
    assert config.DEFAULTS["live_mode"] is False            # Live is off by default
    cfg, errors = sw.build_cfg(dict(config.DEFAULTS), {"live_mode": True, "live_model": " ",
                                                       "live_idle_seconds": "30"})
    assert errors == [] and cfg["live_mode"] is True
    assert cfg["live_model"] == "gemini-3.8-live" and cfg["live_idle_seconds"] == 30
    _, errors = sw.build_cfg(dict(config.DEFAULTS), {"live_idle_seconds": "1"})
    assert errors


def test_window_shows_found_update(monkeypatch):
    from jarvis.core import updater
    info = updater.UpdateInfo("9.9.9", "Better wake word.", "https://x", None, None, "https://p")
    monkeypatch.setattr(updater, "why_cant_update", lambda folder=None: None)
    try:
        win = sw.SettingsWindow(None, dict(config.DEFAULTS), {"update_info": lambda: info}, hidden=True)
    except Exception as e:  # no display / Tk problem
        pytest.skip(f"Tk unavailable: {e}")
    try:
        assert "9.9.9 is available" in win.upd_label.cget("text")
        assert win.upd_btn.cget("state") == "normal" and win.upd_btn.cget("text") == "Update to 9.9.9"
    finally:
        win.close()


def test_knows_tab_lists_edits_deletes_and_forgets(monkeypatch):
    from jarvis.pc import profile
    profile.add_facts(["The user's dog is called Max.", "The user plays chess on Sundays."])
    hooks = {"profile_facts": profile.load_facts, "profile_update": profile.update_fact,
             "profile_delete": profile.delete_fact, "profile_forget_all": profile.forget_all}
    try:
        win = sw.SettingsWindow(None, dict(config.DEFAULTS), hooks, hidden=True)
    except Exception as e:  # no display / Tk problem
        pytest.skip(f"Tk unavailable: {e}")
    try:
        assert "OFF" in win.knows_state.cget("text") and "tick box above" in win.knows_state.cget("text")
        win.vars["learn_about_me"].set(True)                 # follows the Learn about me tick box
        assert "ON" in win.knows_state.cget("text")
        assert len(win.knows_frame.winfo_children()) == 2
        fid = profile.load_facts()[0]["id"]
        win._knows_edit(fid, type("V", (), {"get": lambda self: "The user's dog is called Rex."})())
        assert profile.load_facts()[0]["text"] == "The user's dog is called Rex."
        win._knows_edit(fid, type("V", (), {"get": lambda self: "my password is hunter2!"})())
        assert "password" in win.status.cget("text") and profile.load_facts()[0]["text"].endswith("Rex.")
        win._knows_delete(fid)
        assert len(profile.load_facts()) == 1 and len(win.knows_frame.winfo_children()) == 1
        monkeypatch.setattr(win, "_ask_yes", lambda *a: False)     # "Are you sure?" -> No keeps them
        win._knows_forget_all()
        assert len(profile.load_facts()) == 1
        monkeypatch.setattr(win, "_ask_yes", lambda *a: True)
        win._knows_forget_all()
        assert profile.load_facts() == [] and win.knows_forget_btn.cget("state") == "disabled"
    finally:
        win.close()


def test_knows_tab_without_hooks(monkeypatch):
    try:
        win = sw.SettingsWindow(None, dict(config.DEFAULTS), {}, hidden=True)
    except Exception as e:  # no display / Tk problem
        pytest.skip(f"Tk unavailable: {e}")
    try:
        assert win.knows_forget_btn.cget("state") == "disabled"
    finally:
        win.close()


def test_knows_tab_shows_and_changes_a_facts_game():
    from jarvis.pc import profile
    profile.add_facts(["The user's dog is called Max.", ("The user is doing a strength build.", "Elden Ring")])
    hooks = {"profile_facts": profile.load_facts, "profile_update": profile.update_fact}
    try:
        win = sw.SettingsWindow(None, dict(config.DEFAULTS), hooks, hidden=True)
    except Exception as e:  # no display / Tk problem
        pytest.skip(f"Tk unavailable: {e}")
    try:
        rows = win.knows_frame.winfo_children()
        boxes = [w for r in rows for w in r.winfo_children() if w.__class__.__name__ == "CTkComboBox"]
        assert [b.get() for b in boxes] == ["All games", "Elden Ring"]    # general first, then by game
        v = lambda s: type("V", (), {"get": lambda self: s})()           # noqa: E731
        fid = [f for f in profile.load_facts() if f.get("game")][0]["id"]
        win._knows_edit(fid, v("The user is doing a strength build."), v("Elden Ring Nightreign"))
        assert profile.load_facts()[1]["game"] == "Elden Ring Nightreign"
        win._knows_edit(fid, v("The user is doing a strength build."), v("All games"))
        assert "game" not in profile.load_facts()[1]
    finally:
        win.close()


# Every setting the old 10-tab window had (form keys); the sidebar layout must keep all of them.
OLD_FORM_KEYS = {
    "auto_look", "calendar_enabled", "clickup_enabled", "followup_seconds", "history_mode", "home_city",
    "keep_debug_clips", "learn_about_me", "listen_default_s", "listen_max_s", "live_idle_seconds", "live_mode",
    "live_model", "model_fallback", "model_main", "obs_port", "personality", "ptt_enabled", "ptt_key",
    "reply_mode", "smart_tools", "spotify_client_id", "start_with_windows", "startup_greeting",
    "stop_word_enabled", "stt_gpu", "theme", "user_title", "wake_enabled", "mic_device", "speaker_device",
    "wake_sensitivity", "wake_model", "tts_voice", "stt_model", "daily_limits", "app_aliases", "game_list",
    "never_screenshot", "virtual_inputs", "clickup_team_id",
}


def test_find_settings():
    index = [("General", "General"), ("General", "Personality"), ("Voice", "Wake word sensitivity"),
             ("Privacy & memory", "Let Jarvis look at your screen"), ("Voice", "Wake word on")]
    assert sw.find_settings(index, "") == [] and sw.find_settings(index, "   ") == []
    assert sw.find_settings(index, "WAKE") == [("Voice", "Wake word sensitivity"), ("Voice", "Wake word on")]
    assert sw.find_settings(index, "word sens") == [("Voice", "Wake word sensitivity")]   # every word must match
    assert sw.find_settings(index, "screen")[0][0] == "Privacy & memory"
    assert sw.find_settings(index, "pirate") == []


def test_help_texts_cover_the_confusing_options():
    for key in ("auto_look", "smart_tools", "history_mode", "learn_about_me", "wake_sensitivity", "live_mode",
                "stt_gpu", "virtual_mics"):
        assert len(sw.HELP_TEXTS[key]) > 40, key
    assert sw.PAGES[-1] in sw.PLACEHOLDER_PAGES                     # Minecraft placeholder is last


def test_sidebar_keeps_every_setting_and_search_jumps(monkeypatch):
    try:
        win = sw.SettingsWindow(None, dict(config.DEFAULTS), {}, hidden=True)
    except Exception as e:  # no display / Tk problem
        pytest.skip(f"Tk unavailable: {e}")
    try:
        assert set(win.pages) == set(sw.PAGES) and win.page == "General"
        missing = OLD_FORM_KEYS - set(win.collect_form())
        assert not missing, missing
        win.search_var.set("screenshot")                             # never-screenshot list
        assert win.page == "Privacy & memory" and "match" in win.search_info.cget("text")
        assert win._lit                                               # matching labels lit up
        win.search_var.set("sensitivity")
        assert win.page == "Voice" and win._on_voice_tab
        win.search_var.set("")
        assert win._lit == [] and win.search_info.cget("text") == ""
        win.search_var.set("zzzz")
        assert win.search_info.cget("text") == "Nothing found" and win.page == "Voice"
        shown = []
        monkeypatch.setattr(win, "_show_help", shown.append)
        helps = [b for page in win.pages.values() for b in _all_widgets(page)
                 if b.__class__.__name__ == "CTkButton" and b.cget("text") == "?"]
        assert len(helps) >= 7                                        # virtual mics only when a virtual input exists
        win.show_page("General")
        assert not win._on_voice_tab
    finally:
        win.close()


def _all_widgets(w):
    for c in w.winfo_children():
        yield c
        yield from _all_widgets(c)
