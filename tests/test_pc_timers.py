import datetime

import pytest

from jarvis.core.registry import Registry
from jarvis.pc import timers


class Clock:
    def __init__(self, dt):
        self.t = dt.timestamp()

    def __call__(self):
        return self.t


@pytest.fixture
def env():
    reg = Registry()
    said = []
    reg.announce = said.append
    clock = Clock(datetime.datetime(2026, 11, 17, 18, 0, 0))
    mgr = timers.register(reg, {}, clock=clock, use_thread=False)
    return reg, mgr, clock, said


def test_timer_fires_with_label(env):
    reg, mgr, clock, said = env
    r = reg.call("set_timer", {"minutes": 10, "label": "pasta"})
    assert r["duration"] == "10 minutes"
    clock.t += 599
    assert mgr.check() == 0
    clock.t += 2
    assert mgr.check() == 1
    assert said == ["Your 10 minute pasta timer is done."]
    assert reg.call("list_timers", {})["count"] == 0


def test_timer_no_label_and_validation(env):
    reg, mgr, clock, said = env
    assert "error" in reg.call("set_timer", {})
    reg.call("set_timer", {"seconds": 30})
    clock.t += 31
    mgr.check()
    assert said == ["Your 30 second timer is done."]
    assert "sir" not in said[0].lower()


def test_alarm_next_occurrence(env):
    reg, mgr, clock, said = env
    r = reg.call("set_alarm", {"time": "7:30 pm"})
    assert r["at"] == "19:30" and r["day"] == "today"
    r = reg.call("set_alarm", {"time": "06:00"})
    assert r["at"] == "06:00" and r["day"] == "tomorrow"
    assert "error" in reg.call("set_alarm", {"time": "banana"})
    clock.t += 90 * 60 + 1
    assert mgr.check() == 1
    assert "19:30" in said[0]


def test_list_and_cancel(env):
    reg, mgr, clock, said = env
    reg.call("set_timer", {"minutes": 5, "label": "tea"})
    reg.call("set_timer", {"minutes": 9, "label": "laundry"})
    lst = reg.call("list_timers", {})["timers"]
    assert [t["label"] for t in lst] == ["tea", "laundry"]
    assert reg.call("cancel_timer", {"which": "laundry"})["cancelled"][0]["label"] == "laundry"
    assert "error" in reg.call("cancel_timer", {"which": "nothing"})
    assert len(reg.call("cancel_timer", {"which": "all"})["cancelled"]) == 1
    clock.t += 1000
    assert mgr.check() == 0


def test_cancel_by_id(env):
    reg, mgr, clock, said = env
    i = reg.call("set_timer", {"seconds": 60})["id"]
    assert reg.call("cancel_timer", {"which": str(i)})["cancelled"]


def test_parse_clock():
    assert timers.parse_clock("12 am") == (0, 0)
    assert timers.parse_clock("12:15 PM") == (12, 15)
    assert timers.parse_clock("7pm") == (19, 0)
    assert timers.parse_clock("25:00") is None


def test_thread_fires_real_short_timer():
    reg = Registry()
    said = []
    reg.announce = said.append
    mgr = timers.register(reg, {})
    reg.call("set_timer", {"seconds": 0.2, "label": "x"})
    import time
    for _ in range(40):
        if said:
            break
        time.sleep(0.05)
    mgr.shutdown()
    assert said and "timer is done" in said[0]
