"""Startup greeting and daily brief text. Pure functions; callers do the speaking."""
import logging

log = logging.getLogger(__name__)


def part_of_day(now):
    h = now.hour
    if 5 <= h < 12:
        return "morning"
    if 12 <= h < 18:
        return "afternoon"
    return "evening"


def greeting(cfg, now):
    pod = part_of_day(now)
    title = (cfg.get("user_title") or "sir").strip()
    p = cfg.get("personality", "butler")
    if p == "casual":
        return f"Hey, {pod}! Good to see you."
    if p == "direct":
        return f"{pod.capitalize()}."
    return f"Good {pod}, {title}."


def daily_brief(cfg, now, weather_fn=None, extras=()):
    """Greeting plus weather (if a function and home city exist) and any extras (A5)."""
    parts = [greeting(cfg, now)]
    city = (cfg.get("home_city") or "").strip()
    if weather_fn and city:
        try:
            w = weather_fn(city)
            if w:
                parts.append(str(w).strip())
        except Exception:
            log.exception("weather for brief failed")
    for fn in extras or ():
        try:
            t = fn()
            if t:
                parts.append(str(t).strip())
        except Exception:
            log.exception("brief extra failed")
    return " ".join(parts)


def startup_text(cfg, now, weather_fn=None, extras=()):
    """What to say at startup for cfg['startup_greeting'] (off | simple | brief); '' for off."""
    mode = cfg.get("startup_greeting", "simple")
    if mode == "off":
        return ""
    if mode == "brief":
        return daily_brief(cfg, now, weather_fn, extras)
    return greeting(cfg, now)
