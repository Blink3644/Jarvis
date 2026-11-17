"""Detect the focused game. gather_info() reads the real window; decide() is pure logic."""
import logging
import os
import time

from jarvis.core import game_profiles

log = logging.getLogger(__name__)

NON_GAMES = {
    "explorer.exe", "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe",
    "vlc.exe", "mpv.exe", "wmplayer.exe", "mpc-hc64.exe", "potplayermini64.exe", "netflix.exe",
    "winword.exe", "excel.exe", "powerpnt.exe", "outlook.exe", "onenote.exe", "acrobat.exe",
    "discord.exe", "obs64.exe", "obs32.exe", "spotify.exe", "teams.exe", "slack.exe", "zoom.exe",
    "code.exe", "devenv.exe", "notepad.exe", "applicationframehost.exe", "searchhost.exe",
    "startmenuexperiencehost.exe", "shellexperiencehost.exe", "lockapp.exe", "taskmgr.exe",
    "windowsterminal.exe", "cmd.exe", "powershell.exe", "claude.exe", "python.exe", "pythonw.exe",
    "steamwebhelper.exe", "steam.exe", "epicgameslauncher.exe", "mstsc.exe", "teamviewer.exe",
}

_CACHE_S = 2.0
_cache: tuple[float, dict | None] | None = None


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p)) if p else ""


def _title_name(title: str, exe: str) -> str:
    t = (title or "").strip()
    return t or os.path.splitext(exe)[0]


def decide(info: dict, installed: list[dict], game_list: list[str], fullscreen: bool) -> dict | None:
    """info: {exe, exe_path, title}. Returns {"name", "profile"} or None."""
    exe = (info.get("exe") or "").lower()
    if not exe:
        return None
    full = dict(info, name=_title_name(info.get("title", ""), exe))
    prof = game_profiles.match(full)
    if prof:
        return {"name": prof.name, "profile": prof}
    path = _norm(info.get("exe_path", ""))
    if path:
        for g in installed or []:
            d = _norm(g.get("install_dir", ""))
            if d and (path == d or path.startswith(d.rstrip("\\/") + os.sep)):
                return {"name": g.get("name") or full["name"], "profile": None}
    if exe in {x.lower() for x in (game_list or [])}:
        return {"name": full["name"], "profile": None}
    if fullscreen and exe not in NON_GAMES:
        return {"name": full["name"], "profile": None}
    return None


def gather_info() -> dict | None:
    """Foreground window facts (no decisions)."""
    import psutil
    import win32api
    import win32con
    import win32gui
    import win32process
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return None
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    exe = exe_path = ""
    try:
        exe_path = psutil.Process(pid).exe()
        exe = os.path.basename(exe_path)
    except Exception:
        pass
    fullscreen = False
    try:
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        mon = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
        ml, mt, mr, mb = win32api.GetMonitorInfo(mon)["Monitor"]
        fullscreen = l <= ml and t <= mt and r >= mr and b >= mb
    except Exception:
        pass
    return {"exe": exe, "exe_path": exe_path, "title": win32gui.GetWindowText(hwnd),
            "hwnd": hwnd, "pid": pid, "fullscreen": fullscreen}


def _installed() -> list[dict]:
    try:
        from jarvis.pc.libraries import installed_games
        return installed_games()
    except Exception:
        return []


def current_game(cfg=None, use_cache: bool = True) -> dict | None:
    global _cache
    now = time.monotonic()
    if use_cache and _cache and now - _cache[0] < _CACHE_S:
        return _cache[1]
    result = None
    try:
        info = gather_info()
        if info:
            gl = (cfg.get("game_list") if cfg is not None else None) or []
            d = decide(info, _installed(), gl, info["fullscreen"])
            if d:
                result = {"name": d["name"], "exe": info["exe"], "exe_path": info["exe_path"],
                          "title": info["title"], "hwnd": info["hwnd"], "pid": info["pid"]}
    except Exception:
        log.exception("game detection failed")
    _cache = (now, result)
    return result


def context_for(game: dict | None) -> str:
    if not game:
        return ""
    text = (f"The user is currently playing {game['name']}. "
            "When searching or answering, target that game's wiki and guides.")
    prof = game_profiles.match(dict(game))
    if prof and prof.context:
        try:
            extra = prof.context()
            if extra:
                text += "\n" + extra
        except Exception:
            log.exception("profile context failed")
    return text


def register(reg, cfg) -> None:
    reg.add_context(lambda: context_for(current_game(cfg)))
