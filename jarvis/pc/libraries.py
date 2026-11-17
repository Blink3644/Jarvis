"""Installed game libraries (Steam + Epic). Read-only; nothing is launched here."""
import json
import logging
import os
import re
import time
from pathlib import Path

log = logging.getLogger(__name__)

CACHE_SECONDS = 600
_cache: dict = {"t": 0.0, "games": None}

_SKIP = re.compile(r"steamworks|steam linux runtime|proton|redistributable|steamvr|"
                   r"soundtrack|dedicated server|\bsdk\b|\btools?\b$", re.I)
EPIC_MANIFESTS = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / \
    "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"

_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')


def parse_vdf(text: str) -> dict:
    """Parse Valve's text KeyValues format into nested dicts."""
    root: dict = {}
    stack = [root]
    key = None
    for m in _TOKEN.finditer(text):
        s, brace = m.group(1), m.group(2)
        if brace == "{":
            new: dict = {}
            if key is not None:
                stack[-1][key] = new
            stack.append(new)
            key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        else:
            s = s.replace("\\\\", "\\").replace('\\"', '"')
            if key is None:
                key = s
            else:
                stack[-1][key] = s
                key = None
    return root


def _ci(d: dict, name: str):
    for k, v in d.items():
        if k.lower() == name.lower():
            return v
    return None


def _steam_path() -> Path | None:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            return Path(winreg.QueryValueEx(k, "SteamPath")[0])
    except Exception:
        return None


def steam_games(steam_path: Path | None = None) -> list[dict]:
    steam_path = steam_path or _steam_path()
    if not steam_path:
        return []
    libs = [steam_path]
    try:
        data = parse_vdf((steam_path / "steamapps" / "libraryfolders.vdf").read_text("utf-8", "replace"))
        folders = _ci(data, "libraryfolders") or {}
        for v in folders.values():
            p = v.get("path") if isinstance(v, dict) else v
            if p:
                libs.append(Path(p))
    except OSError:
        log.info("no libraryfolders.vdf under %s", steam_path)
    games, seen = [], set()
    for lib in libs:
        apps = lib / "steamapps"
        if not apps.is_dir():
            continue
        for acf in apps.glob("appmanifest_*.acf"):
            try:
                st = _ci(parse_vdf(acf.read_text("utf-8", "replace")), "AppState") or {}
                appid, name, idir = st.get("appid"), st.get("name"), st.get("installdir", "")
            except OSError:
                continue
            if not appid or not name or appid in seen or _SKIP.search(name):
                continue
            seen.add(appid)
            games.append({"name": name, "install_dir": str(apps / "common" / idir),
                          "launch": f"steam://rungameid/{appid}", "source": "steam"})
    return games


def epic_games(manifest_dir: Path | None = None) -> list[dict]:
    manifest_dir = manifest_dir or EPIC_MANIFESTS
    games = []
    if not manifest_dir.is_dir():
        return games
    for item in manifest_dir.glob("*.item"):
        try:
            d = json.loads(item.read_text("utf-8-sig"))
            name, ns = d["DisplayName"], d["CatalogNamespace"]
            cid, app = d["CatalogItemId"], d["AppName"]
        except (OSError, ValueError, KeyError):
            continue
        games.append({"name": name, "install_dir": d.get("InstallLocation", ""),
                      "launch": f"com.epicgames.launcher://apps/{ns}%3A{cid}%3A{app}?action=launch&silent=true",
                      "source": "epic"})
    return games


def installed_games() -> list[dict]:
    """All installed Steam/Epic games; cached for 10 minutes."""
    now = time.time()
    if _cache["games"] is not None and now - _cache["t"] < CACHE_SECONDS:
        return list(_cache["games"])
    games: list[dict] = []
    for fn in (steam_games, epic_games):
        try:
            games += fn()
        except Exception:
            log.exception("reading %s failed", fn.__name__)
    games.sort(key=lambda g: g["name"].lower())
    _cache.update(t=now, games=games)
    return list(games)
