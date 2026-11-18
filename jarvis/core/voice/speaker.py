"""Text-to-speech and playback.

Online: edge-tts (Microsoft neural voices, default en-GB-RyanNeural).
Offline / edge-tts failing: pyttsx3 (Windows' built-in voices).
Sentences are synthesised on one thread and played on another, so the next sentence is
being prepared while the current one plays. interrupt() stops everything at once.
"""
import asyncio
import logging
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np
import sounddevice as sd

from . import devices

log = logging.getLogger(__name__)
OUT_RATE = 24000
BLOCK = 1200            # 50 ms


def tone(freqs, ms=70, volume=0.18) -> np.ndarray:
    """A short soft beep (one tone per frequency), as int16 at OUT_RATE."""
    parts = []
    for f in freqs:
        t = np.arange(int(OUT_RATE * ms / 1000)) / OUT_RATE
        env = np.minimum(1, np.minimum(t, t[::-1]) / 0.008)       # click-free fade
        parts.append(np.sin(2 * np.pi * f * t) * env * volume)
    return (np.concatenate(parts) * 32767).astype(np.int16)


# Run by _offline() in a child process: fresh engine each time, saves one WAV file.
_SAPI_SCRIPT = r"""
import sys, pyttsx3
text, path = sys.argv[1], sys.argv[2]
e = pyttsx3.init()
for v in e.getProperty("voices"):
    if "david" in v.name.lower() or "george" in v.name.lower():
        e.setProperty("voice", v.id)
e.setProperty("rate", 185)
e.save_to_file(text, path)
e.runAndWait()
"""


def has_words(text: str) -> bool:
    """False for chunks with nothing to say (e.g. '```', '**', '---'); edge-tts
    answers those with NoAudioReceived."""
    return bool(re.search(r"[^\W_]", text or ""))


SOUNDS = {"listen": tone([880, 1320]), "stop": tone([1320, 880])}


def decode(data: bytes) -> np.ndarray:
    import miniaudio
    d = miniaudio.decode(data, output_format=miniaudio.SampleFormat.SIGNED16,
                         nchannels=1, sample_rate=OUT_RATE)
    return np.frombuffer(d.samples, dtype=np.int16).copy()


