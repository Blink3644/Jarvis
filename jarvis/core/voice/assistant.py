"""The voice loop: wake word / push-to-talk -> record -> speech-to-text -> Gemini -> speech.

One audio thread reads the selected mic in 80 ms frames and runs a small state machine.
Speech-to-text, Gemini and speaking happen on a "turn" thread so the mic keeps being
watched (for push-to-talk and "stop") while Jarvis thinks and talks.

Parts (mic, wake, stt, speaker, vad, ptt) are passed in, so tests can use fakes.
"""
import collections
import logging
import queue
import threading
import time

import numpy as np

from .endpoint import DONE, SPEAKING, TIMEOUT, EndpointSettings, Endpointer
from .text import SentenceSplitter, is_stop_command, strip_wake_words

log = logging.getLogger(__name__)

RATE = 16000
FRAME = 1280            # 80 ms, what openwakeword wants
SUB = 320               # 20 ms, what the VAD wants
SELF_GUARD_S = 0.5      # wake word stays paused this long after Jarvis stops talking
PREROLL_FRAMES = 8      # 0.64 s kept from before a wake word / key press

IDLE, LISTEN, PTT, BUSY, FOLLOWUP, MIC_LOST = "idle", "listen", "ptt", "busy", "followup", "mic_lost"

# Whisper sometimes "hears" these in noise; never treat them as a command on their own.
_HALLUCINATIONS = {"", "you", "thank you", "thanks for watching", "thank you for watching",
                   "bye", ".", "uh", "um", "hmm", "so"}


def _meaningful(text: str) -> bool:
    t = "".join(c for c in text.lower() if c.isalnum() or c in " '").strip()
    return t not in _HALLUCINATIONS


