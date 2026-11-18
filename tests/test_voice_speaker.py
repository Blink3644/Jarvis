"""Speaker fallback behaviour: edge-tts retries, offline voice can't hang, voice comes back."""
import sys
import time

import numpy as np
import pytest

from jarvis.core.voice import speaker as sp


@pytest.fixture
def spk(monkeypatch):
    with monkeypatch.context() as m:      # no background threads, only while constructing
        m.setattr(sp.threading, "Thread", lambda *a, **k: type("T", (), {"start": lambda s: None})())
        s = sp.Speaker({"tts_voice": "en-GB-RyanNeural"})
    monkeypatch.setattr(sp, "decode", lambda b: np.frombuffer(b, dtype=np.int16))
    monkeypatch.setattr(sp.time, "sleep", lambda x: None)
    return s


def edge_seq(spk, results):
    calls = []

    async def fake(text):
        calls.append(text)
        r = results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    spk._edge = fake
    return calls


def test_has_words():
    assert sp.has_words("Hello.") and sp.has_words("42")
    for junk in ("", "```", "**", "---", " * ", "_"):
        assert not sp.has_words(junk)


def test_say_skips_unspeakable_chunks(spk):
    spk.say("```")
    spk.say("**")
    assert spk.pending == 0 and spk.say_q.empty()
    spk.say("Done, sir.")
    assert spk.pending == 1


def test_edge_retry_succeeds_without_going_offline(spk):
    calls = edge_seq(spk, [RuntimeError("NoAudioReceived"), b"\x01\x00\x02\x00"])
    spk._offline = lambda t: pytest.fail("should not use offline voice")
    out = spk.synth("Hello")
    assert len(out) == 2 and len(calls) == 2 and spk.offline_until == 0


def test_falls_back_then_returns_to_online(spk):
    edge_seq(spk, [RuntimeError("x"), RuntimeError("x"), b"\x05\x00"])
    spk._offline = lambda t: np.array([9], dtype=np.int16)
    assert spk.synth("Hi")[0] == 9                      # both online tries failed
    assert spk.offline_until > time.monotonic()
    assert spk.synth("Again")[0] == 9                   # still in the offline window (no edge call)
    spk.offline_until = time.monotonic() - 1            # window over
    assert spk.synth("Back")[0] == 5 and spk.offline_until == 0


def test_offline_voice_timeout_cannot_hang(spk):
    spk.OFFLINE_TIMEOUT = 1
    spk._offline_cmd = lambda text, path: [sys.executable, "-c", "import time; time.sleep(30)"]
    t = time.monotonic()
    out = spk._offline("hello")
    assert time.monotonic() - t < 10 and len(out) == 0


def test_offline_voice_writes_audio_each_time(spk):
    # stands in for the pyttsx3 child: writes a file; must work on repeated calls
    spk._offline_cmd = lambda text, path: [
        sys.executable, "-c", f"open(r'{path}','wb').write(b'\\x07\\x00')"]
    for _ in range(3):
        assert spk._offline("x")[0] == 7


def test_level_db():
    from jarvis.core.voice.assistant import level_db
    assert level_db(np.zeros(0, np.int16)) == "n/a"
    assert level_db(np.full(10, 32767, np.int16)) == "-0 dB" or level_db(np.full(10, 32767, np.int16)) == "0 dB"
    assert level_db(np.full(10, 1036, np.int16)) == "-30 dB"
