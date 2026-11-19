"""Google Calendar (Stage A5, optional, off by default).

Uses the user's own OAuth client (credentials.json from Google Cloud, "Desktop app" type),
copied to %APPDATA%/Jarvis/google_credentials.json. The refresh token lives in Windows
Credential Manager (keyring), never in a file or the log.

API checked 2026-11-17 against developers.google.com/workspace/calendar/api/v3/reference:
  GET  /calendars/primary/events?timeMin&timeMax&singleEvents=true&orderBy=startTime
  POST /calendars/primary/events {summary, start:{dateTime|date}, end:{dateTime|date}}
"""
import datetime as dt
import json
import logging
import shutil

try:
    import httpx
except ImportError:      # google-genai 3.x ships the same API as httpx2
    import httpx2 as httpx

from jarvis.core import paths
from jarvis.core.registry import Registry, Tool

log = logging.getLogger(__name__)

API = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
KEYRING_USER = "google_calendar_token"
TIMEOUT = 8.0
MAX_EVENTS = 15


# ---------- files and secrets ----------
def creds_path():
    return paths.data_dir() / "google_credentials.json"


def import_credentials(src) -> str:
    """Copy the user's credentials.json into Jarvis's folder. Returns '' or a plain error."""
    try:
        data = json.loads(open(src, encoding="utf-8").read())
    except Exception:
        return "That file couldn't be read. Pick the credentials.json you downloaded from Google Cloud."
    if "installed" not in data:
        if "web" in data:
            return ('That is a "Web application" client. Make a "Desktop app" client instead '
                    "(step 6 of the guide).")
        return "That doesn't look like a Google OAuth credentials.json file."
    shutil.copyfile(src, creds_path())
    return ""


def has_credentials() -> bool:
    return creds_path().exists()


def _keyring():
    import keyring
    return keyring


def get_refresh_token():
    try:
        raw = _keyring().get_password("Jarvis", KEYRING_USER)
        return json.loads(raw).get("refresh_token") if raw else None
    except Exception:
        return None


def set_refresh_token(token: str) -> None:
    _keyring().set_password("Jarvis", KEYRING_USER, json.dumps({"refresh_token": token}))


def disconnect() -> None:
    try:
        _keyring().delete_password("Jarvis", KEYRING_USER)
    except Exception:
        pass
    _token_cache.clear()


def is_connected() -> bool:
    return has_credentials() and bool(get_refresh_token())


def available(cfg) -> bool:
    return bool(cfg.get("calendar_enabled")) and is_connected()


def connect(open_browser=True, timeout_s=300) -> str:
    """Run Google's sign-in in the browser. Blocks until done. Returns '' or a plain error."""
    if not has_credentials():
        return "Pick your credentials.json first."
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(str(creds_path()), SCOPES)
        creds = flow.run_local_server(port=0, open_browser=open_browser, timeout_seconds=timeout_s,
                                      success_message="Jarvis is connected to Google Calendar. "
                                                      "You can close this tab.")
    except Exception as e:
        log.exception("Google sign-in failed")
        return f"Google sign-in didn't finish ({type(e).__name__}). Try again."
    if not creds.refresh_token:
        return "Google didn't give Jarvis a lasting sign-in. Disconnect and try again."
    set_refresh_token(creds.refresh_token)
    _token_cache.clear()
    return ""


# ---------- access token ----------
_token_cache: dict = {}


def _access_token() -> str:
    """A fresh access token from the stored refresh token (kept in memory until it expires)."""
    now = dt.datetime.now(dt.timezone.utc)
    if _token_cache.get("token") and _token_cache.get("expires", now) > now:
        return _token_cache["token"]
    refresh = get_refresh_token()
    if not refresh or not has_credentials():
        raise NotConnected()
    client = json.loads(creds_path().read_text(encoding="utf-8"))["installed"]
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    creds = Credentials(None, refresh_token=refresh, token_uri=client.get("token_uri", TOKEN_URI),
                        client_id=client["client_id"], client_secret=client.get("client_secret"),
                        scopes=SCOPES)
    creds.refresh(Request())
    _token_cache["token"] = creds.token
    _token_cache["expires"] = now + dt.timedelta(minutes=50)
    return creds.token


class NotConnected(Exception):
    pass


# ---------- helpers (pure) ----------
def day_range(now: dt.datetime, days: int):
    """Start of today to the end of today + (days-1), as RFC3339 strings with the local offset."""
    now = now.astimezone() if now.tzinfo is None else now
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + dt.timedelta(days=max(1, days))
    return start.isoformat(), end.isoformat()


def describe_event(ev: dict, today: dt.date | None = None) -> dict:
    s, e = ev.get("start", {}), ev.get("end", {})
    out = {"title": ev.get("summary") or "(no title)"}
    if "dateTime" in s:
        st = dt.datetime.fromisoformat(s["dateTime"].replace("Z", "+00:00")).astimezone()
        out["day"] = st.strftime("%a %d %b")
        out["start"] = st.strftime("%H:%M")
        if "dateTime" in e:
            out["end"] = dt.datetime.fromisoformat(e["dateTime"].replace("Z", "+00:00")).astimezone().strftime("%H:%M")
    elif "date" in s:
        out["day"] = dt.date.fromisoformat(s["date"]).strftime("%a %d %b")
        out["all_day"] = True
    if ev.get("location"):
        out["location"] = ev["location"]
    return out


def parse_start(text: str):
    """'2026-11-18T15:00' -> aware datetime; '2026-11-18' -> date. Raises ValueError."""
    text = (text or "").strip()
    if not text:
        raise ValueError("no start")
    if "T" not in text and " " not in text:
        return dt.date.fromisoformat(text)
    d = dt.datetime.fromisoformat(text.replace(" ", "T").replace("Z", "+00:00"))
    return d if d.tzinfo else d.astimezone()


