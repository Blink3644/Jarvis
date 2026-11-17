"""Lock, sleep, shutdown, restart, screenshot. Fixed commands only."""
import ctypes
import logging
import subprocess
import time
import uuid
from ctypes import wintypes
from pathlib import Path

from jarvis.core.registry import Tool

log = logging.getLogger(__name__)


class _GUID(ctypes.Structure):
    _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD),
                ("d", ctypes.c_ubyte * 8)]


def pictures_dir() -> Path:
    try:
        g = _GUID.from_buffer_copy(uuid.UUID("{33E28130-4E1E-4676-835A-98395C3BC3BB}").bytes_le)
        buf = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(buf)) == 0:
            path = Path(buf.value)
            ctypes.windll.ole32.CoTaskMemFree(buf)
            return path
    except Exception:
        log.exception("known-folder lookup failed")
    return Path.home() / "Pictures"


def lock_pc() -> dict:
    ctypes.windll.user32.LockWorkStation()
    return {"done": "locked"}


def sleep_pc() -> dict:
    ctypes.windll.powrprof.SetSuspendState(0, 0, 0)
    return {"done": "sleeping"}


def shutdown_pc() -> dict:
    subprocess.run(["shutdown", "/s", "/t", "5"], check=False)
    return {"done": "shutting down in 5 seconds"}


def restart_pc() -> dict:
    subprocess.run(["shutdown", "/r", "/t", "5"], check=False)
    return {"done": "restarting in 5 seconds"}


def take_screenshot(folder: str | Path | None = None) -> dict:
    import mss
    from PIL import Image
    out_dir = Path(folder) if folder else pictures_dir() / "Jarvis Screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / time.strftime("Screenshot %Y-%m-%d %H-%M-%S.png")
    with (getattr(mss, "MSS", None) or mss.mss)() as sct:
        shot = sct.grab(sct.monitors[0])          # all screens
        Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX").save(path)
    return {"saved": str(path)}


def register(reg, cfg: dict) -> None:
    def T(**kw):
        reg.register(Tool(module="pc", visual=False, **kw))

    T(name="lock_pc", description="Lock the computer.", handler=lock_pc)
    T(name="sleep_pc", description="Put the computer to sleep. The user is asked to confirm first.",
      handler=sleep_pc, destructive=True, confirm_text=lambda a: "put the PC to sleep")
    T(name="shutdown_pc", description="Shut the computer down. The user is asked to confirm first.",
      handler=shutdown_pc, destructive=True, confirm_text=lambda a: "shut down the PC")
    T(name="restart_pc", description="Restart the computer. The user is asked to confirm first.",
      handler=restart_pc, destructive=True, confirm_text=lambda a: "restart the PC")
    T(name="take_screenshot", description="Save a screenshot of all screens to the Pictures folder.",
      handler=lambda: take_screenshot())
