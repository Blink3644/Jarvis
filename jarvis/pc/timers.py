"""Timers, alarms and reminders. Timers and alarms live in memory only; reminders are saved
to %APPDATA%/Jarvis/reminders.json so they survive a restart."""
import datetime
import itertools
import json
import logging
import re
import threading
import time

from rapidfuzz import fuzz

from jarvis.core import paths
from jarvis.core.registry import Registry, Tool

log = logging.getLogger(__name__)

_TIME_RE = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*([ap])?\.?m?\.?\s*$", re.I)
_UNITS = {"h": 3600, "m": 60, "s": 1}
_REL_RE = re.compile(r"(\d+(?:\.\d+)?|an?|half an?)\s*(hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b", re.I)
MAX_REMINDERS = 50
LATE_S = 120        # a reminder fired this late (Jarvis was off) says so


def parse_when(text: str, now: datetime.datetime) -> datetime.datetime | None:
    """'in 20 minutes', 'in an hour', 'at 6', '6:30 pm', 'tomorrow at 7' -> datetime or None.

    A bare hour with no am/pm ('at 6') means the next 6 o'clock, morning or evening."""
    t = (text or "").strip().lower().replace(" and ", " ")
    if not t:
        return None
    if t.startswith("in "):
        rest, total = t[3:].strip(), 0.0
        for num, unit in _REL_RE.findall(rest):
            n = 0.5 if num.startswith("half") else 1.0 if num in ("a", "an") else float(num)
            total += n * _UNITS[unit[0]]
        leftover = _REL_RE.sub("", rest).strip()
        if total <= 0 or leftover:
            return None
        return now + datetime.timedelta(seconds=total)
    tomorrow = t.startswith("tomorrow")
    t = re.sub(r"^(tomorrow\s*)?(at\s+)?", "", t).replace("o'clock", "").replace("oclock", "").strip()
    hm = parse_clock(t)
    if not hm:
        return None
    h, mi = hm
    has_ampm = bool(re.search(r"[ap]\.?m?\.?\s*$", t))
    base = now + datetime.timedelta(days=1) if tomorrow else now
    choices = [h]
    if not has_ampm and 1 <= h <= 11:
        choices.append(h + 12)          # 'at 6' could be 6:00 or 18:00
    if not has_ampm and h == 12:
        choices.append(0)
    cands = sorted(base.replace(hour=c, minute=mi, second=0, microsecond=0) for c in choices)
    if tomorrow:
        return base.replace(hour=h, minute=mi, second=0, microsecond=0)   # 'tomorrow at 7' = 7 am
    for c in cands:
        if c > now:
            return c
    return cands[0] + datetime.timedelta(days=1)


def parse_clock(text: str) -> tuple[int, int] | None:
    """'19:30', '7:30 pm', '7pm' -> (hour, minute) or None."""
    m = _TIME_RE.match(text or "")
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if mi > 59:
        return None
    if ap:
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if ap == "p" else 0)
    elif h > 23:
        return None
    return h, mi


def human_duration(seconds: float, adjective: bool = False) -> str:
    s = int(round(seconds))
    parts = []
    for unit, n in (("hour", 3600), ("minute", 60), ("second", 1)):
        q, s = divmod(s, n)
        if q:
            parts.append(f"{q} {unit}{'s' if q != 1 and not adjective else ''}")
    return " ".join(parts) or "0 seconds"


