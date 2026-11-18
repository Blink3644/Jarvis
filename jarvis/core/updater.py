"""In-app updates (Stage A7): check the owner's GitHub Releases, download and verify, hand off to update.ps1.

Safety: only REPO's releases are used (draft and pre-release ones are skipped), only a version newer
than ours is offered, the download is checked (size, checksum when published, zip integrity, the
version inside), and nothing is installed until the user clicks Update. The actual file swap is
done by update.ps1 after Jarvis has closed; it never touches %APPDATA%/Jarvis or .venv.
"""
import datetime
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import paths

log = logging.getLogger(__name__)

REPO = "Blink3644/Jarvis"
API = f"https://api.github.com/repos/{REPO}"
MAX_BYTES = 200 * 1024 * 1024
DOWNLOAD_HOSTS = ("github.com", "api.github.com", "codeload.github.com")   # plus *.githubusercontent.com
_VERSION_RE = re.compile(r'__version__\s*=\s*"([^"]+)"')


class UpdateError(Exception):
    """A short, plain message for the user (details go to the log)."""


@dataclass
class UpdateInfo:
    version: str            # e.g. "0.1.3"
    notes: str              # the release text ("what changed")
    url: str                # zip to download
    size: int | None        # expected bytes (attached zip only)
    sha256: str | None      # expected checksum, when the release publishes one
    page: str               # release page on github.com


# ---------------------------------------------------------------- versions
def parse_version(text: str) -> tuple[int, ...] | None:
    """'v0.1.3' / '0.1.3' -> (0, 1, 3). Anything else (e.g. '0.2-beta') -> None."""
    m = re.fullmatch(r"[vV]?(\d+(?:\.\d+){0,3})", (text or "").strip())
    if not m:
        return None
    parts = [int(p) for p in m.group(1).split(".")]
    return tuple(parts + [0] * (4 - len(parts)))


def is_newer(candidate: str, current: str) -> bool:
    c, cur = parse_version(candidate), parse_version(current)
    return bool(c and cur and c > cur)


def current_version() -> str:
    from jarvis import __version__
    return __version__


def install_dir() -> Path:
    import jarvis
    return Path(jarvis.__file__).resolve().parent.parent


# ---------------------------------------------------------------- HTTP
class Http:
    """The two calls the updater needs. Tests swap in a fake."""

    def get_json(self, url: str):
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "Jarvis-updater"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))

    def get_text(self, url: str, limit: int = 65536) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": "Jarvis-updater"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read(limit).decode("utf-8", "replace")

    def download(self, url: str, dest: Path, progress=None) -> tuple[int, str]:
        """Save url to dest. Returns (bytes written, final URL after redirects)."""
        req = urllib.request.Request(url, headers={"User-Agent": "Jarvis-updater"})
        with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            n = 0
            while chunk := r.read(256 * 1024):
                n += len(chunk)
                if n > MAX_BYTES:
                    raise UpdateError("The update file is far too big, so I stopped.")
                f.write(chunk)
                if progress:
                    progress(n, total)
            if total and n != total:
                raise UpdateError("The download stopped part-way. Try again.")
            return n, r.geturl()


def _host_ok(url: str) -> bool:
    u = urllib.parse.urlparse(url)
    host = (u.hostname or "").lower()
    return u.scheme == "https" and (host in DOWNLOAD_HOSTS or host.endswith(".githubusercontent.com"))


def _from_repo(url: str, prefix: str) -> bool:
    return (url or "").lower().startswith(prefix.lower())


# ---------------------------------------------------------------- check
def pick_release(releases: list, current: str) -> dict | None:
    """The newest proper release (no drafts or pre-releases) that is newer than `current`."""
    best, best_v = None, None
    for r in releases or []:
        if not isinstance(r, dict) or r.get("draft") or r.get("prerelease"):
            continue
        v = parse_version(r.get("tag_name", ""))
        if v and (best_v is None or v > best_v):
            best, best_v = r, v
    if best and is_newer(best["tag_name"], current):
        return best
    return None


def _checksum(release: dict, asset: dict, http) -> str | None:
    d = (asset.get("digest") or "").lower()          # GitHub fills this in for new uploads
    if d.startswith("sha256:") and re.fullmatch(r"[0-9a-f]{64}", d[7:]):
        return d[7:]
    for a in release.get("assets") or []:            # or a "<zip name>.sha256" file next to it
        if a.get("name", "").lower() == asset["name"].lower() + ".sha256":
            url = a.get("browser_download_url", "")
            if not _from_repo(url, f"https://github.com/{REPO}/releases/download/"):
                return None
            m = re.search(r"\b[0-9a-fA-F]{64}\b", http.get_text(url))
            if m:
                return m.group(0).lower()
    return None


def release_info(release: dict, http) -> UpdateInfo:
    """Turn a GitHub release into what we download. Raises UpdateError if it doesn't look right."""
    page = release.get("html_url", "")
    if not _from_repo(page, f"https://github.com/{REPO}/releases/"):
        raise UpdateError("That release isn't from the Jarvis repository, so I won't use it.")
    version = release["tag_name"].lstrip("vV")
    zips = [a for a in release.get("assets") or [] if a.get("name", "").lower().endswith(".zip")]
    zips.sort(key=lambda a: not a["name"].lower().startswith("jarvis"))
    if zips:
        a = zips[0]
        url = a.get("browser_download_url", "")
        if not _from_repo(url, f"https://github.com/{REPO}/releases/download/"):
            raise UpdateError("The update file isn't from the Jarvis repository, so I won't use it.")
        return UpdateInfo(version, release.get("body") or "", url, a.get("size"), _checksum(release, a, http), page)
    # No zip attached: GitHub's own source zip of that release has the same files.
    url = release.get("zipball_url", "")
    if not _from_repo(url, f"{API}/zipball/"):
        raise UpdateError("That release has no download, so I can't update from it.")
    return UpdateInfo(version, release.get("body") or "", url, None, None, page)


