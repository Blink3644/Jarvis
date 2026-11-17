"""The windowed front end (Stage A4): tray icon + on-screen panel + settings window.

Threads: the panel owns the only Tk thread; the settings window is created on it via
overlay.call(). The tray runs on its own pystray thread. Voice runs on its own threads.
"""
import datetime
import logging
import threading

from .. import autostart, config, greeting, instance

log = logging.getLogger(__name__)


class FrontEnd:
    """What the voice loop and the brain talk to: forwards to the panel and the tray icon."""

    def __init__(self, overlay, tray):
        self.overlay, self.tray = overlay, tray

    def status(self, state):
        self.overlay.status(state)
        self.tray.set_state(state)

    def user_text(self, text):
        self.overlay.user_text(text)

    def reply_text(self, chunk, final=False):
        self.overlay.reply_text(chunk, final)

    def notice(self, msg):
        self.overlay.notice(msg)
        if "microphone" in msg.lower() and ("can't find" in msg.lower() or "isn't connected" in msg.lower()):
            self.tray.warn(msg)


def weather_line(get_weather, city):
    """One short sentence for the daily brief, from weather.get_weather's dict."""
    w = get_weather(city)
    if not w or "error" in w:
        return ""
    now = w.get("now", {})
    day = (w.get("forecast") or [{}])[0]
    t = now.get("temperature_c")
    bits = [f"In {w.get('place', city).split(',')[0]} it's {round(t)} degrees and {now.get('conditions', '').lower()}"
            if t is not None else ""]
    if day.get("high_c") is not None:
        bits.append(f"with a high of {round(day['high_c'])}")
    if day.get("rain_chance_percent"):
        bits.append(f"and a {day['rain_chance_percent']} percent chance of rain")
    return (" ".join(b for b in bits if b) + ".") if bits[0] else ""


def test_key(cfg, key):
    """(ok, message) without using generation quota: just look up the backup model."""
    from google import genai
    from google.genai import types
    from ..brain import classify
    try:
        client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=15_000))
        client.models.get(model=cfg["model_fallback"])   # keep `client` alive or the SDK closes it
        return True, "Google accepted it."
    except Exception as e:
        kind = classify(e)
        log.info("key test failed: %s", kind)
        return False, {"bad_key": "Google rejected this key.",
                       "offline": "I can't reach the internet.",
                       "model_missing": "The key works, but the backup model name is wrong."}.get(
                           kind, "Something went wrong; details are in the log.")


class App:
    def __init__(self, cfg, brain, registry, start_voice):
        from .overlay import Overlay
        from .tray import Tray
        self.cfg, self.brain, self.registry = cfg, brain, registry
        self.va = None
        self.settings = None
        self.done = threading.Event()
        self.overlay = Overlay(cfg, on_moved=self._moved)
        self.tray = Tray(self.open_settings, self._mute, self.quit)
        self.ui = FrontEnd(self.overlay, self.tray)
        self._start_voice = start_voice

    # ---------- lifecycle ----------
    def run(self):
        if not self.overlay.start():
            log.error("on-screen panel could not start (no Tk?)")
        self.tray.start()
        instance.listen_for_second_launch(self.open_settings)
        self._sync_autostart()
        try:
            self.va = self._start_voice(self.cfg, self.brain, self.ui)
        except Exception:
            log.exception("voice failed to start")
            self.ui.notice("Voice couldn't start. Details are in the log.")
            self.tray.warn("Voice couldn't start. Details are in the log.")
        if self.va:
            self._chain_speaking()
        self._greet()
        from jarvis.core.secrets import get_api_key, looks_like_key
        if not looks_like_key(get_api_key() or ""):
            self.ui.notice("I need a Gemini API key. Opening settings.")
            self.open_settings()
        self.done.wait()
        self._shutdown()

    def quit(self):
        self.done.set()

    def _shutdown(self):
        try:
            if self.va:
                self.va.stop()
        finally:
            self.brain.close()
            self.tray.stop()
            self.overlay.stop()

    # ---------- wiring ----------
    def _chain_speaking(self):
        sp = self.va.speaker
        inner = sp.on_speaking

        def on_speaking(on):
            if inner:
                inner(on)
            self.overlay.set_speaking(on)
            if on:
                self.tray.set_state("speaking")
        sp.on_speaking = on_speaking

    def _greet(self):
        def weather_fn(city):
            from jarvis.pc.weather import make_get_weather
            return weather_line(make_get_weather(self.cfg), city)
        try:
            text = greeting.startup_text(self.cfg, datetime.datetime.now(), weather_fn)
        except Exception:
            log.exception("greeting failed")
            return
        if not text:
            return
        if self.cfg.get("reply_mode", "both") != "spoken" or not self.va:
            self.ui.notice(text)
        if self.va and self.cfg.get("reply_mode", "both") != "text":
            self.va.speaker.say(text)

    def _sync_autostart(self):
        try:
            want = bool(self.cfg.get("start_with_windows"))
            if want and autostart.current() != autostart.default_command():
                autostart.enable()          # also repairs an old or moved command
            elif not want and autostart.is_enabled():
                autostart.disable()
        except Exception:
            log.exception("start-with-Windows setting failed")

    def _moved(self, x, y):
        self.cfg["overlay_pos"] = [x, y]
        config.save(self.cfg)

    def _mute(self, muted=None):
        if self.va:
            self.va.muted = bool(muted)
        self.tray.set_muted(bool(muted))
        self.ui.notice("Microphone muted." if muted else "Microphone on. I'm listening again.")

    # ---------- settings window ----------
    def open_settings(self):
        self.overlay.call(self._open_settings_on_tk)

    def _open_settings_on_tk(self):
        from .settings_window import SettingsWindow
        if self.settings is not None:
            try:
                if self.settings.win.winfo_exists():
                    self.settings.show()
                    return
            except Exception:
                pass
        self.settings = SettingsWindow(self.overlay.root, self.cfg, self._hooks())
        self.settings.show()

    def _hooks(self):
        app = self

        class Hooks:
            def test_key(self, key):
                return test_key(app.cfg, key)

            def test_wake(self):
                if not app.va:
                    return app.settings.wake_result("Voice isn't running.")
                app.va.test_wake(10)

                def report():
                    best = app.va.wake_test_best
                    need = app.va.wake_threshold()
                    app.settings.wake_result(
                        f"Best score {best:.2f} (needs {need:.2f}). " +
                        ("Good." if best >= need else "Too low: raise the sensitivity or move closer."))
                threading.Timer(10.5, report).start()

            def mic_level(self):
                return getattr(app, "_level", 0.0)

            def reopen_mic(self):
                if app.va:
                    app.va.events.put("reopen_mic")

            def apply(self, cfg):
                app.cfg.clear()
                app.cfg.update(cfg)          # brain, voice and tools share this dict
                app.overlay.set_theme(cfg.get("theme", "hud"))
                app._sync_autostart()

            def clear_history(self):
                from jarvis.core.history import HistoryStore
                HistoryStore(app.cfg).clear_all()

            def check_usage(self):
                return app.brain.usage.summary()

            def tool_names(self):
                return [d["name"] for d in app.registry.declarations()]

        if self.va:
            from jarvis.core.voice.mic import level
            self._level = 0.0

            def tap(frame):
                self._level = level(frame)
            self.va.tap = tap
        return Hooks()
