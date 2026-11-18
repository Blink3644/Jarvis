"""ClickUp (Stage A5, optional, off by default). Personal API token (pk_...) kept in keyring.

API checked 2026-11-17 against developer.clickup.com/reference (v2):
  GET  /user                      -> {"user": {"id", "username"}}
  GET  /team                      -> {"teams": [{"id", "name"}]}   (teams = Workspaces)
  GET  /team/{id}/space           -> {"spaces": [...]}
  GET  /space/{id}/folder         -> {"folders": [{"lists": [...]}]}
  GET  /space/{id}/list           -> {"lists": [...]}             (lists not in a folder)
  GET  /team/{id}/task            ?assignees[]&order_by=due_date&subtasks&due_date_lt (ms) -> {"tasks": [...]}
  POST /list/{id}/task            {name, description, due_date (ms), due_date_time}
Header: Authorization: <token>
"""
import datetime as dt
import logging

try:
    import httpx
except ImportError:
    import httpx2 as httpx

from jarvis.core.registry import Registry, Tool

log = logging.getLogger(__name__)

API = "https://api.clickup.com/api/v2"
KEYRING_USER = "clickup_token"
TIMEOUT = 8.0
MAX_TASKS = 15


def _keyring():
    import keyring
    return keyring


def get_token():
    try:
        return _keyring().get_password("Jarvis", KEYRING_USER)
    except Exception:
        return None


def set_token(token: str) -> None:
    _keyring().set_password("Jarvis", KEYRING_USER, token.strip())


def disconnect() -> None:
    try:
        _keyring().delete_password("Jarvis", KEYRING_USER)
    except Exception:
        pass


def looks_like_token(t: str) -> bool:
    return bool(t) and t.startswith("pk_") and len(t) > 10


def available(cfg) -> bool:
    return bool(cfg.get("clickup_enabled")) and bool(cfg.get("clickup_team_id")) and bool(get_token())


class ClickUpError(Exception):
    """Carries a plain message for the user."""


class Client:
    def __init__(self, token, http=None):
        self.token = token
        self.http = http or httpx

    def _req(self, method, path, **kw):
        try:
            r = self.http.request(method, API + path, headers={"Authorization": self.token},
                                  timeout=TIMEOUT, **kw)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
            raise ClickUpError("I can't reach ClickUp. The internet may be down.")
        if r.status_code == 401:
            raise ClickUpError("ClickUp didn't accept the token. Paste a new one in settings.")
        if r.status_code == 429:
            raise ClickUpError("ClickUp says too many requests. Try again in a minute.")
        if r.status_code >= 400:
            log.warning("ClickUp %s %s -> %s", method, path, r.status_code)
            raise ClickUpError("ClickUp gave an error. Try again in a moment.")
        return r.json()

    def user(self) -> dict:
        return self._req("GET", "/user").get("user", {})

    def teams(self) -> list[dict]:
        return [{"id": str(t["id"]), "name": t.get("name", "")} for t in self._req("GET", "/team").get("teams", [])]

    def lists(self, team_id) -> list[dict]:
        """Every list in the workspace as {id, name, where} (where = space / folder)."""
        out = []
        for sp in self._req("GET", f"/team/{team_id}/space", params={"archived": "false"}).get("spaces", []):
            for f in self._req("GET", f"/space/{sp['id']}/folder", params={"archived": "false"}).get("folders", []):
                for li in f.get("lists", []):
                    out.append({"id": str(li["id"]), "name": li.get("name", ""),
                                "where": f"{sp.get('name', '')} / {f.get('name', '')}"})
            for li in self._req("GET", f"/space/{sp['id']}/list", params={"archived": "false"}).get("lists", []):
                out.append({"id": str(li["id"]), "name": li.get("name", ""), "where": sp.get("name", "")})
        return out

    def tasks(self, team_id, params) -> list[dict]:
        return self._req("GET", f"/team/{team_id}/task", params=params).get("tasks", [])

    def create_task(self, list_id, body) -> dict:
        return self._req("POST", f"/list/{list_id}/task", json=body)


# ---------- pure helpers ----------
def to_ms(d: dt.datetime) -> int:
    return int(d.timestamp() * 1000)


def parse_due(text: str):
    """'2026-11-18' -> (ms at local noon, False); '2026-11-18T15:00' -> (ms, True)."""
    text = (text or "").strip()
    if not text:
        return None, False
    if "T" not in text and " " not in text:
        d = dt.date.fromisoformat(text)
        return to_ms(dt.datetime(d.year, d.month, d.day, 12).astimezone()), False
    d = dt.datetime.fromisoformat(text.replace(" ", "T"))
    return to_ms(d if d.tzinfo else d.astimezone()), True


def describe_task(t: dict) -> dict:
    out = {"name": t.get("name", ""), "status": (t.get("status") or {}).get("status", ""),
           "list": (t.get("list") or {}).get("name", "")}
    if t.get("due_date"):
        due = dt.datetime.fromtimestamp(int(t["due_date"]) / 1000).astimezone()
        out["due"] = due.strftime("%a %d %b")
    return out


