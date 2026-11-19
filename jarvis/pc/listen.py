"""listen_to_audio: record PC audio (WASAPI loopback) or a named input device, only when asked."""
import difflib
import io
import logging
import re
import time
import wave

import numpy as np

from jarvis.core.registry import Media, Tool

log = logging.getLogger(__name__)

HARD_MAX_S = 120
TARGET_RATE = 16000
SILENT_RMS = 30          # int16 units

_ASKED = re.compile(
    r"\b(listen|listening|hear|hearing|song|music|playing|identify|recogni[sz]e|what did .{1,40} say|what('s| is) (this|that) "
    r"(song|track|music|sound)|record (the |this )?(pc )?(audio|sound)|summari[sz]e what|"
    r"what('s| is) being said|who('s| is) (singing|talking|speaking))\b", re.I)


def user_asked(text: str) -> bool:
    return bool(_ASKED.search(text or ""))


def to_wav16k(pcm: np.ndarray, rate: int, channels: int) -> bytes:
    """pcm: int16 interleaved. Returns 16 kHz mono int16 WAV bytes."""
    a = pcm.astype(np.float32)
    if channels > 1:
        n = (len(a) // channels) * channels
        a = a[:n].reshape(-1, channels).mean(axis=1)
    if rate != TARGET_RATE and len(a) > 1:
        m = int(len(a) * TARGET_RATE / rate)
        a = np.interp(np.linspace(0, len(a) - 1, m), np.arange(len(a)), a)
    out = np.clip(a, -32768, 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(TARGET_RATE)
        w.writeframes(out.tobytes())
    return buf.getvalue()


def rms(pcm: np.ndarray) -> float:
    return float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2))) if len(pcm) else 0.0


def clamp_seconds(seconds, default, max_s) -> float:
    cap = min(float(max_s or HARD_MAX_S), HARD_MAX_S)
    try:
        s = float(seconds) if seconds else float(default or 15)
    except (TypeError, ValueError):
        s = float(default or 15)
    return max(1.0, min(s, cap))


def match_device(query: str, names: list[str]) -> int | None:
    q = query.lower().strip()
    low = [n.lower() for n in names]
    for i, n in enumerate(low):
        if q in n:
            return i
    close = difflib.get_close_matches(q, low, n=1, cutoff=0.4)
    return low.index(close[0]) if close else None


def record(seconds: float, device: str, cancel) -> tuple[np.ndarray, int, int, str]:
    import pyaudiowpatch as pa
    p = pa.PyAudio()
    try:
        if device:
            ins = [d for d in p.get_device_info_generator()
                   if d["maxInputChannels"] > 0 and not d.get("isLoopbackDevice")]
            idx = match_device(device, [d["name"] for d in ins])
            if idx is None:
                raise ValueError(f"No input device matching '{device}'.")
            dev = ins[idx]
        else:
            dev = p.get_default_wasapi_loopback()
        rate = int(dev["defaultSampleRate"])
        ch = int(dev["maxInputChannels"])
        stream = p.open(format=pa.paInt16, channels=ch, rate=rate, input=True,
                        input_device_index=dev["index"], frames_per_buffer=1024)
        chunks = []
        end = time.monotonic() + seconds
        try:
            while time.monotonic() < end and not cancel.is_set():
                avail = stream.get_read_available()
                if avail < 256:
                    time.sleep(0.02)
                    continue
                chunks.append(np.frombuffer(stream.read(avail, exception_on_overflow=False), dtype=np.int16))
        finally:
            stream.stop_stream()
            stream.close()
        pcm = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)
        return pcm, rate, ch, dev["name"]
    finally:
        p.terminate()


def make_tool(reg, cfg, recorder=record):
    def listen_to_audio(seconds: float = 0, device: str = "") -> dict:
        if not user_asked(reg.last_user_text):
            return {"error": "I only listen to PC audio when you ask me to directly."}
        n = clamp_seconds(seconds, cfg.get("listen_default_s", 15), cfg.get("listen_max_s", HARD_MAX_S))
        reg.ui_event("pc_audio", {"on": True, "seconds": n})
        try:
            pcm, rate, ch, name = recorder(n, device or "", reg.cancel)
        except Exception as e:
            log.exception("listen failed")
            return {"error": f"I couldn't record the audio ({e})."}
        finally:
            reg.ui_event("pc_audio", {"on": False})
        got = len(pcm) / max(rate * ch, 1)
        if rms(pcm) < SILENT_RMS:
            return {"error": "I couldn't hear anything; nothing was playing."}
        wav = to_wav16k(pcm, rate, ch)
        return {"result": f"Recorded {got:.0f} seconds of PC audio.",
                "_media": [Media("audio/wav", wav, f"{got:.0f} s from {name}")]}
    return listen_to_audio


# Words that point to these tools; a request only describes the tools whose topic matches.
TOPICS = [("audio", r"listen|listening|hear|heard|song|what\s+song|identify|said|saying|say|summari[sz]e|"
                    r"audio|sound|npc|voice|lyrics", ["listen_to_audio"])]


def register(reg, cfg) -> None:
    reg.register(Tool(
        name="listen_to_audio", side_effect=False, brings_data=True,
        description=("Record the PC's audio output (or a named input device) for a few seconds and hear it. "
                     "Call ONLY when the user directly asks you in this same request to listen to or "
                     "identify audio (e.g. 'what song is this?', 'listen for 30 seconds'). NEVER call it "
                     "on your own initiative."),
        handler=make_tool(reg, cfg),
        parameters={"type": "object", "properties": {
            "seconds": {"type": "number", "description": "How long to listen; default 15, max 120"},
            "device": {"type": "string", "description": "Optional input device name instead of PC audio"}}},
        module="pc", visual=False))
