"""look_at_screen tool and auto-look. Captures only happen inside these calls, to memory."""
import io
import logging
import re

from jarvis.core.registry import Media, Tool
from jarvis.pc import games

log = logging.getLogger(__name__)

MAX_SIDE = 1280
JPEG_Q = 80

_NON_VISUAL = re.compile(
    r"\b(volume|mute|unmute|timers?|alarms?|remind(er)?s?|notes?|remember|routines?|weather|"
    r"what('s| is) the (time|date)|what time|lock|shut ?down|restart|sleep|close|quit|"
    r"(open|launch|start)\s+\w+|(play|pause|resume|skip)\b|next (song|track)|previous (song|track)|"
    r"(turn|change) (\w+ ){0,3}(up|down)|louder|quieter|softer|crank|(up|down) (by )?(about |like )?\d+|"
    r"(music|spotify|song|tunes) (back )?(up|down|to \d+)|clip (that|it)|save (that|a) (clip|replay))\b", re.I)


_LOOK = re.compile(
    r"\b(look|looking|see|seeing|screen|screenshot|this|that|these|here|showing|shown|display(ed)?|"
    r"window|read|beat|boss|setting|menu|what'?s that|what am i)\b", re.I)


def user_asked_look(text: str) -> bool:
    """Lenient check that the user's own words are about what's on screen."""
    return bool(_LOOK.search(text or ""))


def make_gate(cfg):
    def gate(user_text: str) -> str | None:
        if cfg.get("auto_look", "games") != "ask" or user_asked_look(user_text):
            return None
        return "I only look at your screen when you ask me to, e.g. 'look at my screen'."
    return gate


def is_non_visual(text: str) -> bool:
    return bool(_NON_VISUAL.search(text or ""))


def blocked(title: str, exe: str, words) -> str | None:
    hay = f"{title} {exe}".lower()
    for w in words or []:
        if w and str(w).lower() in hay:
            return str(w)
    return None


def is_black(img) -> bool:
    from PIL import ImageStat
    st = ImageStat.Stat(img.convert("L").resize((64, 64)))
    return st.mean[0] < 3 and st.stddev[0] < 3


def encode(img) -> bytes:
    from PIL import Image
    img = img.convert("RGB")
    w, h = img.size
    s = MAX_SIDE / max(w, h)
    if s < 1:
        img = img.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=JPEG_Q)
    return buf.getvalue()


def grab(rect=None):
    """rect = (l, t, r, b) in virtual-screen px, or None for the primary monitor. Returns a PIL image."""
    import mss
    from PIL import Image
    with mss.MSS() as sct:
        mons = sct.monitors
        virt = mons[0]
        region = None
        if rect:
            l = max(rect[0], virt["left"])
            t = max(rect[1], virt["top"])
            r = min(rect[2], virt["left"] + virt["width"])
            b = min(rect[3], virt["top"] + virt["height"])
            if r - l > 10 and b - t > 10:
                region = {"left": l, "top": t, "width": r - l, "height": b - t}
        if region is None:
            region = mons[1]
        shot = sct.grab(region)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def window_rect(hwnd):
    import win32gui
    try:
        if win32gui.IsIconic(hwnd):
            return None
        return win32gui.GetWindowRect(hwnd)
    except Exception:
        return None


def capture_window(info, words) -> tuple[Media | None, str | None, str]:
    """info from games.gather_info(). Returns (media, error, title)."""
    title = (info or {}).get("title", "")
    exe = (info or {}).get("exe", "")
    hit = blocked(title, exe, words)
    if hit:
        return None, f"I won't look at this window (it matches your never-screenshot list: {hit}).", title
    rect = window_rect(info["hwnd"]) if info and info.get("hwnd") else None
    try:
        img = grab(rect)
    except Exception:
        log.exception("window capture failed, trying whole screen")
        try:
            img = grab(None)
        except Exception as e:
            return None, f"I couldn't capture the screen ({type(e).__name__}).", title
    if is_black(img):
        return None, ("The capture came out black. If this is a game, switch it to borderless or "
                      "windowed mode and ask again."), title
    return Media("image/jpeg", encode(img), f"Screenshot of {title or 'the screen'}"), None, title


def make_look(cfg):
    def look_at_screen(question: str = "") -> dict:
        info = games.gather_info()
        media, err, title = capture_window(info, cfg.get("never_screenshot", []))
        if err:
            return {"error": err}
        return {"result": f"Screenshot of {title or 'the screen'} attached.", "_media": [media]}
    return look_at_screen


def make_auto_look(cfg):
    def provider(user_text: str) -> list[Media]:
        mode = cfg.get("auto_look", "games")
        if mode == "ask" or is_non_visual(user_text):
            return []
        if mode == "games" and not games.current_game(cfg):
            return []
        info = games.gather_info()
        if not info:
            return []
        media, err, _ = capture_window(info, cfg.get("never_screenshot", []))
        if err:
            log.info("auto-look skipped: %s", err)
            return []
        log.info("auto-look: screenshot attached (side-effect tools in this turn will ask for a yes)")
        return [media]
    return provider


# Words that point to these tools; a request only describes the tools whose topic matches.
TOPICS = [("screen", r"screen|look|looking|see|seeing|showing|shown|window|this|that|here|boss|setting|"
                     r"settings|error|message|read|says|beat|puzzle|map|menu|what's\s+this", ["look_at_screen"])]


def register(reg, cfg) -> None:
    reg.register(Tool(
        name="look_at_screen", side_effect=False, brings_data=True,
        description=("Look at the user's focused window (or screen) and see what is on it. Use ONLY when "
                     "the user's question is about what is on their screen, e.g. 'what does this setting "
                     "do?' or 'how do I beat this boss?'. The screenshot is attached to the result."),
        handler=make_look(cfg), gate=make_gate(cfg),
        parameters={"type": "object", "properties": {
            "question": {"type": "string", "description": "What the user wants to know about the screen"}}},
        module="pc", visual=True))
    reg.add_attachments(make_auto_look(cfg))
