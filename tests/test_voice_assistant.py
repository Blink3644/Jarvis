"""The voice loop with fake parts: wake -> command -> reply -> follow-up -> idle, and "stop"."""
import gc
import queue
import threading
import time

import numpy as np

from jarvis.core.brain import Reply
from jarvis.core.registry import Registry
from jarvis.core.voice import assistant as A

SIL = np.zeros(1280, np.int16)
SPEECH = np.full(1280, 1000, np.int16)
WAKE = np.full(1280, 7, np.int16)


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


class FakeMic:
    def __init__(self, clock):
        self.q, self.clock, self.name = queue.Queue(), clock, "fake"

    def feed(self, *frames):
        for f in frames:
            self.q.put(f)

    def open(self):
        return True

    def read(self, timeout=0.5):
        try:
            f = self.q.get(timeout=0.02)
        except queue.Empty:
            return None
        self.clock.t += 0.08
        return f

    def healthy(self):
        return True

    def close(self):
        pass


class FakeWake:
    def __init__(self):
        self.calls = 0

    def score(self, f):
        self.calls += 1
        return 1.0 if f[0] == 7 else 0.0

    def reset(self):
        pass


class FakeVad:
    def is_speech(self, f):
        return bool(f[0] == 1000)

    def reset(self):
        pass


class FakeSTT:
    def __init__(self, texts):
        self.texts = list(texts)

    def transcribe(self, audio, hints=None):
        return self.texts.pop(0) if self.texts else ""


class FakeSpeaker:
    def __init__(self):
        self.said, self.interrupts, self.busy, self.first_audio_at = [], 0, False, 0.0
        self.on_speaking = None

    def say(self, t):
        self.said.append(t)

    def play_now(self, pcm):
        pass

    def interrupt(self):
        self.interrupts += 1

    def wait_idle(self, cancelled=None):
        pass

    def mark_turn_start(self):
        pass


class FakeBrain:
    def __init__(self, replies, gate=None):
        self.replies, self.asked, self.gate = list(replies), [], gate

    def ask(self, text, on_text=None, on_notice=None):
        self.asked.append(text)
        if self.gate:
            self.gate.wait(5)
        r = self.replies.pop(0)
        on_text(r)
        return Reply(r)


class FakeUI:
    def __init__(self):
        self.states, self.notices = [], []

    def status(self, s):
        self.states.append(s)

    def user_text(self, t):
        pass

    def reply_text(self, t, final=False):
        pass

    def notice(self, m):
        self.notices.append(m)


def make(stt_texts, replies, gate=None, **cfg):
    # Collect leftover Tk objects from earlier UI tests here, on the main thread. Otherwise the
    # garbage collector may run their __del__ inside a voice thread, where each Tcl call waits
    # ~1 s for a Tk thread that no longer exists, and the turn misses the test's 5 s timeout.
    gc.collect()
    clock = Clock()
    c = {"followup_seconds": 5, "max_record_seconds": 15, "end_silence_ms": 800,
         "wake_enabled": True, "wake_sensitivity": 0.5, "reply_mode": "both",
         "stop_word_enabled": True, "listen_sound": False}
    c.update(cfg)
    mic, sp, brain, ui = FakeMic(clock), FakeSpeaker(), FakeBrain(replies, gate), FakeUI()
    va = A.VoiceAssistant(c, brain, Registry(), mic=mic, wake=FakeWake(), stt=FakeSTT(stt_texts),
                          speaker=sp, vad=FakeVad(), ui=ui, clock=clock)
    va.start()
    return va, mic, sp, brain, ui