class VoiceAssistant:
    def __init__(self, cfg, brain, registry, *, mic, wake, stt, speaker, vad, ui, ptt=None,
                 sounds=None, clock=time.monotonic):
        self.cfg, self.brain, self.registry = cfg, brain, registry
        self.mic, self.wake, self.stt, self.speaker, self.vad = mic, wake, stt, speaker, vad
        self.ui, self.ptt, self.sounds, self.clock = ui, ptt, sounds, clock
        self.events: queue.Queue = queue.Queue()
        self.state = IDLE
        self.ring = collections.deque(maxlen=PREROLL_FRAMES)
        self.buf: list[np.ndarray] = []
        self.ep: Endpointer | None = None
        self.source = ""
        self.wake_resume_at = 0.0
        self.followup_guard_until = 0.0
        self.turn_lock = threading.Lock()
        self.turn: dict | None = None
        self.stop_ep: Endpointer | None = None
        self.stop_buf: list[np.ndarray] = []
        self.stop_checking = False
        self.running = False
        self.thread: threading.Thread | None = None
        self.mic_retry_at = 0.0
        self.muted = False                 # tray "Mute mic": ignore the mic completely
        self.tap = None                    # optional fn(frame) for a level meter
        self.wake_test_until = 0.0         # "Test wake word": report scores instead of acting
        self.wake_test_best = 0.0
        self.speaking = False
        self.wake_reset_pending = False
        speaker.on_speaking = self._on_speaking

    # ---------- lifecycle ----------
    def start(self):
        self.running = True
        if self.ptt:
            self.ptt.start(lambda: self.events.put("ptt_down"), lambda: self.events.put("ptt_up"))
        self.thread = threading.Thread(target=self._loop, name="voice-loop", daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.ptt:
            self.ptt.stop()
        self.speaker.interrupt()
        if self.thread:
            self.thread.join(timeout=2)

    def _set(self, state):
        if state != self.state:
            log.debug("voice state %s -> %s", self.state, state)
            self.state = state
            self.ui.status(state)

    # ---------- main loop ----------
    def _loop(self):
        if hasattr(self.mic, "com_init"):
            self.mic.com_init()
        if not self.mic.open():
            self._mic_lost()
        while self.running:
            try:
                self._tick()
            except Exception:
                log.exception("voice loop error")
                time.sleep(0.2)

    def _tick(self):
        while True:
            try:
                ev = self.events.get_nowait()
            except queue.Empty:
                break
            self._event(ev)
        if self.state == MIC_LOST:
            if self.clock() >= self.mic_retry_at:
                self.mic_retry_at = self.clock() + 2
                if self.mic.open():
                    self.ui.notice("Microphone is back. I'm listening again.")
                    self._go_idle()
            time.sleep(0.05)
            return
        frame = self.mic.read(timeout=0.5)
        if frame is None:
            if not self.mic.healthy():
                self._mic_lost()
            return
        if self.muted:
            if self.state != IDLE and self.state != BUSY:
                self._go_idle()
            return
        self.ring.append(frame)
        if self.tap:
            try:
                self.tap(frame)
            except Exception:
                self.tap = None
        st = self.state
        if st == IDLE:
            self._idle_frame(frame)
        elif st in (LISTEN, FOLLOWUP):
            self._listen_frame(frame)
        elif st == PTT:
            self.buf.append(frame)
            if len(self.buf) * FRAME >= self.cfg.get("max_record_seconds", 15) * RATE:
                self._finish_listen()
        elif st == BUSY:
            self._busy_frame(frame)

    def _event(self, ev):
        if ev == "ptt_down":
            if self.muted:
                return
            if self.state == MIC_LOST:
                self._say_now("I can't hear you. The microphone selected in settings isn't connected.")
                return
            if self.state == PTT:
                return
            self._cancel_turn()           # pressing the key interrupts Jarvis
            self._start_listen("ptt")
        elif ev == "ptt_up":
            if self.state == PTT:
                self._finish_listen()
        elif isinstance(ev, tuple) and ev[0] == "turn_done":
            _, turn = ev
            if turn is not self.turn:
                return                    # an older, cancelled turn finished late
            self.turn = None
            end = self.clock()
            self.wake_resume_at = end + SELF_GUARD_S
            self.wake.reset()
            follow = self.cfg.get("followup_seconds", 5)
            if follow and turn["spoke"] and not turn["cancelled"] and self.state == BUSY:
                self.followup_guard_until = end + SELF_GUARD_S
                self._start_listen("followup")
            elif self.state == BUSY:
                self._go_idle()
        elif ev == "reopen_mic":
            self._cancel_turn()
            self.mic.close()
            if self.mic.open():
                self._go_idle()
            else:
                self._mic_lost()
        elif ev == "stop_heard":
            if self.state == BUSY:
                log.info("'stop' heard: interrupting")
                self._cancel_turn()
                self._go_idle()

    # ---------- states ----------
    def _on_speaking(self, speaking: bool):
        """Called by the speaker thread. No wake word while Jarvis talks, plus a short guard."""
        self.speaking = speaking
        if not speaking:
            self.wake_resume_at = max(self.wake_resume_at, self.clock() + SELF_GUARD_S)
            self.wake_reset_pending = True

    def test_wake(self, seconds=10):
        self.wake_test_best = 0.0
        self.wake_test_until = self.clock() + seconds

    def _idle_frame(self, frame):
        if self.speaking or self.speaker.busy:
            return
        if self.wake_reset_pending:
            self.wake_reset_pending = False
            self.wake.reset()
        testing = self.clock() < self.wake_test_until
        if not testing and (not self.cfg.get("wake_enabled", True) or self.clock() < self.wake_resume_at):
            return
        score = self.wake.score(frame)
        if testing:
            self.wake_test_best = max(self.wake_test_best, score)
            if score >= self.wake_threshold():
                self.wake.reset()
                self.ui.notice(f"Wake word heard (score {score:.2f}, needs {self.wake_threshold():.2f}).")
            return
        if score >= self.wake_threshold():
            log.info("wake word (score %.2f)", score)
            self.wake.reset()
            self._start_listen("wake")

    def wake_threshold(self) -> float:
        sens = min(0.95, max(0.05, float(self.cfg.get("wake_sensitivity", 0.5))))
        return 1.0 - sens

    def _start_listen(self, source):
        self.source = source
        self.buf = list(self.ring)[-3:] if source in ("wake", "ptt") else []
        max_ms = int(self.cfg.get("max_record_seconds", 15) * 1000)
        if source == "followup":
            start_ms = int(self.cfg.get("followup_seconds", 5) * 1000)
        else:
            start_ms = 5000
        self.ep = Endpointer(EndpointSettings(
            frame_ms=20, start_timeout_ms=start_ms, max_ms=max_ms,
            min_speech_ms=300 if source == "followup" else 200,
            end_silence_ms=int(self.cfg.get("end_silence_ms", 800))))
        self.vad.reset()
        if source != "followup" and self.cfg.get("listen_sound", True) and self.sounds:
            self.speaker.play_now(self.sounds["listen"])
        self._set({"wake": LISTEN, "ptt": PTT, "followup": FOLLOWUP}[source])

    def _listen_frame(self, frame):
        if self.state == FOLLOWUP and self.clock() < self.followup_guard_until:
            return                         # tail of Jarvis' own voice
        self.buf.append(frame)
        result = None
        for i in range(0, FRAME, SUB):
            result = self.ep.feed(self.vad.is_speech(frame[i:i + SUB]))
            if result in (DONE, TIMEOUT):
                break
        if result == SPEAKING and self.state == FOLLOWUP and self.ui:
            self.ui.status("followup_heard")
        if result == DONE:
            self._finish_listen()
        elif result == TIMEOUT:
            if self.source == "wake":
                log.info("wake word heard but no command followed")
            self._go_idle()

    def _go_idle(self):
        self.buf = []
        self.ep = None
        self._set(IDLE)

    def _finish_listen(self):
        audio = np.concatenate(self.buf) if self.buf else np.zeros(0, np.int16)
        source = self.source
        self.buf = []
        self.ep = None
        if len(audio) < RATE * 0.3:
            self._go_idle()
            return
        self._set(BUSY)
        self.stop_ep = None
        self.stop_buf = []
        turn = {"cancelled": False, "spoke": False, "speech_end": self.clock()}
        self.turn = turn
        threading.Thread(target=self._run_turn, args=(turn, audio, source),
                         name="voice-turn", daemon=True).start()

    def _busy_frame(self, frame):
        """While Jarvis thinks/talks: listen only for a short 'stop'."""
        if not self.cfg.get("stop_word_enabled", True) or self.stop_checking:
            return
        if self.stop_ep is None:
            self.stop_ep = Endpointer(EndpointSettings(frame_ms=20, start_timeout_ms=10 ** 9,
                                                       min_speech_ms=150, end_silence_ms=400,
                                                       max_ms=2500))
            self.stop_buf = []
        self.stop_buf.append(frame)
        result = None
        for i in range(0, FRAME, SUB):
            result = self.stop_ep.feed(self.vad.is_speech(frame[i:i + SUB]))
            if result == DONE:
                break
        if result != SPEAKING and result != DONE:
            self.stop_buf = self.stop_buf[-3:]
        if result == DONE:
            audio = np.concatenate(self.stop_buf)
            self.stop_ep, self.stop_buf = None, []
            if len(audio) <= RATE * 2.5:
                self.stop_checking = True
                threading.Thread(target=self._check_stop, args=(audio,), daemon=True).start()

    def _check_stop(self, audio):
        try:
            text = self.stt.transcribe(audio, hints=None)
            log.info("heard while busy: %r", text)
            if is_stop_command(text):
                self.events.put("stop_heard")
        except Exception:
            log.exception("stop check failed")
        finally:
            self.stop_checking = False

    def _mic_lost(self):
        self.mic.close()
        self.mic_retry_at = self.clock() + 2
        self._set(MIC_LOST)
        self.ui.notice("I can't find the microphone selected in settings. "
                       "Wake word is paused until it's plugged back in.")

    # ---------- turns ----------
    def _cancel_turn(self):
        if self.turn:
            self.turn["cancelled"] = True
            self.turn = None
            self.registry.cancel.set()      # long tools (e.g. listening to PC audio) stop early
        self.speaker.interrupt()

    def _say_now(self, text):
        self.ui.reply_text(text, final=True)
        if self.cfg.get("reply_mode", "both") != "text":
            self.speaker.say(text)

    def _run_turn(self, turn, audio, source):
        try:
            self._turn_body(turn, audio, source)
        except Exception:
            log.exception("voice turn failed")
            if not turn["cancelled"]:
                self._say_now("Sorry, something went wrong on my end. The details are in the log.")
        finally:
            self.events.put(("turn_done", turn))

    def _turn_body(self, turn, audio, source):
        t_end = turn["speech_end"]
        self.speaker.mark_turn_start()
        hints = sorted(set(self.registry.hint_words) | set(self.cfg.get("hint_words") or []))
        text = self.stt.transcribe(audio, hints=hints)
        t_stt = self.clock()
        if source == "wake":
            text = strip_wake_words(text)
        log.info("heard (%s, stt %.2fs): %r", source, t_stt - t_end, text)
        if turn["cancelled"]:
            return
        if not _meaningful(text) or is_stop_command(text):
            if source == "ptt" and not _meaningful(text):
                self.ui.notice("I didn't catch that.")
            return
        self.ui.user_text(text)
        speak = self.cfg.get("reply_mode", "both") != "text"
        splitter = SentenceSplitter()
        streamed = []
        first = {}

        def on_text(chunk):
            if turn["cancelled"]:
                return
            if not streamed:
                first["token"] = self.clock()
            streamed.append(chunk)
            self.ui.reply_text(chunk)
            if speak:
                for s in splitter.feed(chunk):
                    self._speak(turn, s)

        def on_notice(msg):
            if turn["cancelled"]:
                return
            self.ui.notice(msg)
            if speak:
                self._speak(turn, msg)

        with self.turn_lock:              # the brain handles one question at a time
            if turn["cancelled"]:
                return
            reply = self.brain.ask(text, on_text=on_text, on_notice=on_notice)
        if turn["cancelled"]:
            return
        if not streamed or not reply.ok:
            self.ui.reply_text(reply.text)
            if speak:
                self._speak(turn, reply.text)
        elif speak:
            for s in splitter.flush():
                self._speak(turn, s)
        self.ui.reply_text("", final=True)
        if "token" in first:
            log.info("latency: speech end -> first text %.2fs", first["token"] - t_end)
        if speak:
            self.speaker.wait_idle(lambda: turn["cancelled"])
            if self.speaker.first_audio_at and self.speaker.first_audio_at > t_end:
                log.info("latency: speech end -> first spoken word %.2fs",
                         self.speaker.first_audio_at - t_end)

    def _speak(self, turn, text):
        if not turn["cancelled"]:
            turn["spoke"] = True
            self.speaker.say(text)