class Speaker:
    def __init__(self, cfg, on_speaking=None):
        self.cfg = cfg
        self.on_speaking = on_speaking or (lambda b: None)
        self.say_q: queue.Queue = queue.Queue()
        self.play_q: queue.Queue = queue.Queue()
        self.gen = 0
        self.pending = 0
        self.lock = threading.Lock()
        self.offline_until = 0.0
        self.first_audio_at = 0.0
        self._speaking = False
        threading.Thread(target=self._synth_loop, name="tts-synth", daemon=True).start()
        threading.Thread(target=self._play_loop, name="tts-play", daemon=True).start()
        threading.Thread(target=self._warm_up, name="tts-warm", daemon=True).start()

    def _warm_up(self):
        """The first online request is slow (~1.4 s); do one silently at startup."""
        try:
            asyncio.run(self._edge("Ready."))
        except Exception:
            log.info("voice warm-up failed (offline?)", exc_info=True)

    # ---------- public ----------
    def say(self, text: str):
        text = (text or "").strip()
        if not has_words(text):
            return
        with self.lock:
            self.pending += 1
            self.say_q.put((self.gen, text))

    def play_now(self, pcm: np.ndarray):
        with self.lock:
            self.pending += 1
            self.play_q.put((self.gen, pcm))

    def interrupt(self):
        with self.lock:
            self.gen += 1           # everything queued or in progress is now stale
            for q in (self.say_q, self.play_q):
                while True:
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        break
            self.pending = 0

    @property
    def busy(self) -> bool:
        return self.pending > 0

    def wait_idle(self, cancelled=lambda: False, timeout=120):
        end = time.monotonic() + timeout
        while self.pending > 0 and not cancelled() and time.monotonic() < end:
            time.sleep(0.03)

    def mark_turn_start(self):
        self.first_audio_at = 0.0

    # ---------- synthesis ----------
    def _done_one(self):
        with self.lock:
            self.pending = max(0, self.pending - 1)

    def _synth_loop(self):
        while True:
            gen, text = self.say_q.get()
            if gen != self.gen:
                continue                  # interrupt() already uncounted it
            try:
                pcm = self.synth(text)
            except Exception:
                log.exception("speech synthesis failed")
                pcm = None
            with self.lock:
                if gen != self.gen:
                    continue
                if pcm is None or not len(pcm):
                    self.pending = max(0, self.pending - 1)
                else:
                    self.play_q.put((gen, pcm))

    EDGE_TRIES = 2          # online attempts per sentence before using the offline voice
    OFFLINE_SECONDS = 30    # then retry the online voice after this long
    OFFLINE_TIMEOUT = 20    # the offline voice may never block longer than this

    def synth(self, text: str) -> np.ndarray:
        if time.monotonic() >= self.offline_until:
            err = None
            for attempt in range(self.EDGE_TRIES):
                try:
                    pcm = decode(asyncio.run(self._edge(text)))
                    if self.offline_until:
                        log.info("online voice is back")
                        self.offline_until = 0.0
                    return pcm
                except Exception as e:
                    err = e
                    if attempt + 1 < self.EDGE_TRIES:
                        time.sleep(0.3)
            log.warning("online voice failed %d times (%s: %s) for %r; offline voice for %ds",
                        self.EDGE_TRIES, type(err).__name__, err, text[:60], self.OFFLINE_SECONDS)
            self.offline_until = time.monotonic() + self.OFFLINE_SECONDS
        return self._offline(text)

    async def _edge(self, text: str) -> bytes:
        import edge_tts
        com = edge_tts.Communicate(text, self.cfg.get("tts_voice") or "en-GB-RyanNeural",
                                   connect_timeout=4, receive_timeout=10)
        out = bytearray()
        async for ch in com.stream():
            if ch["type"] == "audio":
                out += ch["data"]
        if not out:
            raise RuntimeError("no audio returned")
        return bytes(out)

    def _offline_cmd(self, text: str, path: str) -> list:
        return [sys.executable, "-c", _SAPI_SCRIPT, text, path]

    def _offline(self, text: str) -> np.ndarray:
        """Windows' built-in voice, run in a separate short-lived process.

        pyttsx3's runAndWait() can block forever on a reused engine (seen in the log:
        the second offline sentence never returned, so Jarvis stayed busy for good).
        A child process with a timeout can always be killed, so it can't hang us.
        """
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            cmd = self._offline_cmd(text, path)
            try:
                subprocess.run(cmd, timeout=self.OFFLINE_TIMEOUT, capture_output=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except subprocess.TimeoutExpired:
                log.warning("offline voice timed out; skipping this sentence")
                return np.zeros(0, dtype=np.int16)
            if not os.path.getsize(path):
                log.warning("offline voice produced no audio")
                return np.zeros(0, dtype=np.int16)
            with open(path, "rb") as f:
                return decode(f.read())
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    # ---------- playback ----------
    def _set_speaking(self, b):
        if b != self._speaking:
            self._speaking = b
            try:
                self.on_speaking(b)
            except Exception:
                log.exception("on_speaking failed")

    def _open(self):
        idx = devices.resolve("output", self.cfg.get("speaker_device", ""))
        if idx is None:
            log.warning("speaker %r not found; using Windows default", self.cfg.get("speaker_device"))
            idx, extra = None, None
        else:
            extra = devices.wasapi_settings()
        s = sd.OutputStream(device=idx, samplerate=OUT_RATE, channels=1, dtype="int16",
                            extra_settings=extra)
        s.start()
        return s

    def _play_loop(self):
        devices.com_init()
        stream = None
        while True:
            try:
                gen, pcm = self.play_q.get(timeout=1.5)
            except queue.Empty:
                if stream is not None:
                    stream.close()
                    stream = None
                self._set_speaking(False)
                continue
            if gen != self.gen:
                continue
            try:
                if stream is None:
                    stream = self._open()
                self._set_speaking(True)
                if not self.first_audio_at:
                    self.first_audio_at = time.monotonic()
                for i in range(0, len(pcm), BLOCK):
                    if gen != self.gen:
                        break
                    stream.write(pcm[i:i + BLOCK].reshape(-1, 1))
            except Exception:
                log.exception("playback failed")
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
                stream = None
            finally:
                if gen == self.gen:
                    self._done_one()
            if self.play_q.empty() and self.say_q.empty() and self.pending == 0:
                self._set_speaking(False)
