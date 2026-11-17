import json
import shutil
from pathlib import Path

from jarvis.core.registry import Registry
from jarvis.pc import apps, libraries

FIX = Path(__file__).parent / "fixtures" / "apps"


def make_steam(tmp_path):
    steam = tmp_path / "Steam"
    sa = steam / "steamapps"
    sa.mkdir(parents=True)
    vdf = (FIX / "libraryfolders.vdf").read_text().replace("@LIB@", str(steam).replace("\\", "\\\\"))
    (sa / "libraryfolders.vdf").write_text(vdf)
    for f in FIX.glob("appmanifest_*.acf"):
        shutil.copy(f, sa / f.name)
    return steam


def test_vdf_parser():
    d = libraries.parse_vdf('"a" { "b" "c\\\\d" "n" { "x" "1" } }')
    assert d == {"a": {"b": "c\\d", "n": {"x": "1"}}}


def test_steam_games(tmp_path):
    games = libraries.steam_games(make_steam(tmp_path))
    assert [g["name"] for g in games] == ["Hollow Test: Silksong"]     # redistributable skipped, no duplicates
    assert games[0]["launch"] == "steam://rungameid/100"
    assert games[0]["source"] == "steam"


def test_epic_games(tmp_path):
    shutil.copy(FIX / "sample.item", tmp_path / "a.item")
    (tmp_path / "bad.item").write_text("{not json")
    g = libraries.epic_games(tmp_path)
    assert len(g) == 1 and g[0]["name"] == "Rocket League"
    assert g[0]["launch"].endswith("9773aa1aa54f4f7b80e44bef04986cea%3A530145df28a24424923f5828cc9031a1%3ASugar"
                                   "?action=launch&silent=true")


def test_installed_games_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(libraries, "steam_games", lambda: calls.append(1) or [])
    monkeypatch.setattr(libraries, "epic_games", lambda: [])
    libraries._cache.update(t=0.0, games=None)
    libraries.installed_games()
    libraries.installed_games()
    assert calls == [1]


def test_scan_start_menu_skips_junk(tmp_path):
    for n in ["Google Chrome.lnk", "Uninstall Foo.lnk", "Foo Readme.lnk", "Discord.lnk", "Notes.txt"]:
        (tmp_path / n).write_text("")
    sub = tmp_path / "Sub"
    sub.mkdir()
    (sub / "Steam.url").write_text("")
    names = sorted(e["name"] for e in apps.scan_start_menu([tmp_path]))
    assert names == ["Discord", "Google Chrome", "Steam"]


def setup_entries(monkeypatch, tmp_path):
    entries = [{"name": n, "launch": str(tmp_path / f"{n}.lnk"), "source": "start_menu", "install_dir": ""}
               for n in ["Google Chrome", "Discord", "Spotify", "Steam", "Visual Studio Code", "Visual Studio 2022"]]
    entries.append({"name": "Hollow Knight", "launch": "steam://rungameid/1", "source": "steam", "install_dir": ""})
    apps._cache.update(t=1e12, entries=entries)


def test_find_exact_fuzzy_and_ambiguous(monkeypatch, tmp_path):
    setup_entries(monkeypatch, tmp_path)
    cfg = {"app_aliases": {}}
    assert apps.find_app("discord", cfg)["match"]["name"] == "Discord"
    assert apps.find_app("google chrom", cfg)["match"]["name"] == "Google Chrome"
    amb = apps.find_app("visual studio", cfg)
    assert "match" not in amb and len(amb["candidates"]) >= 2
    assert apps.find_app("zzzzqqq", cfg) == {"candidates": []}


def test_alias_to_app_name_and_path(monkeypatch, tmp_path):
    setup_entries(monkeypatch, tmp_path)
    exe = tmp_path / "tool.exe"
    exe.write_text("")
    cfg = {"app_aliases": {"browser": "Google Chrome", "my tool": str(exe), "ghost": "C:\\nope\\x.exe"}}
    assert apps.find_app("browser", cfg)["match"]["name"] == "Google Chrome"
    assert apps.find_app("my tool", cfg)["match"]["launch"] == str(exe)
    assert apps.find_app("ghost", cfg) == {"candidates": []}


