"""open_app / close_app. Only launches things found in the Start Menu, game libraries or the alias list."""
import logging
import os
import re
import time
from pathlib import Path

from rapidfuzz import fuzz, process

from jarvis.core.registry import Tool
from jarvis.pc import libraries

log = logging.getLogger(__name__)

_START_DIRS = [
    Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
    Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
]
_SKIP_NAME = re.compile(r"uninstall|remove|readme|read me|release notes|help|documentation|manual|"
                        r"license|website|changelog|\bfaq\b|support", re.I)
PROTECTED = {"explorer.exe", "csrss.exe", "winlogon.exe", "svchost.exe", "lsass.exe", "dwm.exe",
             "system", "smss.exe", "wininit.exe", "services.exe", "taskmgr.exe"}
THRESHOLD = 78
_CACHE_SECONDS = 600
_cache: dict = {"t": 0.0, "entries": None}


# ---------- sources ----------
def scan_start_menu(dirs=None) -> list[dict]:
    entries = []
    for base in dirs if dirs is not None else _START_DIRS:
        if not base or not Path(base).is_dir():
            continue
        for p in Path(base).rglob("*"):
            if p.suffix.lower() not in (".lnk", ".url") or _SKIP_NAME.search(p.stem):
                continue
            entries.append({"name": p.stem, "launch": str(p), "source": "start_menu", "install_dir": ""})
    return entries


def scan_apps_folder() -> list[dict]:
    """Everything in Start (incl. Store apps such as Calculator and Settings), via the shell's AppsFolder."""
    try:
        import pythoncom
        import win32com.client
        try:
            pythoncom.CoInitialize()
        except Exception:
            pass
        folder = win32com.client.Dispatch("Shell.Application").NameSpace("shell:AppsFolder")
        out = []
        for item in folder.Items():
            name, app_id = item.Name, item.Path
            if name and app_id and not _SKIP_NAME.search(name):
                out.append({"name": name, "launch": "shell:AppsFolder\\" + app_id,
                            "source": "start_menu", "install_dir": ""})
        return out
    except Exception:
        log.warning("couldn't read the Start apps list", exc_info=True)
        return []


def _all_entries(cfg: dict) -> list[dict]:
    now = time.time()
    if _cache["entries"] is None or now - _cache["t"] > _CACHE_SECONDS:
        base = scan_start_menu() + libraries.installed_games()
        have = {e["name"].lower() for e in base}
        base += [e for e in scan_apps_folder() if e["name"].lower() not in have]
        _cache.update(t=now, entries=base)
    entries = list(_cache["entries"])
    for spoken, target in (cfg.get("app_aliases") or {}).items():
        entries.append({"name": spoken, "launch": None, "source": "alias", "target": str(target),
                        "install_dir": ""})
    return entries


def _resolve_alias(entry: dict, entries: list[dict]) -> dict | None:
    """An alias points at another app's name (preferred) or at a path the user typed in settings."""
    target = entry["target"]
    real = [e for e in entries if e["source"] != "alias"]
    for e in real:
        if e["name"].lower() == target.lower():
            return e
    best = process.extractOne(target, [e["name"] for e in real], scorer=fuzz.WRatio,
                              score_cutoff=THRESHOLD + 7)
    if best:
        return real[best[2]]
    if os.path.exists(target):
        return {"name": entry["name"], "launch": target, "source": "alias", "install_dir": ""}
    return None


def _score(q: str, name: str) -> tuple[int, int]:
    """(tier, score). Tier 3 = exact, 2 = whole words of the name, 1 = close spelling, 0 = no."""
    n = name.lower()
    if n == q:
        return 3, 100
    if re.search(r"(?<![a-z0-9])" + re.escape(q) + r"(?![a-z0-9])", n):
        return 2, 100 - min(40, len(n) - len(q))         # shorter names win
    words = re.findall(r"[a-z0-9']+", n)
    grams = {" ".join(words[i:j]) for i in range(len(words))
             for j in range(i + 1, min(len(words), i + len(q.split()) + 1) + 1)}
    best = fuzz.ratio(q, n)
    for g in grams:                                       # misheard names keep their first letter
        if g[:1] == q[:1]:
            best = max(best, fuzz.ratio(q, g))
    return (1 if best >= 60 else 0), int(best)


def find_app(name: str, cfg: dict) -> dict:
    """-> {"match": entry} or {"candidates": [names]} (ambiguous / none)."""
    entries = _all_entries(cfg)
    q = re.sub(r"\s+", " ", (name or "").strip().lower())
    if not q:
        return {"candidates": []}
    scored, seen = [], set()
    for e in entries:
        key = e["name"].lower()
        tier, sc = _score(q, key)
        if tier and (key, e["source"] == "alias") not in seen:
            seen.add((key, e["source"] == "alias"))
            scored.append((tier, sc, e))
    scored.sort(key=lambda t: (-t[0], -t[1], t[2]["source"] != "alias"))
    pick = None
    if scored:
        top = scored[0]
        same = [t for t in scored if t[0] == top[0] and t[2]["name"].lower() != top[2]["name"].lower()]
        if top[0] == 3:
            pick = top[2]
        elif top[0] == 2 and (not same or top[1] - same[0][1] >= 8):
            pick = top[2]
        elif top[0] == 1 and top[1] >= 85 and (not same or top[1] - same[0][1] >= 5):
            pick = top[2]
    if pick is not None:
        if pick["source"] == "alias":
            pick = _resolve_alias(pick, entries)
        if not pick:
            return {"candidates": []}     # the alias points at something that no longer exists
        return {"match": pick}
    names = []
    for _, _, e in scored:
        if e["name"] not in names:
            names.append(e["name"])
    return {"candidates": names[:3]}


