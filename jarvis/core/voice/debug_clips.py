"""Opt-in "Keep my last N voice commands for tuning" (off by default).

Saves each voice command as a WAV plus what Jarvis heard, in %APPDATA%/Jarvis/debug_clips/,
so speech-to-text can be benchmarked on the user's real voice. Only the newest KEEP clips are
kept; the settings window has a button to delete them all.
"""
import json
import logging
import time
import wave
from pathlib import Path

import numpy as np

from jarvis.core import paths

log = logging.getLogger(__name__)

KEEP = 20
RATE = 16000


def folder() -> Path:
    return paths.sub("debug_clips")


def save(audio, heard: str, source: str = "", keep: int = KEEP):
    """Save one int16 16 kHz mono clip and its transcript, then drop the oldest past `keep`."""
    if audio is None or not len(audio):
        return None
    d = folder()
    stem = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    wav = d / f"{stem}.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(np.asarray(audio, dtype=np.int16).tobytes())
    (d / f"{stem}.json").write_text(json.dumps({"heard": heard, "source": source}), encoding="utf-8")
    prune(keep)
    return wav


def prune(keep: int = KEEP):
    wavs = sorted(folder().glob("*.wav"))
    for old in wavs[:max(0, len(wavs) - keep)]:
        for p in (old, old.with_suffix(".json")):
            try:
                p.unlink()
            except FileNotFoundError:
                pass


def clear() -> int:
    n = 0
    for p in list(folder().glob("*.wav")) + list(folder().glob("*.json")):
        try:
            p.unlink()
            n += p.suffix == ".wav"
        except OSError:
            log.warning("couldn't delete %s", p.name)
    return n