def wait_for(cond, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_wake_command_reply_followup_then_idle():
    va, mic, sp, brain, ui = make(["Hey Jarvis, what time is it?", "and the date?"],
                                  ["It's noon, sir. Anything else?", "The 17th."])
    try:
        mic.feed(*[SIL] * 5, WAKE, *[SPEECH] * 10, *[SIL] * 12)
        assert wait_for(lambda: brain.asked == ["what time is it?"]), \
            (brain.asked, va.state, mic.q.qsize(), mic.clock.t, va.thread.is_alive())
        assert wait_for(lambda: va.state == A.FOLLOWUP)
        assert sp.said == ["It's noon, sir.", "Anything else?"]
        # follow-up question without the wake word (after the 0.5 s self-guard)
        mic.feed(*[SIL] * 8, *[SPEECH] * 10, *[SIL] * 12)
        assert wait_for(lambda: brain.asked[-1] == "and the date?")
        assert wait_for(lambda: va.state == A.FOLLOWUP)
        # nobody speaks: back to waiting for the wake word
        mic.feed(*[SIL] * 75)
        assert wait_for(lambda: va.state == A.IDLE)
    finally:
        va.stop()


def test_noise_in_followup_does_not_start_a_turn():
    va, mic, sp, brain, ui = make(["Jarvis what time is it"], ["Noon."])
    try:
        mic.feed(WAKE, *[SPEECH] * 10, *[SIL] * 12)
        assert wait_for(lambda: va.state == A.FOLLOWUP)
        blips = []
        for _ in range(12):
            blips += [SPEECH, SIL, SIL, SIL]            # short clicks, never clear speech
        mic.feed(*[SIL] * 8, *blips, *[SIL] * 30)
        assert wait_for(lambda: va.state == A.IDLE)
        assert brain.asked == ["what time is it"]
    finally:
        va.stop()


def test_no_wake_word_while_jarvis_speaks():
    va, mic, sp, brain, ui = make([], [])
    try:
        va._on_speaking(True)
        mic.feed(WAKE, SIL)
        time.sleep(0.2)
        assert va.state == A.IDLE and va.wake.calls == 0
        va._on_speaking(False)
        mic.feed(WAKE)                                   # inside the 0.5 s guard: ignored
        time.sleep(0.2)
        assert va.state == A.IDLE
        mic.feed(*[SIL] * 8, WAKE)
        assert wait_for(lambda: va.state == A.LISTEN)
    finally:
        va.stop()


def test_saying_stop_interrupts():
    gate = threading.Event()
    va, mic, sp, brain, ui = make(["what's the weather", "stop"], ["Long answer. More."], gate=gate)
    try:
        mic.feed(WAKE, *[SPEECH] * 10, *[SIL] * 12)
        assert wait_for(lambda: va.state == A.BUSY and brain.asked)
        mic.feed(*[SPEECH] * 6, *[SIL] * 8)              # user says "stop"
        assert wait_for(lambda: va.state == A.IDLE)
        assert sp.interrupts >= 1
        gate.set()
        time.sleep(0.2)
        assert sp.said == []                              # the cancelled reply is never spoken
    finally:
        va.stop()


def test_push_to_talk_interrupts_and_records():
    va, mic, sp, brain, ui = make(["open settings"], ["Okay."], followup_seconds=0)
    try:
        va.events.put("ptt_down")
        mic.feed(*[SPEECH] * 10)
        assert wait_for(lambda: va.state == A.PTT)
        wait_for(lambda: mic.q.empty())
        va.events.put("ptt_up")
        mic.feed(SIL)
        assert wait_for(lambda: brain.asked == ["open settings"])
        assert wait_for(lambda: va.state == A.IDLE)
    finally:
        va.stop()


def test_long_answer_speaks_first_sentences_rest_on_screen():
    va, mic, sp, brain, ui = make(["Hey Jarvis, explain it"],
                                  ["One. Two. Three. Four. Five."], spoken_sentence_limit=2)
    try:
        mic.feed(*[SIL] * 5, WAKE, *[SPEECH] * 10, *[SIL] * 12)
        assert wait_for(lambda: sp.said[-1:] == ["The rest is on screen."])
        assert sp.said == ["One.", "Two.", "The rest is on screen."]
    finally:
        va.stop()


def test_muted_mic_ignores_wake_word():
    va, mic, sp, brain, ui = make(["Hey Jarvis, what time is it?"], ["Noon."])
    try:
        va.muted = True
        mic.feed(*[SIL] * 5, WAKE, *[SPEECH] * 10, *[SIL] * 12)
        time.sleep(0.5)
        assert brain.asked == [] and va.state == A.IDLE
    finally:
        va.stop()
