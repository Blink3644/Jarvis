"""The windowed front end (Stage A4): tray icon + on-screen panel + settings window.

Threads: the panel owns the only Tk thread; the settings window is created on it via
overlay.call(). The tray runs on its own pystray thread. Voice runs on its own threads.
"""
import datetime
import importlib
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
        self.update_info = None

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
        threading.Thread(target=self._startup_update_check, name="update-check", daemon=True).start()
        from jarvis.core.secrets import get_api_key, get_provider_key, looks_like_key
        others = [p for p in self.cfg.get("providers") or [] if p.get("kind") != "gemini"
                  and p.get("enabled", True) and (p.get("preset") == "ollama" or get_provider_key(p["id"]))]
        if not looks_like_key(get_api_key() or "") and not others:
            self.ui.notice("I need an AI provider key (for example Gemini). Opening settings.")
            self.open_settings()
        self.done.wait()
        self._shutdown()

    STEP_TIMEOUT = 1.5          # seconds each shutdown step may take before we move on
    WATCHDOG_S = 6.0            # after Quit, the process ends by this time no matter what

    def quit(self):
        log.info("Quit requested")
        if not self.done.is_set():
            # If the main thread is stuck somewhere (e.g. still starting voice), still exit.
            t = threading.Timer(self.WATCHDOG_S, self._force_exit, args=("watchdog",))
            t.daemon = True
            t.start()
        self.done.set()

    def _step(self, name, fn, timeout=None):
        """Run fn in a helper thread; if it hangs, log it and carry on."""
        err = []

        def run():
            try:
                fn()
            except Exception as e:
                err.append(e)
                log.exception("shutdown step '%s' failed", name)
        t = threading.Thread(target=run, name=f"shutdown-{name}", daemon=True)
        t.start()
        t.join(self.STEP_TIMEOUT if timeout is None else timeout)
        if t.is_alive():
            log.warning("shutdown step '%s' stuck; skipping it", name)
            return False
        return not err

    def _shutdown(self):
        log.info("shutting down")
        if self.va:
            self._step("voice", self.va.stop, timeout=2.5)
        self._step("brain", self.brain.close)
        self._step("tray", self.tray.stop)

        def overlay():
            self.overlay.stop()
            if self.overlay.thread:
                self.overlay.thread.join(self.STEP_TIMEOUT)
                if self.overlay.thread.is_alive():
                    raise TimeoutError("panel thread did not end")
        self._step("panel", overlay, timeout=self.STEP_TIMEOUT + 0.5)
        self._force_exit("normal")

    def _force_exit(self, why):
        """End the process now. Audio drivers, Tk on a side thread and native libraries can
        hang Python's normal exit (atexit/thread joins), so skip it once our own cleanup ran."""
        log.info("exiting (%s)", why)
        alive = [t.name for t in threading.enumerate()
                 if t is not threading.current_thread() and t.is_alive() and not t.daemon]
        if alive:
            log.info("threads still running at exit: %s", ", ".join(alive))
        logging.shutdown()
        self._exit(0)

    @staticmethod
    def _exit(code):
        import os
        os._exit(code)

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
            extras = []
            for mod in ("google_calendar", "clickup"):      # optional; '' when not set up
                try:
                    m = importlib.import_module(f"jarvis.pc.{mod}")
                    extras.append(lambda m=m: m.brief_line(self.cfg))
                except Exception:
                    log.exception("brief source %s unavailable", mod)
            text = greeting.startup_text(self.cfg, datetime.datetime.now(), weather_fn, extras)
        except Exception:
            log.exception("greeting failed")
            return
        if not text:
            return
        if self.cfg.get("reply_mode", "both") != "spoken" or not self.va:
            self.ui.notice(text)
        if self.va and self.cfg.get("reply_mode", "both") != "text":
            self.va.speaker.say(text)

    def _startup_update_check(self):
        """At most once a day: look for a newer release and mention it. Never installs anything."""
        from jarvis.core import updater
        try:
            if not updater.due_today() or updater.why_cant_update():
                return
            info = updater.check()
        except Exception as e:
            log.info("startup update check skipped: %s", e)
            return
        if info:
            self.update_info = info
            msg = f"Jarvis version {info.version} is available. Open settings, About tab, to update."
            self.ui.notice(msg)
            self.tray.warn(msg)

    def install_update(self, folder):
        from jarvis.core import updater
        try:
            updater.launch_installer(folder)
        except Exception:
            log.exception("could not start the updater")
            self.ui.notice("I couldn't start the update. Details are in the log.")
            return
        self.quit()

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

    def _swap_wake(self):
        """Load the newly chosen wake word (a few seconds) and hand it to the voice loop."""
        from jarvis.core.voice import wakewords
        try:
            wake, fell_back = wakewords.load(self.cfg.get("wake_model"))
        except Exception:
            log.exception("could not load the wake word")
            return
        self.va.wake = wake
        self.va.wake_reset_pending = True
        name = wakewords.nice_name(wakewords.DEFAULT if fell_back else self.cfg.get("wake_model") or "")
        if fell_back:
            self.va.ui.notice("That wake word couldn't be loaded, so I'm using \"Hey Jarvis\".")
        else:
            self.va.ui.notice(f'Wake word is now "{name}".')

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
                old_wake = app.cfg.get("wake_model")
                app.cfg.clear()
                app.cfg.update(cfg)          # brain, voice and tools share this dict
                app.overlay.set_theme(cfg.get("theme", "hud"))
                app._sync_autostart()
                if app.va and cfg.get("wake_model") != old_wake:
                    threading.Thread(target=app._swap_wake, daemon=True).start()

            def clear_history(self):
                from jarvis.core.history import HistoryStore
                HistoryStore(app.cfg).clear_all()

            def check_usage(self):
                return app.brain.usage.summary()

            def update_info(self):
                return app.update_info

            def install_update(self, folder):
                app.install_update(folder)

            def tool_names(self):
                return [d["name"] for d in app.registry.declarations()]

        if self.va:
            from jarvis.core.voice.mic import level
            self._level = 0.0

            def tap(frame):
                self._level = level(frame)
            self.va.tap = tap
        return Hooks()
