"""Single instance: a named Windows mutex. A second launch sees it and stops.
A second launch signals the running one, which opens its settings window."""
import ctypes
import sys

_handle = None
ERROR_ALREADY_EXISTS = 183


def acquire(name: str = "Local\\JarvisVoiceAssistant") -> bool:
    global _handle
    if sys.platform != "win32":
        return True
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    _handle = k32.CreateMutexW(None, False, name)
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


_SHOW_EVENT = r"Local\JarvisShowSettings"


def signal_existing() -> None:
    """Second launch: ask the running Jarvis to come to the front (it opens its settings window)."""
    if sys.platform != "win32":
        return
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenEventW.restype = ctypes.c_void_p
    h = k32.OpenEventW(0x0002, False, _SHOW_EVENT)          # EVENT_MODIFY_STATE
    if h:
        k32.SetEvent(ctypes.c_void_p(h))
        k32.CloseHandle(ctypes.c_void_p(h))


def listen_for_second_launch(callback) -> None:
    """Running Jarvis: call callback() whenever another launch is attempted (background thread)."""
    if sys.platform != "win32":
        return
    import threading
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateEventW.restype = ctypes.c_void_p
    h = ctypes.c_void_p(k32.CreateEventW(None, False, False, _SHOW_EVENT))   # auto-reset

    def loop():
        while k32.WaitForSingleObject(h, 0xFFFFFFFF) == 0:
            try:
                callback()
            except Exception:
                pass
    threading.Thread(target=loop, name="second-launch", daemon=True).start()
