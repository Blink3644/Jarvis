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

from jarvis.core.secrets import redact

from .endpoint import DONE, SPEAKING, TIMEOUT, EndpointSettings, Endpointer
from jarvis.core.personality import sign_off

from .text import SentenceSplitter, is_closing_remark, is_stop_command, strip_wake_command

log = logging.getLogger(__name__)

RATE = 16000
FRAME = 1280            # 80 ms, what openwakeword wants
SUB = 320               # 20 ms, what the VAD wants
SELF_GUARD_S = 0.5      # wake word stays paused this long after Jarvis stops talking
PREROLL_FRAMES = 20     # 1.6 s kept from before a wake word / key press
WAKE_PREROLL = 20       # wake turns keep all of it: Whisper must hear the whole "Hey Jarvis" so it can be
                        # stripped; a shorter clip held only "-vis", heard as "Just ..." / "service, ..."
PTT_PREROLL = 3         # 0.24 s: catches a word started just before the key press

def level_db(audio) -> str:
    """Peak level of a recording in dBFS, for the log ('-6 dB' is loud, below '-30 dB' is too quiet)."""
    if audio is None or not len(audio):
        return "n/a"
    peak = int(np.max(np.abs(audio.astype(np.int32))))
    return "%.0f dB" % (20 * np.log10(max(peak, 1) / 32768))


IDLE, LISTEN, PTT, BUSY, FOLLOWUP, MIC_LOST = "idle", "listen", "ptt", "busy", "followup", "mic_lost"
LIVE = "live"            # Stage A8: a Gemini Live conversation is open

# Whisper sometimes "hears" these in noise; never treat them as a command on their own.
_HALLUCINATIONS = {"", "you", "thank you", "thanks for watching", "thank you for watching",
                   "bye", ".", "uh", "um", "hmm", "so"}


def _meaningful(text: str) -> bool:
    t = "".join(c for c in text.lower() if c.isalnum() or c in " '").strip()
    return t not in _HALLUCINATIONS


