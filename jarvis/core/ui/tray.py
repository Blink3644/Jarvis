"""System tray icon (pystray). Runs in its own thread; all methods are thread-safe."""
import logging
import threading

log = logging.getLogger(__name__)

# status() state from the voice loop -> tray icon state
STATE_MAP = {"idle": "idle", "listen": "listening", "ptt": "listening", "followup": "listening",
             "followup_heard": "thinking", "busy": "thinking", "speaking": "speaking",
             "mic_lost": "warning"}
COLORS = {"idle": (80, 170, 255), "listening": (60, 235, 170), "thinking": (255, 190, 60),
          "speaking": (120, 220, 255), "muted": (130, 130, 130), "warning": (255, 80, 80)}
STATES = tuple(COLORS)

_cache = {}


def icon_state(status):
    return STATE_MAP.get(status, "idle")


def make_icon(state, size=64):
    """Glowing ring drawn with Pillow; cached per state."""
    key = (state, size)
    if key in _cache:
        return _cache[key]
    from PIL import Image, ImageDraw, ImageFilter
    r, g, b = COLORS.get(state, COLORS["idle"])
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(glow)
    m = size * 0.16
    d.ellipse([m, m, size - m, size - m], outline=(r, g, b, 200), width=max(2, size // 8))
    glow = glow.filter(ImageFilter.GaussianBlur(size / 20))
    d = ImageDraw.Draw(glow)
    d.ellipse([m, m, size - m, size - m], outline=(r, g, b, 255), width=max(2, size // 12))
    c = size / 2
    rad = size * (0.10 if state != "speaking" else 0.16)
    d.ellipse([c - rad, c - rad, c + rad, c + rad], fill=(r, g, b, 255))
    if state == "muted":
        d.line([m, size - m, size - m, m], fill=(255, 255, 255, 255), width=max(2, size // 12))
    if state == "warning":
        d.rectangle([c - 2, c - size * 0.2, c + 2, c + size * 0.05], fill=(255, 255, 255, 255))
    _cache[key] = glow
    return glow


class Tray:
    def __init__(self, on_open_settings, on_toggle_mute, on_quit):
        self.on_open_settings = on_open_settings
        self.on_toggle_mute = on_toggle_mute
        self.on_quit = on_quit
        self.muted = False
        self.state = "idle"
        self._icon = None
        self._lock = threading.Lock()

    @staticmethod
    def _tip():
        from jarvis import __version__
        return f"Jarvis {__version__}"

    def _menu(self):
        import pystray
        from jarvis import __version__
        return pystray.Menu(
            pystray.MenuItem(f"Jarvis {__version__}", None, enabled=False),
            pystray.MenuItem("Open settings", lambda i, it: self.on_open_settings(), default=True),
            pystray.MenuItem("Mute mic", self._toggle_mute, checked=lambda it: self.muted),
            pystray.MenuItem("Quit", lambda i, it: self._quit()))

    def _toggle_mute(self, icon=None, item=None):
        self.set_muted(not self.muted)
        try:
            self.on_toggle_mute(self.muted)
        except TypeError:
            self.on_toggle_mute()

    def _quit(self):
        try:
            self.on_quit()
        finally:
            self.stop()

    def start(self):
        """Create the icon and run it in a background thread."""
        import pystray
        with self._lock:
            if self._icon:
                return
            self._icon = pystray.Icon("Jarvis", make_icon(self._shown()), self._tip(), self._menu())
        threading.Thread(target=self._icon.run, name="tray", daemon=True).start()

    def _shown(self):
        return "muted" if self.muted else self.state

    def _refresh(self):
        ic = self._icon
        if ic:
            try:
                ic.icon = make_icon(self._shown())
                ic.update_menu()
            except Exception:
                log.exception("tray refresh failed")

    def set_state(self, state):
        """Accepts a tray state or a voice-loop status string."""
        self.state = state if state in COLORS else icon_state(state)
        self._refresh()

    def set_muted(self, muted):
        self.muted = bool(muted)
        self._refresh()

    def warn(self, msg):
        ic = self._icon
        if ic:
            try:
                ic.notify(msg, "Jarvis")
            except Exception:
                log.exception("tray notify failed")

    def stop(self):
        ic, self._icon = self._icon, None
        if ic:
            try:
                ic.stop()
            except Exception:
                pass
