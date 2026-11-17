"""On-screen overlay: animated orb + text panel (HUD theme) or a clean panel (Minimal).

Implements the same UI interface as ConsoleUI. Every public method is thread-safe: it only puts a
job on a queue, and the Tk thread (started by start()) applies it. Idle = no animation, hidden =
nothing drawn, so it costs nothing in games.
"""
import logging
import math
import queue
import re
import threading
import time

log = logging.getLogger(__name__)

LONG_CHARS = 300
SUMMARY_TAIL = " The details are on screen."
MAX_SHOWN = 1500
_SENT = re.compile(r"(?<=[.!?])\s+")
_LIST = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+", re.M)

THEMES = {
    "hud": {"bg": "#04121c", "fg": "#bfefff", "dim": "#4f8ba3", "accent": "#35d8ff", "alpha": 0.9, "width": 400},
    "minimal": {"bg": "#202124", "fg": "#f1f3f4", "dim": "#9aa0a6", "accent": "#8ab4f8", "alpha": 0.96, "width": 360},
}
STATE_COLORS = {"idle": "#35d8ff", "listening": "#3cebaa", "thinking": "#ffbe3c", "speaking": "#78dcff"}
STATUS_TO_ORB = {"idle": "idle", "listen": "listening", "ptt": "listening", "followup": "listening",
                 "followup_heard": "thinking", "busy": "thinking", "mic_lost": "idle"}
STATUS_LABEL = {"listen": "Listening...", "ptt": "Listening...", "followup": "Still listening...",
                "busy": "Thinking...", "mic_lost": "Microphone missing"}


def split_summary(text):
    """(spoken, full). Long or list-like replies: speak 1-2 sentences, rest goes on screen."""
    text = (text or "").strip()
    if len(text) <= LONG_CHARS and not _LIST.search(text):
        return text, text
    sents = [s for s in _SENT.split(text.replace("\n", " ")) if s]
    spoken = sents[0] if sents else text
    if len(spoken) < 80 and len(sents) > 1:
        spoken += " " + sents[1]
    if len(spoken) > 220:
        spoken = spoken[:220].rsplit(" ", 1)[0].rstrip(",;:") + "."
    if spoken and spoken[-1] not in ".!?:":
        spoken += "."
    return spoken + SUMMARY_TAIL, text


class AutoHide:
    """Decides when the panel should disappear. Clock is injectable for tests."""

    def __init__(self, hide_s=8, clock=time.monotonic):
        self.hide_s = hide_s
        self.clock = clock
        self.deadline = None
        self.hold = False

    def touch(self, text_len=0):
        extra = min(max(text_len - 120, 0) / 40.0, 20.0)   # +1 s per 40 chars past 120, max +20 s
        self.deadline = self.clock() + self.hide_s + extra

    def expired(self):
        return (not self.hold) and self.deadline is not None and self.clock() >= self.deadline

    def clear(self):
        self.deadline = None


def _clickthrough(hwnd, on):
    try:
        import ctypes
        u = ctypes.windll.user32
        GWL_EXSTYLE, LAYERED, TRANSPARENT, TOOL, NOACT = -20, 0x80000, 0x20, 0x80, 0x08000000
        get = getattr(u, "GetWindowLongPtrW", u.GetWindowLongW)
        setl = getattr(u, "SetWindowLongPtrW", u.SetWindowLongW)
        style = get(hwnd, GWL_EXSTYLE) | LAYERED | TOOL | NOACT
        style = (style | TRANSPARENT) if on else (style & ~TRANSPARENT)
        setl(hwnd, GWL_EXSTYLE, style)
    except Exception:
        log.debug("click-through failed", exc_info=True)


