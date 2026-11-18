from jarvis.core.ui.app import FrontEnd, weather_line


class Rec:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a: self.calls.append((name, a))


def test_front_end_forwards_status_to_panel_and_tray():
    ov, tr = Rec(), Rec()
    ui = FrontEnd(ov, tr)
    ui.status("listen")
    ui.reply_text("hi", True)
    assert ("status", ("listen",)) in ov.calls and ("set_state", ("listen",)) in tr.calls
    assert ("reply_text", ("hi", True)) in ov.calls


def test_mic_missing_notice_warns_in_tray():
    ov, tr = Rec(), Rec()
    FrontEnd(ov, tr).notice("I can't find the microphone selected in settings.")
    assert tr.calls and tr.calls[0][0] == "warn"
    tr2 = Rec()
    FrontEnd(Rec(), tr2).notice("Timer done.")
    assert not tr2.calls


def test_weather_line():
    w = {"place": "Leeds, United Kingdom", "now": {"temperature_c": 7.4, "conditions": "Light rain"},
         "forecast": [{"high_c": 9.6, "rain_chance_percent": 80}]}
    line = weather_line(lambda c: w, "Leeds")
    assert line == "In Leeds it's 7 degrees and light rain with a high of 10 and a 80 percent chance of rain."
    assert weather_line(lambda c: {"error": "x"}, "Leeds") == ""


# ---------- Quit / shutdown ----------
import threading
import time

from jarvis.core.ui.app import App


class _Part:
    def __init__(self, hang=False):
        self.hang, self.called, self.thread = hang, False, None

    def _do(self):
        self.called = True
        if self.hang:
            time.sleep(30)
    stop = close = _do


def _app(va):
    app = App.__new__(App)
    app.va, app.brain, app.tray, app.overlay = va, _Part(), _Part(), _Part()
    app.done = threading.Event()
    app.exits = []
    app._exit = app.exits.append
    app.STEP_TIMEOUT = 0.2
    return app


def test_shutdown_exits_even_if_a_part_hangs(caplog, monkeypatch):
    va = _Part(hang=True)
    app = _app(va)
    monkeypatch.setattr(App, "_step", lambda self, n, fn, timeout=None: _orig_step(self, n, fn, 0.2))
    t0 = time.monotonic()
    app._shutdown()
    assert time.monotonic() - t0 < 2
    assert app.exits == [0]
    assert app.brain.called and app.tray.called and app.overlay.called
    assert "shutdown step 'voice' stuck" in caplog.text


_orig_step = App._step


def test_shutdown_clean_path_exits():
    app = _app(_Part())
    app._shutdown()
    assert app.exits == [0] and app.va.called


def test_quit_watchdog_forces_exit_when_main_thread_is_stuck(monkeypatch):
    monkeypatch.setattr(App, "WATCHDOG_S", 0.1)
    app = _app(None)
    app.quit()                       # nobody runs _shutdown: simulate a stuck main thread
    deadline = time.monotonic() + 2
    while not app.exits and time.monotonic() < deadline:
        time.sleep(0.02)
    assert app.exits == [0] and app.done.is_set()