class TimerManager:
    def __init__(self, announce, clock=time.time, use_thread: bool = True, store=None):
        self.announce = announce          # callable(text); may be replaced
        self.clock = clock
        self.use_thread = use_thread
        self.store = store                # Path for reminders.json, or None (memory only)
        self._items: dict[int, dict] = {}
        self._ids = itertools.count(1)
        self._cv = threading.Condition()
        self._thread = None
        self._stop = False
        self._load()

    # ----- reminders on disk -----
    def _load(self):
        if not self.store or not self.store.exists():
            return
        try:
            saved = json.loads(self.store.read_text(encoding="utf-8"))
        except Exception:
            log.exception("reminders.json is unreadable; starting with none (file left untouched)")
            return
        with self._cv:
            for r in saved[:MAX_REMINDERS] if isinstance(saved, list) else []:
                try:
                    item = {"kind": "reminder", "label": str(r["text"])[:200], "due": float(r["due"])}
                except (KeyError, TypeError, ValueError):
                    continue
                item["at"] = datetime.datetime.fromtimestamp(item["due"]).strftime("%H:%M")
                item["id"] = next(self._ids)
                self._items[item["id"]] = item
            if self._items:
                self._ensure_thread()

    def _save(self):
        """Write pending reminders. Call with self._cv held."""
        if not self.store:
            return
        data = [{"text": i["label"], "due": i["due"]} for i in self._items.values() if i["kind"] == "reminder"]
        try:
            tmp = self.store.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            tmp.replace(self.store)
        except Exception:
            log.exception("could not save reminders")

    def add_reminder(self, due: float, text: str) -> dict:
        item = {"kind": "reminder", "label": text.strip()[:200], "due": due,
                "at": datetime.datetime.fromtimestamp(due).strftime("%H:%M")}
        return self._add(item)

    def reminder_count(self) -> int:
        with self._cv:
            return sum(1 for i in self._items.values() if i["kind"] == "reminder")

    # ----- creation -----
    def add_timer(self, seconds: float, label: str = "") -> dict:
        item = {"kind": "timer", "label": label.strip(), "seconds": seconds,
                "due": self.clock() + seconds}
        return self._add(item)

    def add_alarm(self, hour: int, minute: int, label: str = "") -> dict:
        now = datetime.datetime.fromtimestamp(self.clock())
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += datetime.timedelta(days=1)
        item = {"kind": "alarm", "label": label.strip(), "at": target.strftime("%H:%M"),
                "due": target.timestamp(), "tomorrow": target.date() != now.date()}
        return self._add(item)

    def _add(self, item: dict) -> dict:
        with self._cv:
            item["id"] = next(self._ids)
            self._items[item["id"]] = item
            if item["kind"] == "reminder":
                self._save()
            self._ensure_thread()
            self._cv.notify_all()
        return item

    # ----- queries -----
    def snapshot(self) -> list[dict]:
        now = self.clock()
        with self._cv:
            items = sorted(self._items.values(), key=lambda i: i["due"])
            return [self._public(i, now) for i in items]

    @staticmethod
    def _public(i: dict, now: float) -> dict:
        d = {"id": i["id"], "type": i["kind"], "label": i["label"],
             "time_left": human_duration(max(0, i["due"] - now))}
        if i["kind"] in ("alarm", "reminder"):
            d["at"] = i["at"]
        return d

    def cancel(self, which: str) -> list[dict]:
        which = (which or "").strip()
        with self._cv:
            if which.lower() == "all":
                gone = list(self._items.values())
            elif which.isdigit() and int(which) in self._items:
                gone = [self._items[int(which)]]
            else:
                gone = []
                scored = [(fuzz.partial_ratio(which.lower(), i["label"].lower()), i)
                          for i in self._items.values() if i["label"]]
                scored = [s for s in scored if s[0] >= 75]
                if scored:
                    gone = [max(scored, key=lambda s: s[0])[1]]
            for i in gone:
                del self._items[i["id"]]
            if any(i["kind"] == "reminder" for i in gone):
                self._save()
        now = self.clock()
        return [self._public(i, now) for i in gone]

    # ----- firing -----
    @staticmethod
    def message(i: dict, now: float | None = None) -> str:
        label = i["label"]
        if i["kind"] == "reminder":
            if now is not None and now - i["due"] > LATE_S:
                return f"While I was off, you asked me to remind you at {i['at']}: {label}."
            return f"Reminder: {label}."
        if i["kind"] == "timer":
            dur = human_duration(i["seconds"], adjective=True)
            return f"Your {dur} {label} timer is done." if label else f"Your {dur} timer is done."
        return f"Your {label} alarm is going off." if label else f"Your {i['at']} alarm is going off."

    def check(self) -> int:
        """Fire everything that is due. Returns how many fired."""
        now = self.clock()
        with self._cv:
            due = sorted((i for i in self._items.values() if i["due"] <= now), key=lambda i: i["due"])
            for i in due:
                del self._items[i["id"]]
            if any(i["kind"] == "reminder" for i in due):
                self._save()
        for i in due:
            try:
                self.announce(self.message(i, now))
            except Exception:
                log.exception("announce failed")
        return len(due)

    def _ensure_thread(self):
        if self.use_thread and (self._thread is None or not self._thread.is_alive()):
            self._stop = False
            self._thread = threading.Thread(target=self._run, name="jarvis-timers", daemon=True)
            self._thread.start()

    def _run(self):
        while True:
            self.check()
            with self._cv:
                if self._stop:
                    return
                if not self._items:
                    self._thread = None
                    return
                nxt = min(i["due"] for i in self._items.values()) - self.clock()
                self._cv.wait(timeout=max(0.05, min(nxt, 5.0)))

    def shutdown(self):
        with self._cv:
            self._stop = True
            self._cv.notify_all()


