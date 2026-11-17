"""'Start with Windows' via the HKCU Run key. Touches only the value named Jarvis."""
import logging

try:
    import winreg
except ImportError:  # non-Windows
    winreg = None

log = logging.getLogger(__name__)
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "Jarvis"


def default_command():
    # Same launcher as the Start Menu shortcut: tray mode, works from any working folder.
    from jarvis.core.shortcuts import launch_command
    return launch_command()


def current():
    """The command stored in the Run key, or None."""
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as k:
            return winreg.QueryValueEx(k, VALUE)[0]
    except OSError:
        return None


def is_enabled():
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as k:
            winreg.QueryValueEx(k, VALUE)
        return True
    except OSError:
        return False


def enable(cmd=None):
    if winreg is None:
        return False
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, VALUE, 0, winreg.REG_SZ, cmd or default_command())
    return True


def disable():
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, VALUE)
    except OSError:
        pass
    return True
