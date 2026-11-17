import pytest

from jarvis.core import game_profiles as gp
from jarvis.pc import games


@pytest.fixture(autouse=True)
def clean():
    saved = list(gp._profiles)
    gp._profiles.clear()
    games._cache = None
    yield
    gp._profiles[:] = saved
    games._cache = None


def info(exe="x.exe", path="C:\\Other\\x.exe", title="Win"):
    return {"exe": exe, "exe_path": path, "title": title}


def test_installed_game_by_path():
    inst = [{"name": "Elden Ring", "install_dir": "C:\\Games\\ER", "launch": "", "source": "steam"}]
    d = games.decide(info("er.exe", "C:\\Games\\ER\\game\\er.exe"), inst, [], False)
    assert d["name"] == "Elden Ring"
    assert games.decide(info("er.exe", "C:\\Games\\ERX\\er.exe"), inst, [], False) is None


def test_game_list():
    assert games.decide(info("Foo.exe", title="Foo!"), [], ["foo.exe"], False)["name"] == "Foo!"


def test_fullscreen_heuristic_and_non_games():
    assert games.decide(info("indie.exe", title="Indie"), [], [], True)["name"] == "Indie"
    assert games.decide(info("indie.exe"), [], [], False) is None
    assert games.decide(info("chrome.exe"), [], [], True) is None
    assert games.decide(info("VLC.exe"), [], [], True) is None


def test_no_exe():
    assert games.decide({"exe": ""}, [], [], True) is None


def test_profile_wins_and_adds_context():
    gp.register_profile(gp.GameProfile("Minecraft", lambda i: "javaw" in i["exe"].lower(),
                                       context=lambda: "Pack: ATM"))
    d = games.decide(info("javaw.exe"), [], [], False)
    assert d["name"] == "Minecraft"
    text = games.context_for({"name": "Minecraft", "exe": "javaw.exe", "exe_path": "", "title": "t"})
    assert "currently playing Minecraft" in text and "Pack: ATM" in text
    assert games.context_for(None) == ""


def test_current_game_uses_gather_and_caches(monkeypatch):
    calls = []
    def fake():
        calls.append(1)
        return {"exe": "g.exe", "exe_path": "C:\\g.exe", "title": "G", "hwnd": 1, "pid": 2, "fullscreen": True}
    monkeypatch.setattr(games, "gather_info", fake)
    monkeypatch.setattr(games, "_installed", lambda: [])
    g = games.current_game({"game_list": []})
    assert g["name"] == "G" and g["hwnd"] == 1
    games.current_game({"game_list": []})
    assert len(calls) == 1


def test_register_adds_context(monkeypatch):
    from jarvis.core.registry import Registry
    reg = Registry()
    monkeypatch.setattr(games, "current_game", lambda cfg=None, use_cache=True: {
        "name": "Doom", "exe": "d.exe", "exe_path": "", "title": "Doom"})
    games.register(reg, {})
    assert "Doom" in reg.extra_context()


def test_live_gather_info_runs():
    i = games.gather_info()
    assert i is None or "exe" in i
