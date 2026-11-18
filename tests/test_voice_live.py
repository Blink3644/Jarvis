"""Stage A8: Gemini Live mode with a fake Live connection (no network)."""
import asyncio
import threading
from types import SimpleNamespace as NS

import numpy as np

from jarvis.core.registry import Registry, Tool
from jarvis.core.voice import assistant as A
from jarvis.core.voice.live import LiveSession, classify_live_error
from tests.test_voice_assistant import (SIL, SPEECH, WAKE, Clock, FakeBrain, FakeMic, FakeSpeaker,
                                        FakeSTT, FakeUI, FakeVad, FakeWake, wait_for)


def msg(user=None, audio=None, reply=None, done=False, interrupted=False, calls=None):
    sc = NS(input_transcription=NS(text=user) if user else None,
            model_turn=NS(parts=[NS(inline_data=NS(data=audio))]) if audio else None,
            output_transcription=NS(text=reply) if reply else None,
            interrupted=interrupted, turn_complete=done)
    tc = NS(function_calls=[NS(id=f"c{i}", name=n, args=a) for i, (n, a) in enumerate(calls)]) if calls else None
    return NS(server_content=sc if not calls else None, tool_call=tc, go_away=None)


class FakeLive:
    """Plays scripted turns; each turn waits for a 'go' so tests control the timing."""

    def __init__(self, turns, fail=None):
        self.turns, self.fail = list(turns), fail
        self.audio_in, self.tool_responses = 0, []
        self.go = asyncio.Event()

    def connect(self, model, config):
        fake = self

        class CM:
            async def __aenter__(self):
                if fake.fail:
                    raise fake.fail
                return fake

            async def __aexit__(self, *a):
                return False
        return CM()

    async def send_realtime_input(self, audio=None, video=None):
        self.audio_in += 1

    async def send_tool_response(self, function_responses):
        self.tool_responses.append(function_responses)
        self.go.set()

    async def receive(self):
        if not self.turns:
            await asyncio.sleep(3600)           # stay open until closed
        turn = self.turns.pop(0)
        for m in turn:
            await asyncio.sleep(0.01)
            yield m


def session(fake, reg, **cb):
    got = {"audio": [], "user": [], "reply": [], "closed": None, "interrupted": 0}
    s = LiveSession({}, reg, connect=fake.connect, config_factory=lambda: {},
                    on_audio=lambda p: got["audio"].append(p),
                    on_user_text=lambda t: got["user"].append(t),
                    on_reply_text=lambda t: got["reply"].append(t),
                    on_interrupted=lambda: got.__setitem__("interrupted", got["interrupted"] + 1),
                    on_closed=lambda e, r: got.__setitem__("closed", (e, r)), **cb)
    return s, got


def test_session_streams_audio_and_text():
    pcm = np.arange(10, dtype=np.int16).tobytes()
    fake = FakeLive([[msg(user="what time"), msg(audio=pcm, reply="Noon, sir."), msg(done=True)]])
    s, got = session(fake, Registry())
    s.start()
    assert s.ready.wait(2)
    s.send_audio(SPEECH)
    assert wait_for(lambda: got["reply"] == ["Noon, sir."])
    assert wait_for(lambda: fake.audio_in == 1)
    assert list(got["audio"][0]) == list(range(10)) and s.replied
    s.close()
    assert wait_for(lambda: got["closed"] is not None)
    assert got["closed"] == (None, True)


def test_tool_call_and_spoken_confirmation():
    reg = Registry()
    ran = []
    reg.register(Tool(name="shutdown_pc", description="x", handler=lambda: ran.append(1) or {"ok": True},
                      parameters={"type": "object", "properties": {}}, destructive=True))
    fake = FakeLive([
        [msg(user="shut down my pc"), msg(calls=[("shutdown_pc", {})])],
        [msg(reply="Are you sure?"), msg(done=True)],
        [msg(user="yes"), msg(calls=[("confirm_action", {"confirm_id": "1"})])],
    ])
    s, got = session(fake, reg)
    s.start()
    assert wait_for(lambda: len(fake.tool_responses) == 2)
    first = fake.tool_responses[0][0].response
    assert first["status"] == "needs_confirmation"
    assert ran == [1]                                   # "yes" was heard, so it ran
    assert fake.tool_responses[1][0].response == {"ok": True}
    s.close()