class Overlay:
    def __init__(self, cfg, on_moved=None, clock=time.monotonic, offscreen=False):
        self.cfg = cfg
        self.on_moved = on_moved
        self.clock = clock
        self.offscreen = offscreen          # tests: place the window far off screen
        self.q = queue.Queue()
        self.thread = None
        self.ready = threading.Event()
        self.failed = False
        self.root = None
        # state (touched only on the Tk thread)
        self.visible = False
        self.orb = "idle"
        self.level = 0.0
        self.speaking = False
        self.move_mode = False
        self.user_line = ""
        self.reply = ""
        self.label = ""
        self.size = (400, 80)
        self.glow = self.ring = self.core = None
        self.hider = AutoHide(cfg.get("overlay_hide_s", 8), clock)
        self._anim = False
        self._drag = None

    # ---------- public, thread-safe ----------
    def start(self, timeout=5):
        if self.thread:
            return not self.failed
        self.thread = threading.Thread(target=self._run, name="overlay", daemon=True)
        self.thread.start()
        self.ready.wait(timeout)
        return not self.failed

    def stop(self):
        self.q.put(("quit",))

    def _put(self, *job):
        self.q.put(job)

    def call(self, fn):
        """Run fn() on the panel's Tk thread (used to open the settings window)."""
        self._put("call", fn)

    def status(self, state):
        self._put("status", state)

    def user_text(self, text):
        self._put("user", text)

    def reply_text(self, chunk, final=False):
        self._put("reply", chunk, final)

    def notice(self, msg):
        self._put("notice", msg)

    def show_full(self, text):
        self._put("full", text)

    def set_speaking(self, on):
        self._put("speaking", bool(on))

    def set_level(self, level):
        self.level = max(0.0, min(1.0, float(level)))   # plain float write; safe from any thread

    def set_move_mode(self, on):
        self._put("move", bool(on))

    def set_theme(self, name):
        self._put("theme", name)

    # ---------- Tk thread ----------
    def _run(self):
        try:
            import tkinter as tk
            self.root = tk.Tk()
            self.root.withdraw()
            self.root.overrideredirect(True)
            self.root.attributes("-topmost", True)
            self.canvas = tk.Canvas(self.root, highlightthickness=0, bd=0)
            self.canvas.pack(fill="both", expand=True)
            self.canvas.bind("<ButtonPress-1>", self._press)
            self.canvas.bind("<B1-Motion>", self._motion)
            self.canvas.bind("<ButtonRelease-1>", self._release)
            self._apply_theme()
        except Exception:
            log.exception("overlay could not start (no display?)")
            self.failed = True
            self.ready.set()
            return
        self.ready.set()
        self.root.after(30, self._poll)
        try:
            self.root.mainloop()
        except Exception:
            log.exception("overlay loop crashed")
        finally:
            try:
                self.root.destroy()
            except Exception:
                pass

    def _theme(self):
        return THEMES.get(self.cfg.get("theme"), THEMES["hud"])

    def _apply_theme(self):
        t = self._theme()
        self.canvas.configure(bg=t["bg"])
        try:
            self.root.attributes("-alpha", t["alpha"])
        except Exception:
            pass
        self._redraw()

    def _poll(self):
        if self.root is None:
            return
        try:
            while True:
                self._handle(self.q.get_nowait())
                if self.root is None:
                    return
        except queue.Empty:
            pass
        except Exception:
            log.exception("overlay job failed")
        if self.visible and self.hider.expired():
            self._hide()
        self.root.after(30 if self.visible else 60, self._poll)

    def _handle(self, job):
        kind = job[0]
        if kind == "quit":
            self.root.quit()
            self.root = None
        elif kind == "status":
            s = job[1]
            self.label = STATUS_LABEL.get(s, "")
            self.orb = "speaking" if self.speaking else STATUS_TO_ORB.get(s, "idle")
            self.hider.hold = s in ("listen", "ptt", "followup", "busy") or self.speaking
            if not self.hider.hold:
                self.hider.touch(len(self.reply))
            if s in STATUS_LABEL or self.visible:
                self._show()
        elif kind == "user":
            self.user_line, self.reply = job[1], ""
            self._show()
        elif kind == "reply":
            if job[1]:
                self.reply += job[1]
            if self.cfg.get("reply_mode", "both") != "spoken" or self.visible:
                self._show()
            if job[2]:
                self.hider.touch(len(self.reply))
        elif kind == "notice":
            self.user_line, self.reply = "", job[1]
            self._show()
            self.hider.touch(len(job[1]))
        elif kind == "full":
            self.reply = job[1]
            self.hider.hold = False
            self._show()
            self.hider.touch(len(job[1]))
        elif kind == "speaking":
            self.speaking = job[1]
            self.orb = "speaking" if job[1] else "idle"
            self.hider.hold = job[1]
            if not job[1]:
                self.hider.touch(len(self.reply))
            if self.visible:
                self._start_anim()
        elif kind == "move":
            self.move_mode = job[1]
            if job[1]:
                self._show()
                self.hider.hold = True
            else:
                self.hider.hold = False
                self.hider.touch(0)
            self._set_clickthrough()
        elif kind == "call":
            job[1]()
        elif kind == "theme":
            self.cfg["theme"] = job[1]
            self._apply_theme()
            self._place()

    def _text_block(self):
        parts = []
        if self.user_line:
            parts.append(("dim", "You: " + self.user_line))
        body = self.reply[-MAX_SHOWN:]
        if body:
            parts.append(("fg", body))
        elif self.label:
            parts.append(("dim", self.label))
        return parts

    def _redraw(self):
        """Redraw everything (only on content change, never per animation frame)."""
        c, t = self.canvas, self._theme()
        c.delete("all")
        w = t["width"]
        hud = self.cfg.get("theme", "hud") != "minimal"
        if hud:
            self.cx, self.cy, self.r = 38, 38, 20
            self.glow = c.create_oval(0, 0, 0, 0, outline=t["accent"], width=2)
            self.ring = c.create_oval(0, 0, 0, 0, outline=t["accent"], width=3)
            self.core = c.create_oval(0, 0, 0, 0, fill=t["accent"], outline="")
            x = 80
            self._orb_frame(0)
        else:
            self.glow = self.ring = None
            self.core = c.create_oval(14, 16, 24, 26, fill=t["accent"], outline="")
            x = 36
        y, bottom = 14, 0
        for kind, text in self._text_block():
            col = t["fg"] if kind == "fg" else t["dim"]
            it = c.create_text(x, y, text=text, anchor="nw", fill=col, width=w - x - 14,
                               font=("Segoe UI", 10 if kind == "dim" else 11))
            y = c.bbox(it)[3] + 6
            bottom = y
        h = max(bottom + 10, 80 if hud else 44)
        c.configure(width=w, height=h)
        self.size = (w, h)
        c.create_rectangle(1, 1, w - 1, h - 1, outline=t["dim"] if hud else "#3c4043", width=1)

    def _orb_frame(self, phase):
        if self.glow is None:
            return
        col = STATE_COLORS.get(self.orb, STATE_COLORS["idle"])
        if self.orb == "speaking":
            rr = self.r * (0.8 + 0.3 * self.level)
        elif self.orb == "listening":
            rr = self.r * (0.9 + 0.1 * math.sin(phase * 3))
        elif self.orb == "thinking":
            rr = self.r * (0.85 + 0.1 * math.sin(phase * 6))
        else:
            rr = self.r * 0.85
        cx, cy, c = self.cx, self.cy, self.canvas
        c.coords(self.ring, cx - rr, cy - rr, cx + rr, cy + rr)
        g = rr + 5 + (4 * self.level if self.orb == "speaking" else 2)
        c.coords(self.glow, cx - g, cy - g, cx + g, cy + g)
        s = rr * 0.35
        c.coords(self.core, cx - s, cy - s, cx + s, cy + s)
        c.itemconfig(self.glow, outline=col)
        c.itemconfig(self.ring, outline=col)
        c.itemconfig(self.core, fill=col)

    def _start_anim(self):
        if not self._anim and self.visible and self.orb != "idle" and self.glow is not None:
            self._anim = True
            self._tick()

    def _tick(self):
        if self.root is None or not (self.visible and self.orb != "idle"):
            self._anim = False
            if self.root is not None and self.visible and self.glow is not None:
                self._orb_frame(0)
            return
        self._orb_frame(self.clock())
        self.root.after(33, self._tick)

    def _pos(self):
        if self.offscreen:
            return -4000, -4000
        pos = self.cfg.get("overlay_pos")
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            return int(pos[0]), int(pos[1])
        return self.root.winfo_screenwidth() - self.size[0] - 24, 40

    def _place(self):
        x, y = self._pos()
        w, h = self.size
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _show(self):
        self._redraw()
        self._place()
        if not self.visible:
            self.visible = True
            self.root.deiconify()
            self.root.update_idletasks()
            self._set_clickthrough()
        if not self.hider.hold:
            self.hider.touch(len(self.reply))
        self.root.attributes("-topmost", True)
        self._start_anim()

    def _hide(self):
        self.visible = False
        self.hider.clear()
        if not self.move_mode:
            self.root.withdraw()

    def _set_clickthrough(self):
        try:
            import ctypes
            hwnd = self.root.winfo_id()
            top = ctypes.windll.user32.GetParent(hwnd) or hwnd
            _clickthrough(top, not self.move_mode)
        except Exception:
            log.debug("click-through unavailable", exc_info=True)

    # ---- dragging (move mode only; otherwise the window is click-through) ----
    def _press(self, e):
        if self.move_mode:
            self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def _motion(self, e):
        if self.move_mode and self._drag:
            self.root.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def _release(self, e):
        if self.move_mode and self._drag:
            self._drag = None
            x, y = self.root.winfo_x(), self.root.winfo_y()
            self.cfg["overlay_pos"] = [x, y]
            if self.on_moved:
                try:
                    self.on_moved(x, y)
                except Exception:
                    log.exception("on_moved failed")