def due_filter(when: str, now: dt.datetime) -> dict:
    """Query params for 'today' / 'overdue' / 'week' / 'any'."""
    end_today = now.replace(hour=23, minute=59, second=59, microsecond=0)
    start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if when == "today":
        return {"due_date_lt": to_ms(end_today)}
    if when == "overdue":
        return {"due_date_lt": to_ms(start_today)}
    if when == "week":
        return {"due_date_lt": to_ms(end_today + dt.timedelta(days=7))}
    return {}


def make_tools(cfg, client_fn=None, clock=None):
    client_fn = client_fn or (lambda: Client(get_token()))
    clock = clock or (lambda: dt.datetime.now().astimezone())
    user_cache: dict = {}

    def _uid(c):
        if "id" not in user_cache:
            user_cache["id"] = str(c.user().get("id", ""))
        return user_cache["id"]

    def fetch(when: str, mine: bool = True) -> list[dict]:
        c = client_fn()
        params = {"order_by": "due_date", "subtasks": "true", "page": 0}
        if mine:
            params["assignees[]"] = _uid(c)
        params.update(due_filter(when, clock()))
        tasks = c.tasks(cfg["clickup_team_id"], params)
        return [describe_task(t) for t in tasks[:MAX_TASKS]]

    def list_clickup_tasks(due: str = "any", only_mine: bool = True) -> dict:
        due = due if due in ("any", "today", "overdue", "week") else "any"
        try:
            tasks = fetch(due, bool(only_mine) if only_mine is not None else True)
        except ClickUpError as e:
            return {"error": str(e)}
        except Exception:
            log.exception("ClickUp list failed")
            return {"error": "ClickUp gave an error. Try again in a moment."}
        return {"due": due, "tasks": tasks, "count": len(tasks),
                "note": "Open tasks only, at most 15." if len(tasks) >= MAX_TASKS else ""}

    def create_clickup_task(name: str, description: str = "", due: str = "", list_name: str = "") -> dict:
        name = (name or "").strip()
        if not name:
            return {"error": "The task needs a name."}
        try:
            due_ms, has_time = parse_due(due)
        except ValueError:
            return {"error": "I need the due date as 2026-11-18 or 2026-11-18T15:00."}
        try:
            c = client_fn()
            list_id, list_label = cfg.get("clickup_list_id"), cfg.get("clickup_list_name", "")
            if (list_name or "").strip():
                match = find_list(c.lists(cfg["clickup_team_id"]), list_name)
                if not match:
                    return {"error": f"I couldn't find a ClickUp list called {list_name}."}
                list_id, list_label = match["id"], match["name"]
            if not list_id:
                return {"error": "No default ClickUp list is set. Pick one in settings, on the Integrations tab."}
            body = {"name": name}
            if description:
                body["description"] = description
            if due_ms:
                body["due_date"], body["due_date_time"] = due_ms, has_time
            c.create_task(list_id, body)
        except ClickUpError as e:
            return {"error": str(e)}
        except Exception:
            log.exception("ClickUp create failed")
            return {"error": "ClickUp gave an error. Try again in a moment."}
        return {"created": name, "list": list_label}

    def brief() -> str:
        try:
            due = fetch("today")
        except Exception:
            log.exception("ClickUp brief failed")
            return ""
        if not due:
            return "No ClickUp tasks due today."
        names = ", ".join(t["name"] for t in due[:5])
        n = len(due)
        return f"In ClickUp you have {n} task{'s' if n != 1 else ''} due today or overdue: {names}."

    return list_clickup_tasks, create_clickup_task, brief


def find_list(lists: list[dict], name: str):
    name = name.strip().lower()
    for li in lists:
        if li["name"].lower() == name:
            return li
    try:
        from rapidfuzz import process, fuzz
        best = process.extractOne(name, {i: li["name"].lower() for i, li in enumerate(lists)},
                                  scorer=fuzz.WRatio, score_cutoff=80)
        return lists[best[2]] if best else None
    except ImportError:
        return next((li for li in lists if name in li["name"].lower()), None)


def register(reg: Registry, cfg: dict) -> None:
    list_tasks, create_task, _ = make_tools(cfg)
    is_on = lambda: available(cfg)   # noqa: E731
    reg.register(Tool(
        name="list_clickup_tasks",
        description="List the user's open ClickUp tasks, soonest due first.",
        handler=list_tasks,
        parameters={"type": "object", "properties": {
            "due": {"type": "string", "enum": ["any", "today", "overdue", "week"],
                    "description": "today = due today or earlier; week = due within 7 days."},
            "only_mine": {"type": "boolean", "description": "Only tasks assigned to the user (default true)."},
        }},
        module="pc", visual=False, available=is_on,
    ))
    reg.register(Tool(
        name="create_clickup_task",
        description="Create a ClickUp task in the user's default list (or a named list).",
        handler=create_task,
        parameters={"type": "object", "properties": {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "due": {"type": "string", "description": "Optional local due date 2026-11-18 or 2026-11-18T15:00."},
            "list_name": {"type": "string", "description": "Only if the user names a list."},
        }, "required": ["name"]},
        module="pc", visual=False, available=is_on,
        destructive=True, confirm_text=lambda a: f'create the ClickUp task "{a.get("name", "")}"',
    ))


def brief_line(cfg) -> str:
    if not available(cfg):
        return ""
    return make_tools(cfg)[2]()
