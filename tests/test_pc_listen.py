import io
import wave

import numpy as np

from jarvis.core.registry import Registry
from jarvis.pc import listen


def test_user_asked():
    for t in ["listen to this, what song is it", "what did that NPC just say", "can you hear this",
              "listen for 30 seconds and summarize"]:
        assert listen.user_asked(t), t
    for t in ["what time is it", "set a timer", ""]:
        assert not listen.user_asked(t), t


def test_clamp():
    assert listen.clamp_seconds(0, 15, 120) == 15
    assert listen.clamp_seconds(500, 15, 120) == 120
    assert listen.clamp_seconds(500, 15, 60) == 60
    assert listen.clamp_seconds(500, 15, 9999) == 120
    assert listen.clamp_seconds("x", 15, 120) == 15


def test_wav_conversion():
    rate, ch = 48000, 2
    t = np.arange(rate) / rate
    mono = (np.sin(2 * np.pi * 440 * t) * 8000).astype(np.int16)
    pcm = np.repeat(mono, ch)
    data = listen.to_wav16k(pcm, rate, ch)
    with wave.open(io.BytesIO(data)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
        assert abs(w.getnframes() - 16000) <= 2


def test_match_device():
    names = ["Microphone (Realtek)", "Headset Microphone (Oculus)", "Line In"]
    assert listen.match_device("oculus", names) == 1
    assert listen.match_device("line in", names) == 2
    assert listen.match_device("zzzzqqq", names) is None


def _tool(recorder, text="listen to this"):
    reg = Registry()
    reg.last_user_text = text
    events = []
    reg.ui_event = lambda n, d: events.append((n, d))
    return listen.make_tool(reg, {"listen_default_s": 15, "listen_max_s": 120}, recorder), events


def _loud(n, dev, cancel):
    return (np.full(16000, 3000, dtype=np.int16), 16000, 1, "Speakers")


def test_refuses_without_request():
    called = []
    tool, ev = _tool(lambda *a: called.append(1), text="what time is it")
    assert "only listen" in tool()["error"]
    assert not called and not ev


def test_records_and_events():
    tool, ev = _tool(_loud)
    r = tool(seconds=5)
    assert r["result"] == "Recorded 1 seconds of PC audio."
    assert r["_media"][0].mime == "audio/wav"
    assert ev[0] == ("pc_audio", {"on": True, "seconds": 5.0}) and ev[-1] == ("pc_audio", {"on": False})


def test_silence_and_errors():
    tool, ev = _tool(lambda n, d, c: (np.zeros(16000, dtype=np.int16), 16000, 1, "S"))
    assert "couldn't hear" in tool()["error"]
    def boom(*a):
        raise OSError("nope")
    tool, ev = _tool(boom)
    assert "error" in tool() and ev[-1] == ("pc_audio", {"on": False})


def test_register_not_visual():
    reg = Registry()
    listen.register(reg, {})
    t = reg.tools["listen_to_audio"]
    assert t.visual is False and "NEVER" in t.description


def test_live_loopback_2s():
    import threading
    try:
        pcm, rate, ch, name = listen.record(2, "", threading.Event())
    except Exception as e:  # no output device on this machine
        import pytest
        pytest.skip(str(e))
    assert rate > 0  # loopback yields no frames while nothing plays
