import io

from PIL import Image

from jarvis.core.registry import Media, Registry, Tool
from jarvis.pc import games, screen


def test_non_visual_regex():
    for t in ["set the volume to 30", "set a timer for 5 minutes", "remember my wifi", "open Spotify",
              "what's the weather", "what time is it", "pause the music", "shut down", "next song"]:
        assert screen.is_non_visual(t), t
    for t in ["how do I beat this boss", "what does this setting do", "what am I looking at"]:
        assert not screen.is_non_visual(t), t


def test_blocked_words():
    assert screen.blocked("My Vault - Bitwarden", "chrome.exe", ["bitwarden"]) == "bitwarden"
    assert screen.blocked("Game", "g.exe", ["bank"]) is None


def test_black_detection_and_encode_downscale():
    assert screen.is_black(Image.new("RGB", (200, 100), (0, 0, 0)))
    assert not screen.is_black(Image.new("RGB", (200, 100), (120, 90, 40)))
    big = Image.effect_noise((1920, 1080), 60).convert("RGB")
    out = Image.open(io.BytesIO(screen.encode(big)))
    assert out.format == "JPEG" and max(out.size) == 1280 and out.size == (1280, 720)
    small = Image.open(io.BytesIO(screen.encode(Image.new("RGB", (400, 300), "red"))))
    assert small.size == (400, 300)


def _patch(monkeypatch, img, title="Game", exe="g.exe"):
    monkeypatch.setattr(games, "gather_info", lambda: {"exe": exe, "title": title, "hwnd": 5})
    monkeypatch.setattr(screen, "window_rect", lambda h: (0, 0, 100, 100))
    monkeypatch.setattr(screen, "grab", lambda rect=None: img)


def test_look_tool_ok(monkeypatch):
    _patch(monkeypatch, Image.effect_noise((800, 600), 60).convert("RGB"))
    r = screen.make_look({"never_screenshot": []})("what is this")
    assert r["result"] == "Screenshot of Game attached."
    assert isinstance(r["_media"][0], Media) and r["_media"][0].mime == "image/jpeg"


def test_look_tool_black_and_blocked(monkeypatch):
    _patch(monkeypatch, Image.new("RGB", (100, 100), "black"))
    assert "borderless" in screen.make_look({})()["error"]
    _patch(monkeypatch, Image.new("RGB", (100, 100), "white"), title="KeePass DB")
    assert "never-screenshot" in screen.make_look({"never_screenshot": ["keepass"]})()["error"]


def test_auto_look_modes(monkeypatch):
    _patch(monkeypatch, Image.effect_noise((300, 300), 60).convert("RGB"))
    cur = {"v": None}
    monkeypatch.setattr(games, "current_game", lambda cfg=None, use_cache=True: cur["v"])
    cfg = {"auto_look": "ask"}
    p = screen.make_auto_look(cfg)
    assert p("how do I beat this boss") == []
    cfg["auto_look"] = "games"
    assert p("how do I beat this boss") == []          # no game focused
    cur["v"] = {"name": "G"}
    assert len(p("how do I beat this boss")) == 1
    assert p("set a timer for 5 minutes") == []        # non-visual skipped
    cfg["auto_look"] = "always"
    cur["v"] = None
    assert len(p("what's on my screen")) == 1


MUSIC_VOLUME = ["Turn music up 20%", "Turn the music up 35%.", "turn my music down 45%", "Turn up my music 10%",
                "Turn it up about 35%", "Turn it down to 60.", "down 10%.", "change my music up.",
                "Turn that back up to 25.", "music louder", "make it quieter", "set my music to 30"]


def test_music_volume_words_skip_auto_look():
    for t in MUSIC_VOLUME:
        assert screen.is_non_visual(t), t
    for t in ["what's that up there", "how do I get up this cliff", "what does this button do"]:
        assert not screen.is_non_visual(t), t


def _gaming_registry(monkeypatch):
    _patch(monkeypatch, Image.effect_noise((300, 300), 60).convert("RGB"))
    monkeypatch.setattr(games, "current_game", lambda cfg=None, use_cache=True: {"name": "G"})
    reg = Registry()
    screen.register(reg, {"auto_look": "games"})
    done = []
    reg.register(Tool(name="spotify_volume", description="", handler=lambda **a: done.append(a) or {"ok": True}))
    return reg, done


def test_music_volume_while_gaming_needs_no_yes(monkeypatch):
    # Real log: "Turn music up 20%" with a game focused took a screenshot, so the volume change asked for a yes.
    reg, done = _gaming_registry(monkeypatch)
    for t in MUSIC_VOLUME:
        reg.begin_turn(t)
        assert reg.attachments(t) == [], t
        assert reg.call("spotify_volume", {"delta": 20}) == {"ok": True}, t
    assert len(done) == len(MUSIC_VOLUME)


def test_screenshot_turn_still_needs_yes(monkeypatch):
    # The safety rule is unchanged: once a screenshot is attached, side effects still ask first.
    reg, done = _gaming_registry(monkeypatch)
    reg.begin_turn("what is this item")
    assert len(reg.attachments("what is this item")) == 1
    assert reg.call("spotify_volume", {"delta": 20})["status"] == "needs_confirmation"
    assert done == []


def test_register():
    reg = Registry()
    screen.register(reg, {})
    assert reg.tools["look_at_screen"].visual is True
    assert len(reg.attachment_providers) == 1


def test_live_capture_to_memory():
    info = games.gather_info()
    if not info:
        return
    img = screen.grab(screen.window_rect(info["hwnd"]))
    assert img.size[0] > 0