class VoiceAssistant:
    def __init__(self, cfg, brain, registry, *, mic, wake, stt, speaker, vad, ui, ptt=None,
                 sounds=None, clock=time.monotonic, live_factory=None):
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
        self.live_factory = live_factory   # fn(**callbacks) -> LiveSession, or None
        self.live = None
        self.live_source = ""
        self.live_frames: list[np.ndarray] = []   # audio kept until Live answers (for the fallback)
        self.live_blocked_until = 0.0
        self.live_end_pending = False      # close Live once the sign-off finishes playing
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
        if self.live:
            self.live.close()
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
            if self.state == LIVE:
                self._end_live()
            elif self.state != IDLE and self.state != BUSY:
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
        elif st == LIVE:
            self._live_frame(frame)

    def _event(self, ev):
        if ev == "ptt_down":
            if self.muted:
                return
            if self.state == MIC_LOST:
                self._say_now("I can't hear you. The microphone selected in settings isn't connected.")
                return
            if self.state == PTT:
                return
            if self.state == LIVE:
                self.speaker.interrupt()  # talk over Jarvis; the Live session keeps listening
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
            if (follow and turn["spoke"] and not turn["cancelled"] and not turn.get("end")
                    and self.state == BUSY):
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
        elif isinstance(ev, tuple) and ev[0] == "live_closed":
            self._live_closed(*ev[1:])
        elif ev == "live_stop":
            if self.state == LIVE:
                log.info("'stop' heard in live mode")
                self._end_live()
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

    def _start_listen(self, source, fallback=False):
        if source in ("wake", "ptt") and not fallback and self.live_enabled():
            self._start_live(source)
            return
        self.source = source
        keep = {"wake": WAKE_PREROLL, "ptt": PTT_PREROLL}.get(source, 0) if not fallback else 0
        self.buf = list(self.ring)[-keep:] if keep else []
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
        if source != "followup" and not fallback and self.cfg.get("listen_sound", True) and self.sounds:
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

    # ---------- Stage A8: Gemini Live ----------
    def live_enabled(self) -> bool:
        return bool(self.cfg.get("live_mode", False) and self.live_factory
                    and self.clock() >= self.live_blocked_until)

    def _start_live(self, source):
        self.live_source = source
        self.live_frames = list(self.ring)[-3:]
        if self.cfg.get("listen_sound", True) and self.sounds:
            self.speaker.play_now(self.sounds["listen"])
        holder = {}

        def mine():
            return self.live is holder.get("s")

        def on_audio(pcm):
            if mine():
                self.speaker.play_now(pcm)

        def on_user_text(chunk):
            s = holder.get("s")
            if not mine():
                return
            self.ui.user_text(chunk)
            if self.cfg.get("stop_word_enabled", True) and is_stop_command(s.user_text):
                self.events.put("live_stop")

        def on_reply_text(chunk):
            if mine():
                self.ui.reply_text(chunk)

        def on_turn_complete():
            if mine():
                self.ui.reply_text("", final=True)
                # "Thanks Jarvis" in Live mode: close the session once the sign-off has been spoken
                if (getattr(self.registry, "conversation_over", False)
                        or is_closing_remark(getattr(self.registry, "last_user_text", ""))):
                    self.live_end_pending = True

        def on_interrupted():
            if mine():
                self.speaker.interrupt()   # the user talked over Jarvis

        def on_closed(err, replied):
            self.events.put(("live_closed", holder.get("s"), err, replied))

        s = self.live_factory(on_audio=on_audio, on_user_text=on_user_text,
                              on_reply_text=on_reply_text, on_turn_complete=on_turn_complete,
                              on_interrupted=on_interrupted, on_closed=on_closed)
        holder["s"] = s
        self.live = s
        self._set(LIVE)
        s.start()
        for f in self.live_frames:
            s.send_audio(f)

    def _live_frame(self, frame):
        s = self.live
        if s is None:
            self._go_idle()
            return
        s.send_audio(frame)
        if not s.replied:
            self.live_frames.append(frame)
            keep = int(self.cfg.get("max_record_seconds", 15) * RATE / FRAME)
            if len(self.live_frames) > keep:
                self.live_frames = self.live_frames[-keep:]
        else:
            self.live_frames = []
        if self.live_end_pending and not self.speaker.busy and not self.speaking:
            log.info("live: user wrapped up, closing the session")
            self._end_live()
            return
        idle = float(self.cfg.get("live_idle_seconds", 20))
        if s.idle_for() > idle and not self.speaker.busy and not self.speaking:
            log.info("live session idle for %.0fs: closing to save quota", idle)
            self._end_live()

    def _end_live(self):
        s, self.live = self.live, None
        self.live_end_pending = False
        if s is not None:
            s.close()
        self.live_frames = []
        self.wake_resume_at = self.clock() + SELF_GUARD_S
        self.wake.reset()
        self._go_idle()

    def _live_closed(self, session, err, replied):
        if session is not self.live:
            return                         # we already moved on (idle timeout, "stop", mute)
        self.live = None
        frames, self.live_frames = self.live_frames, []
        if err is None:
            self._end_live()
            return
        from .live import classify_live_error
        kind = classify_live_error(err)
        self.live_blocked_until = self.clock() + (600 if kind == "quota" else 120)
        if replied:
            self.ui.notice("Live mode dropped out. Say Hey Jarvis to carry on.")
            self._end_live()
            return
        why = {"quota": "I've hit the Live limit", "key": "Live mode isn't allowed with this key"}.get(
            kind, "I couldn't connect to Live mode")
        self.ui.notice(f"{why}, so I'm using normal mode.")
        if self.state != LIVE:
            return
        # Hand the audio heard so far to the normal pipeline, which keeps listening from here.
        self._start_listen(self.live_source, fallback=True)
        for f in frames:
            if self.state == LISTEN:
                self._listen_frame(f)
            elif self.state == PTT:
                self.buf.append(f)
            else:
                break

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
            log.info("heard while busy: %r", redact(text))
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
        hints = sorted(set(self.registry.hint_words) | set(self.cfg.get("hint_words") or [])
                       | ({"Jarvis"} if source == "wake" else set()))
        text = self.stt.transcribe(audio, hints=hints)
        t_stt = self.clock()
        if source == "wake":
            text = strip_wake_command(text)
        log.info("heard (%s, stt %.2fs, level %s): %r", source, t_stt - t_end, level_db(audio), redact(text))
        if self.cfg.get("keep_debug_clips"):
            try:
                from . import debug_clips
                debug_clips.save(audio, text, source)
            except Exception:
                log.warning("couldn't save the debug clip", exc_info=True)
        if turn["cancelled"]:
            return
        if (source != "followup" or _meaningful(text)) and is_closing_remark(text) \
                and self._can_close_locally():
            # "Thanks Jarvis": the job is done. Sign off without asking the AI, and don't keep listening.
            turn["end"] = True
            self.ui.user_text(text)
            msg = sign_off(self.cfg, text)
            log.info("closing remark: signing off, no follow-up listening")
            self.ui.reply_text(msg, final=True)
            if self.cfg.get("reply_mode", "both") != "text":
                self._speak(turn, msg)
                self.speaker.wait_idle(lambda: turn["cancelled"])
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
                    say_sentence(s)

        # Long answers: speak the first few sentences, the full text stays in the on-screen panel.
        limit = self.cfg.get("spoken_sentence_limit", 3) if self.cfg.get("reply_mode", "both") == "both" else 0
        count = {"n": 0, "held": False}

        def say_sentence(s):
            count["n"] += 1
            if limit and count["n"] > limit:
                count["held"] = True
                return
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
        if getattr(self.registry, "conversation_over", False):
            turn["end"] = True             # the AI judged the user is done (end_conversation tool)
            log.info("AI ended the conversation: no follow-up listening")
        if not streamed or not reply.ok:
            self.ui.reply_text(reply.text)
            if speak:
                self._speak(turn, reply.text)
        elif speak:
            for s in splitter.flush():
                say_sentence(s)
            if count["held"]:
                self._speak(turn, "The rest is on screen.")
        self.ui.reply_text("", final=True)
        if "token" in first:
            log.info("latency: speech end -> first text %.2fs", first["token"] - t_end)
        if speak:
            self.speaker.wait_idle(lambda: turn["cancelled"])
            if self.speaker.first_audio_at and self.speaker.first_audio_at > t_end:
                log.info("latency: speech end -> first spoken word %.2fs",
                         self.speaker.first_audio_at - t_end)

    def _can_close_locally(self) -> bool:
        """The quick 'thanks' shortcut only when Jarvis isn't waiting on an answer; otherwise the AI
        reads the context ('yes thanks' to 'shall I close Chrome?' is a yes, not a goodbye)."""
        if getattr(self.registry, "pending", None):
            return False
        return not (getattr(self.registry, "last_reply", "") or "").rstrip().endswith("?")

    def _speak(self, turn, text):
        if not turn["cancelled"]:
            turn["spoke"] = True
            self.speaker.say(text)
