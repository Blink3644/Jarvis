"""Stage A5: Google Calendar and ClickUp tools, with fake HTTP (no network)."""
import datetime as dt
import json

from jarvis.core import config, greeting
from jarvis.core.registry import Registry, Tool
from jarvis.core.ui import settings_window as sw
from jarvis.pc import clickup as cu
from jarvis.pc import google_calendar as gc

TZ = dt.timezone(dt.timedelta(hours=1))
NOW = dt.datetime(2026, 11, 18, 9, 30, tzinfo=TZ)


class Resp:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def json(self):
        return self.data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeHttp:
    def __init__(self, routes):
        self.routes = routes          # (method, url-suffix) -> data | Resp
        self.calls = []

    def _hit(self, method, url, **kw):
        self.calls.append((method, url, kw))
        for (m, suffix), data in self.routes.items():
            if m == method and url.endswith(suffix):
                return data if isinstance(data, Resp) else Resp(data)
        return Resp({}, 404)

    def get(self, url, **kw):
        return self._hit("GET", url, **kw)

    def post(self, url, **kw):
        return self._hit("POST", url, **kw)

    def request(self, method, url, **kw):
        return self._hit(method, url, **kw)


# ---------------- registry: hidden tools ----------------
def test_unavailable_tools_are_hidden_and_refused():
    reg = Registry()
    on = {"v": False}
    reg.register(Tool(name="t", description="d", handler=lambda: {"ok": 1}, available=lambda: on["v"]))
    reg.register(Tool(name="always", description="d", handler=lambda: {}))
    names = {d["name"] for d in reg.declarations()}
    assert "always" in names and "t" not in names
    assert "error" in reg.call("t", {})
    on["v"] = True
    assert "t" in {d["name"] for d in reg.declarations()}
    assert reg.call("t", {}) == {"ok": 1}


def test_tools_hidden_by_default():
    cfg = dict(config.DEFAULTS)
    reg = Registry()
    gc.register(reg, cfg)
    cu.register(reg, cfg)
    names = {d["name"] for d in reg.declarations()}
    assert not names & {"get_calendar_events", "add_calendar_event", "list_clickup_tasks", "create_clickup_task"}
    assert gc.brief_line(cfg) == "" and cu.brief_line(cfg) == ""


# ---------------- Google Calendar ----------------
def test_day_range_and_event_body():
    tmin, tmax = gc.day_range(NOW, 2)
    assert tmin == "2026-11-18T00:00:00+01:00" and tmax == "2026-11-20T00:00:00+01:00"
    b = gc.event_body("Dentist", gc.parse_start("2026-11-19T15:00+01:00"), 30)
    assert b["start"]["dateTime"] == "2026-11-19T15:00:00+01:00"
    assert b["end"]["dateTime"] == "2026-11-19T15:30:00+01:00"
    b = gc.event_body("Holiday", gc.parse_start("2026-11-20"))
    assert b["start"] == {"date": "2026-11-20"} and b["end"] == {"date": "2026-11-21"}


def test_calendar_list_add_and_brief():
    items = [{"summary": "Standup", "start": {"dateTime": "2026-11-18T10:00:00+01:00"},
              "end": {"dateTime": "2026-11-18T10:15:00+01:00"}},
             {"summary": "Birthday", "start": {"date": "2026-11-18"}, "end": {"date": "2026-11-19"}},
             {"summary": "Gone", "status": "cancelled", "start": {"date": "2026-11-18"}}]
    http = FakeHttp({("GET", "/events"): {"items": items},
                     ("POST", "/events"): {"summary": "Dentist",
                                           "start": {"dateTime": "2026-11-19T15:00:00+01:00"}}})
    get, add, brief = gc.make_tools({}, http=http, token_fn=lambda: "tok", clock=lambda: NOW)
    out = get(days=1)
    assert out["count"] == 2 and out["events"][1]["all_day"]
    m, url, kw = http.calls[0]
    assert kw["headers"]["Authorization"] == "Bearer tok"
    assert kw["params"]["singleEvents"] == "true" and kw["params"]["orderBy"] == "startTime"
    assert add("Dentist", "2026-11-19T15:00")["added"]["title"] == "Dentist"
    assert http.calls[-1][2]["json"]["summary"] == "Dentist"
    assert add("", "2026-11-19")["error"] and add("X", "next tuesday")["error"]
    assert brief().startswith("You have 2 events today: Standup at")


def test_calendar_not_connected_is_friendly():
    def no_token():
        raise gc.NotConnected()
    get, _, brief = gc.make_tools({}, http=FakeHttp({}), token_fn=no_token, clock=lambda: NOW)
    assert "isn't connected" in get()["error"]
    assert brief() == ""


def test_import_credentials(tmp_path):
    bad = tmp_path / "web.json"
    bad.write_text(json.dumps({"web": {}}))
    assert "Desktop app" in gc.import_credentials(bad)
    good = tmp_path / "c.json"
    good.write_text(json.dumps({"installed": {"client_id": "x"}}))
    assert gc.import_credentials(good) == "" and gc.has_credentials()
    assert not gc.is_connected()
    gc.set_refresh_token("r1")
    assert gc.is_connected() and gc.available({"calendar_enabled": True})
    assert not gc.available({"calendar_enabled": False})
    gc.disconnect()
    assert not gc.is_connected()


