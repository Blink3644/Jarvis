"""Single instance: a named Windows mutex. A second launch sees it and stops.
(Focusing the existing window arrives with the tray/settings UI in Stage A4.)"""
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
