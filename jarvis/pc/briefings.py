"""Proactive briefings: Jarvis speaks first. Both parts are off by default.

- Calendar heads-up (cfg["calendar_headsup"]): says 'Heads up: "X" starts in 10 minutes.'
  Event titles are data only: they are spoken and shown, never sent to the AI or acted on.
- Morning brief (cfg["morning_brief"] at cfg["morning_brief_time"]): the same text as the
  'Daily brief' startup greeting, once a day.
Timed reminders live in timers.py. This module adds no AI tools.
"""
import datetime as dt
import json
import logging
import threading
import time

from jarvis.core import greeting, paths
from jarvis.core.registry import Registry
from jarvis.pc import google_calendar as gcal
from jarvis.pc.timers import parse_clock

log = logging.getLogger(__name__)

TICK_S = 30                 # how often the loop wakes
CALENDAR_REFRESH_S = 300    # how often Google Calendar is asked (when heads-up is on)
BRIEF_WINDOW_MIN = 60       # if Jarvis starts up to an hour after the brief time, still give it


def _default_brief(cfg, now):
    try:
        from jarvis.core.ui.app import brief_sources
        weather_fn, extras = brief_sources(cfg)
    except Exception:
        log.exception("brief sources unavailable")
        weather_fn, extras = None, ()
    return greeting.daily_brief(cfg, now, weather_fn, extras)


class Briefings:
    def __init__(self, cfg, announce, now_fn=None, fetch_events=None, brief_fn=None, state_path=None):
        self.cfg = cfg
        self.announce = announce
        self.now_fn = now_fn or (lambda: dt.datetime.now().astimezone())
        self.fetch_events = fetch_events or (lambda now: gcal.upcoming(now))
        self.brief_fn = brief_fn or _default_brief
        self.state_path = state_path or paths.data_dir() / "briefings.json"
        self.events: list[dict] = []
        self.fetched_at = 0.0
        self.seen: set = set()
        self._stop = threading.Event()

    # ----- state (last morning brief date) -----
    def _last_brief(self) -> str:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8")).get("last_brief", "")
        except Exception:
            return ""

    def _set_last_brief(self, day: str):
        try:
            self.state_path.write_text(json.dumps({"last_brief": day}), encoding="utf-8")
        except Exception:
            log.exception("could not save briefings state")

    # ----- one step of the loop (tests call this directly) -----
    def tick(self) -> list[str]:
        said = []
        now = self.now_fn()
        for part in (self._morning, self._headsup):
            try:
                said += part(now)
            except Exception:
                log.exception("briefing step failed")
        for text in said:
            try:
                self.announce(text)
            except Exception:
                log.exception("announce failed")
        return said

    def _morning(self, now) -> list[str]:
        if not self.cfg.get("morning_brief"):
            return []
        hm = parse_clock(self.cfg.get("morning_brief_time") or "")
        if not hm:
            return []
        target = now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
        today = now.date().isoformat()
        if not target <= now < target + dt.timedelta(minutes=BRIEF_WINDOW_MIN) or self._last_brief() == today:
            return []
        self._set_last_brief(today)
        text = self.brief_fn(self.cfg, now.replace(tzinfo=None))
        return [text] if text else []

    def _headsup(self, now) -> list[str]:
        if not self.cfg.get("calendar_headsup") or not gcal.available(self.cfg):
            return []
        if time.monotonic() - self.fetched_at >= CALENDAR_REFRESH_S or not self.fetched_at:
            self.fetched_at = time.monotonic()
            try:
                self.events = self.fetch_events(now)
            except Exception as e:
                log.info("calendar heads-up fetch skipped: %s", type(e).__name__)
        try:
            lead = max(1, min(120, int(self.cfg.get("calendar_headsup_minutes", 10))))
        except (TypeError, ValueError):
            lead = 10
        out = []
        for ev, mins in gcal.due_headsups(self.events, now, lead, self.seen):
            self.seen.add(ev["key"])
            out.append(gcal.headsup_text(ev, mins))
        if len(self.seen) > 500:
            self.seen = {e["key"] for e in self.events}
        return out

    # ----- background loop -----
    def start(self):
        threading.Thread(target=self._run, name="jarvis-briefings", daemon=True).start()

    def _run(self):
        while not self._stop.wait(TICK_S):
            self.tick()

    def stop(self):
        self._stop.set()


def register(reg: Registry, cfg: dict) -> Briefings:
    b = Briefings(cfg, lambda text: reg.announce(text))
    b.start()          # cheap: it does nothing until a setting is turned on
    return b
