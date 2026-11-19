"""copy_to_clipboard: put text on the Windows clipboard so the user can paste it with Ctrl+V.

Safety: this ONLY sets the clipboard. It never presses keys or pastes into other apps/games.
Privacy: the clipboard text is never logged (only its length).
"""
import ctypes
import logging
import time
from ctypes import wintypes

from jarvis.core.registry import Registry, Tool

log = logging.getLogger(__name__)

MAX_CHARS = 20_000
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def _api():
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    u32.OpenClipboard.argtypes = [wintypes.HWND]
    u32.OpenClipboard.restype = wintypes.BOOL
    u32.CloseClipboard.restype = wintypes.BOOL
    u32.EmptyClipboard.restype = wintypes.BOOL
    u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    u32.SetClipboardData.restype = wintypes.HANDLE
    u32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    u32.CreateWindowExW.restype = wintypes.HWND
    u32.DestroyWindow.argtypes = [wintypes.HWND]
    k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k32.GlobalAlloc.restype = wintypes.HGLOBAL
    k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalLock.restype = wintypes.LPVOID
    k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    k32.GlobalFree.restype = wintypes.HGLOBAL
    return u32, k32


def set_clipboard(text: str, tries: int = 10) -> None:
    """Replace the clipboard contents with `text` (Win32 API; no extra packages)."""
    u32, k32 = _api()
    # a hidden owner window: with no owner, SetClipboardData may fail (same trick pyperclip uses)
    hwnd = u32.CreateWindowExW(0, "STATIC", None, 0, 0, 0, 0, 0, None, None, None, None)
    try:
        for i in range(tries):                       # another app may be holding the clipboard
            if u32.OpenClipboard(hwnd):
                break
            time.sleep(0.05)
        else:
            raise OSError("the clipboard is busy (another app is using it)")
        try:
            if not u32.EmptyClipboard():
                raise ctypes.WinError(ctypes.get_last_error())
            data = (text + "\0").encode("utf-16-le")
            h = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not h:
                raise ctypes.WinError(ctypes.get_last_error())
            p = k32.GlobalLock(h)
            ctypes.memmove(p, data, len(data))
            k32.GlobalUnlock(h)
            if not u32.SetClipboardData(CF_UNICODETEXT, h):
                k32.GlobalFree(h)                    # only freed on failure; Windows owns it on success
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            u32.CloseClipboard()
    finally:
        if hwnd:
            u32.DestroyWindow(hwnd)


# Words that point to these tools; a request only describes the tools whose topic matches.
TOPICS = [("clipboard", r"copy|copied|clipboard|paste", ["copy_to_clipboard"])]


def register(reg: Registry, cfg: dict, setter=None) -> None:
    setter = setter or set_clipboard

    def copy_to_clipboard(text: str = "", last_answer: bool = False) -> dict:
        text = "" if text is None else str(text)
        if last_answer or not text.strip():
            text = reg.last_reply or ""
        if not text.strip():
            return {"error": "There's nothing to copy yet. Tell the user what you can copy."}
        truncated = len(text) > MAX_CHARS
        if truncated:
            text = text[:MAX_CHARS]
        try:
            setter(text)
        except Exception as e:
            log.warning("clipboard write failed: %s", type(e).__name__)   # never log the text
            return {"error": "Couldn't reach the clipboard (another app may be using it). Try again."}
        log.info("copied %d characters to the clipboard", len(text))
        out = {"copied": True, "characters": len(text),
               "note": "Tell the user it's on the clipboard and they can paste it with Ctrl+V."}
        if truncated:
            out["truncated_to"] = MAX_CHARS
        return out

    reg.register(Tool(
        "copy_to_clipboard",
        "Put text on the user's Windows clipboard so THEY can paste it with Ctrl+V. It only sets the "
        "clipboard; it never types or pastes anywhere. Use when the user asks to copy something "
        "(e.g. 'copy that', 'put the weather on my clipboard', 'copy my note about the router'). "
        "For 'that' / 'your last answer', set last_answer=true (copies your previous reply word for "
        "word). Otherwise pass the exact text in `text`: get it first with another tool if needed "
        "(e.g. weather, list_notes) and copy the useful content itself, not a description of it. "
        f"Max {MAX_CHARS} characters.",
        copy_to_clipboard,
        {"type": "object", "properties": {
            "text": {"type": "string", "description": "The exact text to copy."},
            "last_answer": {"type": "boolean",
                            "description": "True to copy your previous reply instead of `text`."}}},
        module="pc", visual=False))
