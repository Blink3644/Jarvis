"""Volume (pycaw) and media keys."""
import ctypes
import logging
from ctypes import wintypes

from jarvis.core.registry import Tool

log = logging.getLogger(__name__)

VK = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2}
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1


def _endpoint():
    import comtypes
    from pycaw.pycaw import AudioUtilities
    try:
        comtypes.CoInitialize()
    except OSError:
        pass                                   # already initialised in this thread
    return AudioUtilities.GetSpeakers().EndpointVolume


def _state(ep) -> dict:
    return {"volume": round(ep.GetMasterVolumeLevelScalar() * 100), "muted": bool(ep.GetMute())}


def get_volume() -> dict:
    return _state(_endpoint())


def set_volume(percent) -> dict:
    p = max(0, min(100, int(round(float(percent)))))
    ep = _endpoint()
    ep.SetMasterVolumeLevelScalar(p / 100, None)
    if p > 0 and ep.GetMute():
        ep.SetMute(0, None)
    return _state(ep)


def change_volume(delta) -> dict:
    ep = _endpoint()
    cur = round(ep.GetMasterVolumeLevelScalar() * 100)
    return set_volume(cur + float(delta))


def set_mute(muted) -> dict:
    ep = _endpoint()
    ep.SetMute(1 if muted else 0, None)
    return _state(ep)


class _KBD(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _MOUSE(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KBD), ("mi", _MOUSE)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def send_key(vk: int) -> None:
    """Press and release one virtual key via SendInput."""
    items = (_INPUT * 2)()
    for i, flags in enumerate((0, KEYEVENTF_KEYUP)):
        items[i].type = INPUT_KEYBOARD
        items[i].ki = _KBD(vk, 0, flags, 0, 0)
    ctypes.windll.user32.SendInput(2, ctypes.byref(items), ctypes.sizeof(_INPUT))


def media_control(action: str) -> dict:
    vk = VK.get(str(action).lower().replace(" ", "_"))
    if vk is None:
        return {"error": f"Unknown action {action}. Use play_pause, next, previous or stop."}
    send_key(vk)
    return {"done": action}


# Words that point to these tools; a request only describes the tools whose topic matches.
TOPICS = [
    ("volume", r"volume|louder|quieter|softer|mute|muted|unmute|sound|loud|quiet|turn\s+(it|that|this|the\s+\w+)?"
               r"\s*(up|down)|crank|lower|raise", ["get_volume", "set_volume", "change_volume", "set_mute"]),
    ("music", r"play|playing|pause|resume|stop|skip|next|previous|back|song|songs|track|music|tune|tunes",
     ["media_control"]),
]


def register(reg, cfg: dict) -> None:
    def obj(props, req=()):
        return {"type": "object", "properties": props, "required": list(req)}

    def T(**kw):
        reg.register(Tool(module="pc", visual=False, **kw))

    T(name="get_volume", side_effect=False, description="Get the current system volume (0-100) and whether it is muted.",
      handler=get_volume)
    T(name="set_volume", description="Set the PC (system) volume to a percentage from 0 to 100. If the user means the "
                                    "music or Spotify, use spotify_volume instead when it is available.",
      handler=lambda percent: set_volume(percent),
      parameters=obj({"percent": {"type": "number"}}, ["percent"]))
    T(name="change_volume", description="Raise or lower the PC (system) volume by a number of percentage points "
                                       "(e.g. 10 or -10). If the user means the music or Spotify, use "
                                       "spotify_volume instead when it is available.",
      handler=lambda delta: change_volume(delta),
      parameters=obj({"delta": {"type": "number"}}, ["delta"]))
    T(name="set_mute", description="Mute (true) or unmute (false) the system sound.",
      handler=lambda muted: set_mute(muted),
      parameters=obj({"muted": {"type": "boolean"}}, ["muted"]))
    T(name="media_control", description="Control media playback: play_pause, next, previous or stop.",
      handler=lambda action: media_control(action),
      parameters=obj({"action": {"type": "string", "enum": list(VK)}}, ["action"]))
