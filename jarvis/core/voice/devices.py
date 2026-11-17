"""Audio device lists. Only real microphones are offered as inputs: anything that carries
PC/game audio (Stereo Mix, loopback, virtual cables) is filtered out, so game sound and
Discord voices can never reach the wake word.

Windows lists each device once per audio system (MME, DirectSound, WASAPI, WDM-KS).
We use WASAPI only: full names, and Windows converts the sample rate for us.
"""
import logging
import threading

import sounddevice as sd

log = logging.getLogger(__name__)
_lock = threading.Lock()

# names that mean "this input is really an output being recorded"
_NOT_A_MIC = ("stereo mix", "what u hear", "what you hear", "loopback", "wave out", "waveout",
              "mixage st", "stereomix", "cable output", "voicemeeter out", "vb-audio point",
              "monitor of", "sonar - stream", "sonar - media", "sonar - gaming", "sonar - chat")


def is_real_mic(name: str) -> bool:
    low = name.lower()
    return not any(bad in low for bad in _NOT_A_MIC)


def _wasapi_index():
    for i, api in enumerate(sd.query_hostapis()):
        if "wasapi" in api["name"].lower():
            return i, api
    return None, None


def refresh():
    """Re-scan devices (PortAudio caches the list). Stops any open streams."""
    with _lock:
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            log.exception("audio device refresh failed")


def _list(kind: str) -> list[dict]:
    api_i, api = _wasapi_index()
    out, seen = [], set()
    chan = "max_input_channels" if kind == "input" else "max_output_channels"
    for d in sd.query_devices():
        if d[chan] < 1 or (api_i is not None and d["hostapi"] != api_i):
            continue
        if kind == "input" and not is_real_mic(d["name"]):
            continue
        if d["name"] in seen:
            continue
        seen.add(d["name"])
        out.append({"index": d["index"], "name": d["name"],
                    "default": api is not None and d["index"] == api[f"default_{kind}_device"]})
    return out


def list_inputs() -> list[dict]:
    return _list("input")


def list_outputs() -> list[dict]:
    return _list("output")


def resolve(kind: str, wanted: str):
    """Index of the device named `wanted` ("" = Windows default). None if it isn't connected."""
    devs = _list(kind)
    if wanted:
        for d in devs:
            if d["name"] == wanted:
                return d["index"]
        return None
    for d in devs:
        if d["default"]:
            return d["index"]
    return devs[0]["index"] if devs else None


def com_init():
    """WASAPI streams can only be opened from threads that have COM set up. Call this
    first thing in any thread that opens a mic or speaker stream."""
    try:
        import ctypes
        ctypes.windll.ole32.CoInitializeEx(None, 0)     # COINIT_MULTITHREADED
    except Exception:
        log.debug("CoInitializeEx failed", exc_info=True)


def wasapi_settings():
    return sd.WasapiSettings(exclusive=False, auto_convert=True)