def check(http=None, current: str | None = None) -> UpdateInfo | None:
    """Ask GitHub for the latest release. Returns UpdateInfo when it's newer, else None."""
    http = http or Http()
    current = current or current_version()
    try:
        releases = http.get_json(f"{API}/releases?per_page=20")
    except UpdateError:
        raise
    except Exception as e:
        log.warning("update check failed: %r", e)
        raise UpdateError("I couldn't reach GitHub to check for updates.") from e
    _save_state({"last_check": datetime.date.today().isoformat()})
    if not isinstance(releases, list):
        raise UpdateError("GitHub sent an answer I didn't understand.")
    r = pick_release(releases, current)
    return release_info(r, http) if r else None


# ---------------------------------------------------------------- once a day
def _state_path() -> Path:
    return paths.data_dir() / "update_check.json"


def _save_state(d: dict):
    try:
        _state_path().write_text(json.dumps(d), encoding="utf-8")
    except OSError:
        log.exception("could not save update-check state")


def due_today(today: datetime.date | None = None) -> bool:
    """True if we haven't checked yet today (the startup check runs at most once a day)."""
    today = today or datetime.date.today()
    try:
        last = json.loads(_state_path().read_text(encoding="utf-8")).get("last_check")
        return last != today.isoformat()
    except (OSError, ValueError, AttributeError):
        return True


# ---------------------------------------------------------------- download + verify
def why_cant_update(folder: Path | None = None) -> str | None:
    """None if this copy can update itself; otherwise a plain reason."""
    folder = folder or install_dir()
    if (folder / ".git").exists():
        return "This is a developer copy (a git folder), so update it with git instead."
    if not (folder / ".venv" / "Scripts" / "python.exe").exists():
        return "This copy wasn't set up with setup.bat, so I can't update it myself."
    if not (folder / "update.ps1").exists():
        return "update.ps1 is missing from your Jarvis folder, so I can't update."
    return None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _find_root(z: zipfile.ZipFile) -> str:
    """The folder inside the zip that holds Jarvis.pyw ('Jarvis/' or GitHub's 'owner-repo-sha/')."""
    for n in z.namelist():
        parts = n.split("/")
        if parts[-1] == "Jarvis.pyw" and len(parts) <= 2:
            return "/".join(parts[:-1]) + ("/" if len(parts) == 2 else "")
    raise UpdateError("That download isn't a Jarvis zip, so I won't install it.")


def download(info: UpdateInfo, work: Path, http=None, progress=None) -> Path:
    """Download and check the release. Returns the unpacked program folder, ready for update.ps1."""
    http = http or Http()
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    zpath = work / "update.zip"
    try:
        n, final = http.download(info.url, zpath, progress)
    except UpdateError:
        raise
    except Exception as e:
        log.warning("update download failed: %r", e)
        raise UpdateError("The download failed. Check your internet and try again.") from e
    if not _host_ok(final):
        raise UpdateError("The download came from an unexpected website, so I won't install it.")
    if info.size is not None and n != info.size:
        raise UpdateError("The download is incomplete. Try again.")
    if info.sha256 and _sha256(zpath) != info.sha256.lower():
        raise UpdateError("The download doesn't match its checksum, so I won't install it.")
    try:
        with zipfile.ZipFile(zpath) as z:
            if z.testzip() is not None:
                raise UpdateError("The download is damaged. Try again.")
            root = _find_root(z)
            for name in z.namelist():
                p = Path(name)
                if p.is_absolute() or ".." in p.parts or ":" in name:
                    raise UpdateError("That zip has unsafe file paths, so I won't install it.")
            src = f"{root}jarvis/__init__.py"
            m = _VERSION_RE.search(z.read(src).decode("utf-8", "replace")) if src in z.namelist() else None
            if not m or parse_version(m.group(1)) != parse_version(info.version):
                found = m.group(1) if m else "unknown"
                raise UpdateError(f"The release is called {info.version} but its files say version {found}. "
                                  "The release needs fixing before I can install it.")
            z.extractall(work / "files")
    except zipfile.BadZipFile as e:
        raise UpdateError("The download is damaged. Try again.") from e
    folder = (work / "files" / root).resolve()
    if not (folder / "Jarvis.pyw").exists():
        raise UpdateError("That download isn't a Jarvis zip, so I won't install it.")
    return folder


def work_dir() -> Path:
    return Path(tempfile.gettempdir()) / "JarvisUpdate"


def launch_installer(new_files: Path, folder: Path | None = None, pid: int | None = None,
                     popen=subprocess.Popen):
    """Start update.ps1 in its own window. It waits for Jarvis (pid) to close, swaps the files and restarts."""
    folder = folder or install_dir()
    script = new_files.parent.parent / "update.ps1"          # a copy, so the swap can't touch it
    shutil.copy2(folder / "update.ps1", script)
    args = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
            "-Source", str(new_files), "-Dest", str(folder), "-Auto", "-WaitPid", str(pid or os.getpid())]
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    log.info("starting updater: %s", args)
    return popen(args, cwd=str(new_files.parent.parent), creationflags=flags, close_fds=True)
