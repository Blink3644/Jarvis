import subprocess

from jarvis.core.registry import Registry
from jarvis.pc import media, system


class FakeEp:
    def __init__(self):
        self.vol, self.mute = 0.34, 0

    def GetMasterVolumeLevelScalar(self):
        return self.vol

    def SetMasterVolumeLevelScalar(self, v, ctx):
        self.vol = v

    def GetMute(self):
        return self.mute

    def SetMute(self, m, ctx):
        self.mute = m


def test_volume_tools(monkeypatch):
    ep = FakeEp()
    monkeypatch.setattr(media, "_endpoint", lambda: ep)
    assert media.get_volume() == {"volume": 34, "muted": False}
    assert media.set_volume(50)["volume"] == 50
    assert media.change_volume(-10)["volume"] == 40
    assert media.change_volume(500)["volume"] == 100
    assert media.set_volume(-5)["volume"] == 0
    assert media.set_mute(True)["muted"] is True
    assert media.set_volume(20) == {"volume": 20, "muted": False}      # raising volume unmutes


def test_media_keys(monkeypatch):
    sent = []
    monkeypatch.setattr(media, "send_key", sent.append)
    for a in ("play_pause", "next", "previous", "stop"):
        media.media_control(a)
    assert sent == [0xB3, 0xB0, 0xB1, 0xB2]
    assert "error" in media.media_control("explode") and len(sent) == 4


def test_system_commands_fixed_args(monkeypatch):
    calls = []
    monkeypatch.setattr(system.subprocess, "run", lambda args, **kw: calls.append(args))
    system.shutdown_pc()
    system.restart_pc()
    assert calls == [["shutdown", "/s", "/t", "5"], ["shutdown", "/r", "/t", "5"]]


def test_lock_and_sleep_mocked(monkeypatch):
    seen = []

    class L:
        def __getattr__(self, n):
            return lambda *a: seen.append((n, a))

    class W:
        user32 = powrprof = L()
    monkeypatch.setattr(system.ctypes, "windll", W())
    system.lock_pc()
    system.sleep_pc()
    assert seen == [("LockWorkStation", ()), ("SetSuspendState", (0, 0, 0))]


def test_destructive_flags_and_confirmation(monkeypatch):
    reg = Registry()
    system.register(reg, {})
    media.register(reg, {})
    assert [n for n in ("lock_pc", "sleep_pc", "shutdown_pc", "restart_pc", "take_screenshot")
            if reg.tools[n].destructive] == ["sleep_pc", "shutdown_pc", "restart_pc"]
    called = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: called.append(a))
    r = reg.call("shutdown_pc", {})
    assert r["status"] == "needs_confirmation" and called == []
    assert all(t.visual is False for t in reg.tools.values() if t.module == "pc")


def test_screenshot_to_temp_folder(tmp_path):
    r = system.take_screenshot(tmp_path / "shots")
    from pathlib import Path
    p = Path(r["saved"])
    assert p.exists() and p.suffix == ".png" and p.stat().st_size > 1000


def test_pictures_dir_resolves():
    assert system.pictures_dir().name.lower() == "pictures"
