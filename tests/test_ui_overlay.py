import threading
import time

import pytest

from jarvis.core.ui import overlay, tray


def test_short_reply_spoken_whole():
    assert overlay.split_summary("It is half past nine.") == ("It is half past nine.",) * 2


def test_long_reply_summarised():
    text = "Photosynthesis turns light into sugar. " + "More detail here. " * 30
    spoken, full = overlay.split_summary(text)
    assert spoken.endswith("The details are on screen.")
    assert len(spoken) < 300 and full == text.strip()


def test_list_reply_summarised():
    spoken, full = overlay.split_summary("Here are the steps:\n- one\n- two")
    assert spoken.endswith("on screen.") and "- two" in full


def test_autohide_clock():
    now = [100.0]
    h = overlay.AutoHide(8, clock=lambda: now[0])
    assert not h.expired()
    h.touch(0)
    now[0] = 107
    assert not h.expired()
    now[0] = 108.1
    assert h.expired()
    h.touch(0)
    h.hold = True
    now[0] = 500
    assert not h.expired()
    h.hold = False
    h.touch(1000)  # long text stays longer
    now[0] = 520
    assert not h.expired()


def test_tray_state_mapping_and_icons():
    assert tray.icon_state("ptt") == "listening"
    assert tray.icon_state("busy") == "thinking"
    assert tray.icon_state("mic_lost") == "warning"
    assert tray.icon_state("whatever") == "idle"
    for s in tray.STATES:
        img = tray.make_icon(s, 32)
        assert img.size == (32, 32)
        assert tray.make_icon(s, 32) is img  # cached


def test_tray_set_state_without_icon():
    t = tray.Tray(lambda: None, lambda m: None, lambda: None)
    t.set_state("busy")
    assert t.state == "thinking"
    t.set_muted(True)
    assert t._shown() == "muted"
    t.warn("x")  # no icon yet: no error


def test_overlay_smoke_threaded():
    ov = overlay.Overlay({"theme": "hud", "overlay_hide_s": 0.2}, offscreen=True)
    if not ov.start(timeout=5):
        pytest.skip("Tk unavailable")

    def work():
        ov.status("listen")
        ov.user_text("what time is it")
        ov.reply_text("It is ")
        ov.set_speaking(True)
        ov.set_level(0.7)
        ov.reply_text("nine.", final=True)
        ov.set_speaking(False)
        ov.status("idle")
        ov.set_theme("minimal")
        ov.show_full("long " * 50)
        ov.notice("hello")
    th = threading.Thread(target=work)
    th.start()
    th.join()
    time.sleep(0.4)
    ov.stop()
    time.sleep(0.2)
    assert not ov.failed