# Words that point to these tools; a request only describes the tools whose topic matches.
TOPICS = [("timers", r"timer|timers|alarm|alarms|remind|reminder|wake\s+me|countdown|minutes?|seconds?|hours?|"
                     r"mins?|secs?|o'?clock|am|pm|cancel|stop", ["set_timer", "set_alarm", "set_reminder",
                                                               "cancel_timer", "list_timers"])]


def register(reg: Registry, cfg: dict, clock=time.time, use_thread: bool = True, store=None) -> TimerManager:
    store = store or paths.data_dir() / "reminders.json"
    mgr = TimerManager(lambda text: reg.announce(text), clock=clock, use_thread=use_thread, store=store)

    def set_reminder(text: str, when: str) -> dict:
        text = " ".join((text or "").split())
        if not text:
            return {"error": "What should I remind you about?"}
        now = datetime.datetime.fromtimestamp(clock())
        due = parse_when(when, now)
        if not due:
            return {"error": f"I couldn't read '{when}' as a time. Try 'in 20 minutes', 'at 6 pm' "
                             "or 'tomorrow at 9'."}
        if mgr.reminder_count() >= MAX_REMINDERS:
            return {"error": f"You already have {MAX_REMINDERS} reminders. Cancel some first."}
        i = mgr.add_reminder(due.timestamp(), text)
        day = "today" if due.date() == now.date() else "tomorrow" if due.date() == (
            now + datetime.timedelta(days=1)).date() else due.strftime("%A %d %B")
        return {"set": True, "id": i["id"], "at": i["at"], "day": day, "text": i["label"]}

    def set_timer(seconds: float = 0, minutes: float = 0, label: str = "") -> dict:
        total = float(seconds or 0) + float(minutes or 0) * 60
        if total <= 0:
            return {"error": "Tell me how long the timer should run."}
        if total > 24 * 3600:
            return {"error": "Timers can be at most 24 hours. Use an alarm for later."}
        i = mgr.add_timer(total, label or "")
        return {"set": True, "id": i["id"], "duration": human_duration(total), "label": i["label"]}

    def set_alarm(time: str, label: str = "") -> dict:   # noqa: A002 - tool arg name
        hm = parse_clock(time)
        if not hm:
            return {"error": f"I couldn't read '{time}' as a time. Use something like 07:30 or 7:30 pm."}
        i = mgr.add_alarm(hm[0], hm[1], label or "")
        return {"set": True, "id": i["id"], "at": i["at"],
                "day": "tomorrow" if i["tomorrow"] else "today", "label": i["label"]}

    def list_timers() -> dict:
        items = mgr.snapshot()
        return {"timers": items, "count": len(items)}

    def cancel_timer(which: str) -> dict:
        gone = mgr.cancel(which)
        if not gone:
            return {"error": f"No timer, alarm or reminder matched '{which}'."}
        return {"cancelled": gone}

    note = " They are kept in memory only, so they are lost if Jarvis restarts."
    reg.register(Tool("set_timer", "Start a countdown timer; say it out loud when it finishes. "
                      "Give seconds and/or minutes." + note, set_timer,
                      {"type": "object", "properties": {
                          "seconds": {"type": "number"}, "minutes": {"type": "number"},
                          "label": {"type": "string", "description": "What it is for, e.g. pasta"}}},
                      module="pc", visual=False))
    reg.register(Tool("set_alarm", "Set an alarm for the next occurrence of a clock time." + note, set_alarm,
                      {"type": "object", "properties": {
                          "time": {"type": "string", "description": "'HH:MM' 24 hour, or '7:30 pm'"},
                          "label": {"type": "string"}}, "required": ["time"]},
                      module="pc", visual=False))
    reg.register(Tool("set_reminder", "Remind the user about something later; Jarvis says it out loud at "
                      "that time. Saved, so it survives a restart. Use this for 'remind me ...'.", set_reminder,
                      {"type": "object", "properties": {
                          "text": {"type": "string", "description": "What to remind them about, e.g. stretch"},
                          "when": {"type": "string", "description": "As the user said it: 'in 20 minutes', "
                                   "'in an hour', 'at 6', '6:30 pm', 'tomorrow at 9'"}},
                       "required": ["text", "when"]},
                      module="pc", visual=False))
    reg.register(Tool("list_timers", "List running timers, alarms and reminders with time left.", list_timers,
                      side_effect=False, module="pc", visual=False))
    reg.register(Tool("cancel_timer", "Cancel a timer, alarm or reminder by its label or id, or 'all'.", cancel_timer,
                      {"type": "object", "properties": {"which": {"type": "string",
                       "description": "label, id number, or 'all'"}}, "required": ["which"]},
                      module="pc", visual=False))
    return mgr
