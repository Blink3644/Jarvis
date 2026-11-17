"""Start Menu shortcut for Jarvis (current user only, no admin). Also the shared launch command."""
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]          # the Jarvis program folder
LAUNCHER = ROOT / "Jarvis.pyw"
NAME = "Jarvis.lnk"


def start_menu_dir() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home()) / "Microsoft/Windows/Start Menu/Programs"


def pythonw() -> Path:
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    return pyw if pyw.exists() else exe


def launch_command() -> str:
    """Command line that starts Jarvis (tray mode) from any working folder."""
    return f'"{pythonw()}" "{LAUNCHER}"'


def write_icon(dest: Path) -> Path | None:
    try:
        from jarvis.core.ui.tray import make_icon
        img = make_icon("idle", 256)
        dest.parent.mkdir(parents=True, exist_ok=True)
        img.save(dest, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)])
        return dest
    except Exception:
        log.exception("could not write shortcut icon")
        return None


def create(folder: Path | None = None, icon: Path | None = None, shell=None) -> Path:
    """Create or refresh the Jarvis shortcut. Returns its path."""
    folder = Path(folder or start_menu_dir())
    folder.mkdir(parents=True, exist_ok=True)
    lnk = folder / NAME
    if shell is None:
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
    sc = shell.CreateShortcut(str(lnk))
    sc.TargetPath = str(pythonw())
    sc.Arguments = f'"{LAUNCHER}"'
    sc.WorkingDirectory = str(ROOT)
    sc.Description = "Jarvis voice assistant"
    if icon:
        sc.IconLocation = f"{icon},0"
    sc.Save()
    return lnk


def remove(folder: Path | None = None) -> None:
    try:
        (Path(folder or start_menu_dir()) / NAME).unlink()
    except FileNotFoundError:
        pass


def exists(folder: Path | None = None) -> bool:
    return (Path(folder or start_menu_dir()) / NAME).exists()


if __name__ == "__main__":               # used by make_shortcut.bat
    from jarvis.core import paths
    path = create(icon=write_icon(paths.data_dir() / "jarvis.ico"))
    print(f"Done. Jarvis is now in your Start Menu ({path.name}).")
