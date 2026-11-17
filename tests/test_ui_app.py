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