# ---------- open ----------
def open_app(cfg: dict, name: str) -> dict:
    found = find_app(name, cfg)
    if "match" not in found:
        c = found["candidates"]
        return {"opened": False, "candidates": c,
                "message": ("Not sure which one: " + ", ".join(c) + ". Ask the user.") if c
                else f"I couldn't find an app called {name}."}
    e = found["match"]
    os.startfile(e["launch"])
    return {"opened": True, "app": e["name"]}


def _has_window(pids: set[int]) -> bool:
    import win32gui
    import win32process
    found = [False]

    def cb(hwnd, _):
        if (win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd).strip() and
                win32process.GetWindowThreadProcessId(hwnd)[1] in pids):
            found[0] = True
    win32gui.EnumWindows(cb, None)
    return found[0]


def wait_until_ready(cfg: dict, name: str, timeout: float = 20.0, settle: float = 2.0,
                     clock=time.monotonic, sleep=time.sleep) -> bool:
    """After open_app: wait until the app shows a window (max `timeout` s), then `settle` s more
    so it can take media keys etc. Used between routine steps. False if it never appeared."""
    entry = find_app(name, cfg).get("match")
    end = clock() + timeout
    while clock() < end:
        try:
            pids = {p.pid for p in find_processes(name, entry)}
            if pids and _has_window(pids):
                sleep(settle)
                return True
        except Exception:
            log.exception("waiting for %s failed", name)
            break
        sleep(0.5)
    log.info("%s didn't show a window within %.0f s", name, timeout)
    return False


# ---------- close ----------
def _is_protected(proc_name: str, exe: str, pid: int) -> bool:
    import psutil
    if (proc_name or "").lower() in PROTECTED:
        return True
    if exe and exe.lower().startswith(os.environ.get("SystemRoot", r"C:\Windows").lower() + "\\"):
        return True
    me = os.getpid()
    if pid == me:
        return True
    try:
        if pid == psutil.Process(me).ppid():
            return True
    except Exception:
        pass
    return False


def _shortcut_target(path: str) -> str:
    if path.lower().endswith(".lnk"):
        import win32com.client
        return win32com.client.Dispatch("WScript.Shell").CreateShortcut(path).TargetPath or ""
    return ""


def _window_pids(title_part: str) -> set[int]:
    import win32gui
    import win32process
    pids: set[int] = set()
    low = title_part.lower()

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and low in win32gui.GetWindowText(hwnd).lower():
            pids.add(win32process.GetWindowThreadProcessId(hwnd)[1])
    win32gui.EnumWindows(cb, None)
    return pids


def find_processes(name: str, entry: dict | None) -> list:
    """Running psutil processes belonging to the matched app (never protected ones)."""
    import psutil
    exe_name, install_dir = "", (entry or {}).get("install_dir") or ""
    launch = (entry or {}).get("launch") or ""
    try:
        target = _shortcut_target(launch) or (launch if launch.lower().endswith(".exe") else "")
        exe_name = os.path.basename(target).lower()
    except Exception:
        log.exception("could not read shortcut target")
    title_pids = _window_pids(name) if name else set()
    out = []
    for p in psutil.process_iter(["pid", "name", "exe"]):
        try:
            pname, exe = (p.info["name"] or ""), (p.info["exe"] or "")
            hit = ((exe_name and pname.lower() == exe_name) or
                   (install_dir and exe and exe.lower().startswith(install_dir.lower().rstrip("\\") + "\\")) or
                   p.info["pid"] in title_pids)
            if hit and not _is_protected(pname, exe, p.info["pid"]):
                out.append(p)
        except Exception:
            continue
    return out


def _post_close(pids: set[int]) -> None:
    import win32con
    import win32gui
    import win32process

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32process.GetWindowThreadProcessId(hwnd)[1] in pids:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    win32gui.EnumWindows(cb, None)


def close_app(cfg: dict, name: str, wait: float = 4.0) -> dict:
    import psutil
    entry = find_app(name, cfg).get("match")
    label = entry["name"] if entry else name
    procs = find_processes(entry["name"] if entry else name, entry)
    if not procs:
        return {"closed": False, "message": f"{label} doesn't seem to be running."}
    _post_close({p.pid for p in procs})
    deadline = time.time() + wait
    while time.time() < deadline and any(p.is_running() for p in procs):
        time.sleep(0.25)
    forced = False
    for p in procs:
        try:
            if p.is_running():
                p.terminate()
                forced = True
        except psutil.Error:
            pass
    return {"closed": True, "app": label, "forced": forced}


# ---------- registration ----------
def register(reg, cfg: dict) -> None:
    reg.register(Tool(
        name="open_app",
        description="Open an application or game by its spoken name (Start Menu apps, Steam/Epic games, "
                    "or the user's aliases). If it returns candidates, ask the user which one.",
        handler=lambda name: open_app(cfg, name),
        parameters={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        module="pc", visual=False))
    reg.register(Tool(
        name="close_app",
        description="Close a running application by its spoken name. The user is asked to confirm first.",
        handler=lambda name: close_app(cfg, name),
        parameters={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        destructive=True, confirm_text=lambda a: f"close {a.get('name', 'that app')}",
        module="pc", visual=False))