def test_connect_failure_reported():
    fake = FakeLive([], fail=RuntimeError("429 RESOURCE_EXHAUSTED quota"))
    s, got = session(fake, Registry())
    s.start()
    assert wait_for(lambda: got["closed"] is not None)
    err, replied = got["closed"]
    assert classify_live_error(err) == "quota" and not replied


def test_classify():
    assert classify_live_error(Exception("API key not valid")) == "key"
    assert classify_live_error(ConnectionError("websocket closed 1011")) == "connection"


# ---------- the voice loop in live mode ----------
class FakeSession:
    def __init__(self, callbacks):
        self.cb = callbacks
        self.frames, self.closed, self.replied, self.user_text = 0, False, False, ""
        self.quiet_since = 0.0

    def start(self):
        pass

    def send_audio(self, f):
        self.frames += 1

    def close(self):
        self.closed = True

    def idle_for(self):
        return self.quiet_for if hasattr(self, "quiet_for") else 0.0


def make_live(**cfg):
    clock = Clock()
    c = {"followup_seconds": 0, "max_record_seconds": 15, "end_silence_ms": 800, "wake_enabled": True,
         "wake_sensitivity": 0.5, "reply_mode": "both", "stop_word_enabled": True,
         "listen_sound": False, "live_mode": True, "live_idle_seconds": 20}
    c.update(cfg)
    sessions = []

    def factory(**cb):
        sessions.append(FakeSession(cb))
        return sessions[-1]
    mic, sp, ui = FakeMic(clock), FakeSpeaker(), FakeUI()
    brain = FakeBrain(["It's noon, sir."])
    va = A.VoiceAssistant(c, brain, Registry(), mic=mic, wake=FakeWake(), stt=FakeSTT(["what time is it"]),
                          speaker=sp, vad=FakeVad(), ui=ui, clock=clock, live_factory=factory)
    va.start()
    return va, mic, sp, brain, ui, sessions


def test_wake_opens_live_and_streams_mic():
    va, mic, sp, brain, ui, sessions = make_live()
    try:
        mic.feed(SIL, WAKE, *[SPEECH] * 5)
        assert wait_for(lambda: va.state == A.LIVE and sessions and sessions[0].frames >= 5)
        assert brain.asked == []                       # normal pipeline not used
        sessions[0].cb["on_audio"](np.zeros(10, np.int16))
        sessions[0].cb["on_interrupted"]()             # user talked over Jarvis
        assert sp.interrupts >= 1
    finally:
        va.stop()


def test_live_off_uses_normal_mode():
    va, mic, sp, brain, ui, sessions = make_live(live_mode=False)
    try:
        mic.feed(SIL, WAKE, *[SPEECH] * 10, *[SIL] * 12)
        assert wait_for(lambda: brain.asked == ["what time is it"])
        assert sessions == []
    finally:
        va.stop()


def test_failure_before_reply_falls_back_to_normal_mode():
    va, mic, sp, brain, ui, sessions = make_live()
    try:
        mic.feed(SIL, WAKE, *[SPEECH] * 10)
        assert wait_for(lambda: sessions and sessions[0].frames >= 10)
        sessions[0].cb["on_closed"](RuntimeError("429 quota"), False)
        mic.feed(*[SIL] * 12)                          # user finished speaking
        assert wait_for(lambda: brain.asked == ["what time is it"]), (va.state, ui.notices)
        assert any("normal mode" in n for n in ui.notices)
        assert not va.live_enabled()                   # Live is paused for a while after a quota hit
    finally:
        va.stop()


def test_idle_session_closes_and_stop_word():
    va, mic, sp, brain, ui, sessions = make_live()
    try:
        mic.feed(SIL, WAKE, SIL)
        assert wait_for(lambda: va.state == A.LIVE)
        sessions[0].quiet_for = 30
        mic.feed(SIL)
        assert wait_for(lambda: va.state == A.IDLE and sessions[0].closed)
        mic.feed(*[SIL] * 8, WAKE, SIL)               # wake again after the 0.5 s guard
        assert wait_for(lambda: len(sessions) == 2 and va.state == A.LIVE)
        sessions[1].user_text = "stop"
        sessions[1].cb["on_user_text"]("stop")
        assert wait_for(lambda: va.state == A.IDLE and sessions[1].closed)
    finally:
        va.stop()
