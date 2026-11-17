"""Reads the ONE microphone chosen in settings, as 16 kHz mono int16 frames of 80 ms."""
import logging
import queue
import time

import numpy as np
import sounddevice as sd

from . import devices

log = logging.getLogger(__name__)
RATE, FRAME = 16000, 1280


class Mic:
    def __init__(self, cfg):
        self.cfg = cfg
        self.stream = None
        self.q: queue.Queue = queue.Queue(maxsize=200)
        self.last_frame = 0.0
        self.name = ""
        self.last_refresh = 0.0

    @staticmethod
    def com_init():
        devices.com_init()

    def open(self) -> bool:
        self.close()
        idx = devices.resolve("input", self.cfg.get("mic_device", ""))
        if idx is None and time.monotonic() - self.last_refresh > 10:
            # re-scan for a re-plugged mic (rarely: a re-scan briefly resets all audio streams)
            self.last_refresh = time.monotonic()
            devices.refresh()
            idx = devices.resolve("input", self.cfg.get("mic_device", ""))
        if idx is None:
            log.warning("microphone %r not found", self.cfg.get("mic_device") or "(default)")
            return False
        try:
            self.stream = sd.InputStream(device=idx, samplerate=RATE, channels=1, dtype="int16",
                                         blocksize=FRAME, callback=self._cb,
                                         extra_settings=devices.wasapi_settings())
            self.stream.start()
        except Exception:
            log.exception("could not open microphone %s", idx)
            self.stream = None
            return False
        self.name = sd.query_devices(idx)["name"]
        self.last_frame = time.monotonic()
        log.info("listening on microphone: %s", self.name)
        return True

    def _cb(self, indata, frames, t, status):
        if status:
            log.debug("mic status: %s", status)
        self.last_frame = time.monotonic()
        try:
            self.q.put_nowait(indata[:, 0].copy())
        except queue.Full:            # loop fell behind; drop the oldest audio
            try:
                self.q.get_nowait()
            except queue.Empty:
                pass

    def read(self, timeout=0.5):
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def healthy(self) -> bool:
        return bool(self.stream and self.stream.active and time.monotonic() - self.last_frame < 2.0)

    def close(self):
        if self.stream:
            try:
                self.stream.close()
            except Exception:
                log.debug("mic close failed", exc_info=True)
        self.stream = None
        while not self.q.empty():
            self.q.get_nowait()


def level(frame: np.ndarray) -> float:
    """0..1 loudness for a level meter."""
    if frame is None or not len(frame):
        return 0.0
    rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
    return min(1.0, rms / 6000.0)