def test_calendar_add_needs_confirmation():
    reg = Registry()
    gc.register(reg, {"calendar_enabled": True})
    out = reg.call("add_calendar_event", {"title": "Dentist", "start": "2026-11-19T15:00"})
    assert "error" in out        # not connected -> tool is hidden
    reg.tools["add_calendar_event"].available = lambda: True
    out = reg.call("add_calendar_event", {"title": "Dentist", "start": "2026-11-19T15:00"})
    assert out["status"] == "needs_confirmation" and "Dentist" in out["instruction"]


# ---------------- ClickUp ----------------
TASKS = [{"name": "Write report", "status": {"status": "to do"}, "list": {"name": "Work"},
          "due_date": str(int(NOW.timestamp() * 1000))},
         {"name": "Buy milk", "status": {"status": "open"}, "list": {"name": "Home"}, "due_date": None}]


def cu_http():
    return FakeHttp({("GET", "/user"): {"user": {"id": 42, "username": "me"}},
                     ("GET", "/team"): {"teams": [{"id": 9, "name": "Mine"}]},
                     ("GET", "/team/9/space"): {"spaces": [{"id": "s1", "name": "Space"}]},
                     ("GET", "/space/s1/folder"): {"folders": [{"name": "F", "lists": [{"id": 1, "name": "Work"}]}]},
                     ("GET", "/space/s1/list"): {"lists": [{"id": 2, "name": "Home"}]},
                     ("GET", "/team/9/task"): {"tasks": TASKS},
                     ("POST", "/task"): {"id": "t1"}})


def test_clickup_client_lists_and_teams():
    c = cu.Client("pk_x", http=cu_http())
    assert c.teams() == [{"id": "9", "name": "Mine"}]
    lists = c.lists("9")
    assert [li["name"] for li in lists] == ["Work", "Home"] and lists[0]["where"] == "Space / F"
    assert cu.find_list(lists, "home")["id"] == "2"
    assert cu.find_list(lists, "wrk")["id"] == "1"
    assert cu.find_list(lists, "zzzzzz") is None


def test_clickup_tasks_create_and_brief():
    http = cu_http()
    cfg = {"clickup_team_id": "9", "clickup_list_id": "1", "clickup_list_name": "Work"}
    lst, create, brief = cu.make_tools(cfg, client_fn=lambda: cu.Client("pk_x", http=http), clock=lambda: NOW)
    out = lst(due="today")
    assert out["count"] == 2 and out["tasks"][0]["status"] == "to do" and "due" in out["tasks"][0]
    params = [c for c in http.calls if c[1].endswith("/team/9/task")][0][2]["params"]
    assert params["assignees[]"] == "42" and params["order_by"] == "due_date" and "due_date_lt" in params
    http.calls.clear()
    assert create("Call mum", due="2026-11-19") == {"created": "Call mum", "list": "Work"}
    m, url, kw = http.calls[-1]
    assert url.endswith("/list/1/task") and kw["json"]["due_date_time"] is False
    assert create("Fix bike", list_name="home")["list"] == "Home"
    assert http.calls[-1][1].endswith("/list/2/task")
    assert "error" in create("")
    assert brief().startswith("In ClickUp you have 2 tasks due today")


def test_clickup_bad_token_is_friendly():
    http = FakeHttp({("GET", "/user"): Resp({}, 401)})
    lst, _, _ = cu.make_tools({"clickup_team_id": "9"}, client_fn=lambda: cu.Client("pk_bad", http=http))
    assert "didn't accept the token" in lst()["error"]


def test_clickup_availability_and_token():
    assert cu.looks_like_token("pk_123_ABCDEFG") and not cu.looks_like_token("abc")
    cfg = {"clickup_enabled": True, "clickup_team_id": "9"}
    assert not cu.available(cfg)
    cu.set_token("pk_123_ABCDEFG")
    assert cu.available(cfg) and not cu.available({**cfg, "clickup_enabled": False})
    cu.disconnect()
    assert not cu.available(cfg)


# ---------------- settings + brief ----------------
def test_build_cfg_integrations():
    cfg, errs = sw.build_cfg(config.DEFAULTS, {"calendar_enabled": 1, "clickup_enabled": True,
                                               "clickup_team_id": " 9 ", "clickup_list_id": "1",
                                               "clickup_list_name": "Work"})
    assert not errs and cfg["calendar_enabled"] is True and cfg["clickup_team_id"] == "9"
    assert config.DEFAULTS["calendar_enabled"] is False and config.DEFAULTS["clickup_enabled"] is False


def test_daily_brief_includes_extras():
    cfg = {"startup_greeting": "brief", "personality": "butler"}
    text = greeting.startup_text(cfg, NOW, None, [lambda: "You have 1 event today: X at 10:00.", lambda: ""])
    assert text == "Good morning, sir. You have 1 event today: X at 10:00."