def test_open_app_uses_startfile(monkeypatch, tmp_path):
    setup_entries(monkeypatch, tmp_path)
    started = []
    monkeypatch.setattr(apps.os, "startfile", lambda p: started.append(p))
    r = apps.open_app({"app_aliases": {}}, "spotify")
    assert r == {"opened": True, "app": "Spotify"} and started == [str(tmp_path / "Spotify.lnk")]
    started.clear()
    r = apps.open_app({"app_aliases": {}}, "visual studio")
    assert r["opened"] is False and r["candidates"] and started == []
    r = apps.open_app({"app_aliases": {}}, "C:\\Windows\\System32\\cmd.exe")   # arbitrary path is not a source
    assert r["opened"] is False and started == []


def test_protected_processes():
    import os
    assert apps._is_protected("explorer.exe", r"C:\Windows\explorer.exe", 99999)
    assert apps._is_protected("foo.exe", r"C:\Windows\System32\foo.exe", 99999)
    assert apps._is_protected("python.exe", "C:\\x\\python.exe", os.getpid())
    assert not apps._is_protected("chrome.exe", r"C:\Program Files\Google\chrome.exe", 99999)


class FakeProc:
    def __init__(self, pid, stubborn):
        self.pid, self.stubborn, self.alive, self.terminated = pid, stubborn, True, False

    def is_running(self):
        return self.alive

    def terminate(self):
        self.terminated = True
        self.alive = False


def test_close_graceful_and_forced(monkeypatch, tmp_path):
    setup_entries(monkeypatch, tmp_path)
    posted = []
    graceful, stubborn = FakeProc(1, False), FakeProc(2, True)

    def post(pids):
        posted.append(set(pids))
        graceful.alive = False
    monkeypatch.setattr(apps, "_post_close", post)
    monkeypatch.setattr(apps, "find_processes", lambda n, e: [graceful])
    r = apps.close_app({"app_aliases": {}}, "discord", wait=0.3)
    assert r == {"closed": True, "app": "Discord", "forced": False} and posted == [{1}]
    monkeypatch.setattr(apps, "_post_close", lambda pids: None)
    monkeypatch.setattr(apps, "find_processes", lambda n, e: [stubborn])
    r = apps.close_app({"app_aliases": {}}, "discord", wait=0.3)
    assert r["forced"] is True and stubborn.terminated
    monkeypatch.setattr(apps, "find_processes", lambda n, e: [])
    assert apps.close_app({"app_aliases": {}}, "discord")["closed"] is False


def test_register_close_is_destructive():
    reg = Registry()
    apps.register(reg, {"app_aliases": {}})
    assert reg.tools["close_app"].destructive is True and reg.tools["open_app"].destructive is False
    assert reg.describe("close_app", {"name": "Chrome"}) == "close Chrome"
    r = reg.call("close_app", {"name": "Chrome"})
    assert r["status"] == "needs_confirmation"
    assert all(t.visual is False for t in reg.tools.values() if t.module == "pc")


def test_match_tiers_whole_word_beats_substring(monkeypatch):
    entries = [{"name": n, "launch": n, "source": "start_menu", "install_dir": ""}
               for n in ["Half Sword", "WordPad", "Microsoft Edge", "Reset settings", "Settings",
                         "Spotify", "Minecraft Launcher", "Minecraft - Prism Launcher"]]
    apps._cache.update(t=1e12, entries=entries)
    find = lambda q: apps.find_app(q, {})
    assert "match" not in find("word")                       # never "Half Sword"
    assert "Half Sword" not in find("word")["candidates"][:1]
    assert find("edge")["match"]["name"] == "Microsoft Edge"
    assert find("settings")["match"]["name"] == "Settings"
    assert find("spotifi")["match"]["name"] == "Spotify"     # misheard
    assert find("minecraft")["match"]["name"] == "Minecraft Launcher"     # shortest whole-word match
    assert find("chrome") == {"candidates": []}