def event_body(title: str, start, duration_minutes: int = 60) -> dict:
    if isinstance(start, dt.datetime):
        end = start + dt.timedelta(minutes=max(5, int(duration_minutes or 60)))
        return {"summary": title, "start": {"dateTime": start.isoformat()},
                "end": {"dateTime": end.isoformat()}}
    return {"summary": title, "start": {"date": start.isoformat()},
            "end": {"date": (start + dt.timedelta(days=1)).isoformat()}}   # end date is exclusive


# ---------- API calls ----------
def _friendly(e: Exception) -> dict:
    if isinstance(e, NotConnected):
        return {"error": "Google Calendar isn't connected. Connect it in settings, on the Integrations page."}
    if isinstance(e, (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError)):
        return {"error": "I can't reach Google Calendar. The internet may be down."}
    if type(e).__name__ == "RefreshError":
        return {"error": "Google sign-in has expired. Reconnect Google Calendar in settings."}
    log.warning("calendar request failed: %s", type(e).__name__)
    return {"error": "Google Calendar gave an error. Try again in a moment."}


def make_tools(cfg, http=None, token_fn=None, clock=None):
    http = http or httpx
    token_fn = token_fn or _access_token
    clock = clock or (lambda: dt.datetime.now().astimezone())

    def _headers():
        return {"Authorization": f"Bearer {token_fn()}"}

    def fetch(days: int) -> list[dict]:
        tmin, tmax = day_range(clock(), days)
        r = http.get(API, headers=_headers(), timeout=TIMEOUT, params={
            "timeMin": tmin, "timeMax": tmax, "singleEvents": "true", "orderBy": "startTime",
            "maxResults": MAX_EVENTS})
        r.raise_for_status()
        return [describe_event(ev) for ev in r.json().get("items", []) if ev.get("status") != "cancelled"]

    def get_calendar_events(days: int = 1) -> dict:
        try:
            days = max(1, min(14, int(days or 1)))
        except (TypeError, ValueError):
            days = 1
        try:
            events = fetch(days)
        except Exception as e:
            return _friendly(e)
        return {"days": days, "events": events, "count": len(events)}

    def add_calendar_event(title: str, start: str, duration_minutes: int = 60) -> dict:
        title = (title or "").strip()
        if not title:
            return {"error": "The event needs a title."}
        try:
            when = parse_start(start)
        except ValueError:
            return {"error": "I need the start as a date (2026-11-18) or date and time (2026-11-18T15:00)."}
        try:
            r = http.post(API, headers=_headers(), json=event_body(title, when, duration_minutes),
                          timeout=TIMEOUT)
            r.raise_for_status()
        except Exception as e:
            return _friendly(e)
        return {"added": describe_event(r.json())}

    def brief() -> str:
        try:
            events = fetch(1)
        except Exception:
            log.exception("calendar brief failed")
            return ""
        if not events:
            return "Your calendar is clear today."
        items = [f"{e['title']} at {e['start']}" if e.get("start") else f"{e['title']} (all day)"
                 for e in events[:5]]
        n = len(events)
        return f"You have {n} event{'s' if n != 1 else ''} today: " + ", ".join(items) + "."

    return get_calendar_events, add_calendar_event, brief


def _confirm_add(args):
    try:
        when = parse_start(args.get("start", ""))
        when_s = when.strftime("%A %d %B at %H:%M") if isinstance(when, dt.datetime) else when.strftime("%A %d %B")
    except ValueError:
        when_s = str(args.get("start", ""))
    return f'add "{args.get("title", "")}" to your calendar on {when_s}'


# Words that point to these tools; a request only describes the tools whose topic matches.
TOPICS = [("calendar", r"calendar|event|events|meeting|meetings|appointment|appointments|schedule|"
                       r"today|tomorrow|tonight|week|weekend|busy|free|plans|planned|agenda|monday|tuesday|"
                       r"wednesday|thursday|friday|saturday|sunday|book|add", ["get_calendar_events",
                                                                              "add_calendar_event"])]


def register(reg: Registry, cfg: dict) -> None:
    get_events, add_event, _ = make_tools(cfg)
    is_on = lambda: available(cfg)   # noqa: E731
    reg.register(Tool(
        name="get_calendar_events", side_effect=False, brings_data=True,
        description="List the user's Google Calendar events from today onwards (today = days 1).",
        handler=get_events,
        parameters={"type": "object", "properties": {
            "days": {"type": "integer", "description": "How many days from today, 1 to 14 (default 1 = today)."},
        }},
        module="pc", visual=False, available=is_on,
    ))
    reg.register(Tool(
        name="add_calendar_event",
        description="Add an event to the user's Google Calendar. Work out the date from the current date "
                    "and time; use local time.",
        handler=add_event,
        parameters={"type": "object", "properties": {
            "title": {"type": "string"},
            "start": {"type": "string", "description": "Local start, e.g. 2026-11-18T15:00, or just "
                                                       "2026-11-18 for an all-day event."},
            "duration_minutes": {"type": "integer", "description": "Length in minutes (default 60)."},
        }, "required": ["title", "start"]},
        module="pc", visual=False, available=is_on,
        destructive=True, confirm_text=_confirm_add,     # read back the date before writing
    ))


def brief_line(cfg) -> str:
    """For the 'Daily brief' greeting; '' when Calendar isn't set up."""
    if not available(cfg):
        return ""
    return make_tools(cfg)[2]()
