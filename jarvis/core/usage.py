"""Local request counter per model per day.

Google resets daily (RPD) quotas at midnight Pacific time (checked 2026-11-17),
so the "day" here is the Pacific-time date.
"""
import datetime
import json
import logging
from zoneinfo import ZoneInfo

from . import paths

log = logging.getLogger(__name__)
PACIFIC = ZoneInfo("America/Los_Angeles")


def pacific_today(now: datetime.datetime | None = None) -> str:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return now.astimezone(PACIFIC).date().isoformat()


def next_reset(now: datetime.datetime | None = None) -> datetime.datetime:
    """Next midnight Pacific, as an aware UTC datetime."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    local = now.astimezone(PACIFIC)
    midnight = datetime.datetime.combine(local.date() + datetime.timedelta(days=1),
                                         datetime.time(0), tzinfo=PACIFIC)
    return midnight.astimezone(datetime.timezone.utc)


class Usage:
    def __init__(self, cfg: dict, clock=None):
        self.cfg = cfg
        self.clock = clock or (lambda: datetime.datetime.now(datetime.timezone.utc))
        self.path = paths.data_dir() / "usage.json"
        self.data = {"day": pacific_today(self.clock()), "counts": {}, "warned": []}
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text(encoding="utf-8")))
            except Exception:
                log.exception("usage.json unreadable; starting fresh")
        self._roll()

    def _roll(self):
        today = pacific_today(self.clock())
        if self.data.get("day") != today:
            self.data = {"day": today, "counts": {}, "warned": []}
            self._save()

    def _save(self):
        try:
            self.path.write_text(json.dumps(self.data), encoding="utf-8")
        except Exception:
            log.exception("could not save usage.json")

    def limit(self, model: str) -> int | None:
        return (self.cfg.get("daily_limits") or {}).get(model)

    def count(self, model: str) -> int:
        self._roll()
        return self.data["counts"].get(model, 0)

    def exhausted(self, model: str) -> bool:
        lim = self.limit(model)
        return bool(lim) and self.count(model) >= lim

    def record(self, model: str) -> str | None:
        """Count one request. Returns a one-time warning message at the warn threshold."""
        self._roll()
        self.data["counts"][model] = self.data["counts"].get(model, 0) + 1
        msg = None
        lim = self.limit(model)
        pct = self.cfg.get("usage_warn_percent", 80)
        if lim and model not in self.data["warned"] and self.data["counts"][model] * 100 >= lim * pct:
            self.data["warned"].append(model)
            msg = (f"Heads up: I've used {self.data['counts'][model]} of today's "
                   f"{lim} free requests on {model}.")
        self._save()
        return msg

    def summary(self) -> list[tuple[str, int, int | None]]:
        self._roll()
        models = [self.cfg["model_main"], self.cfg["model_fallback"]]
        return [(m, self.count(m), self.limit(m)) for m in models]
