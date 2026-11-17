"""Where Jarvis keeps its files (%APPDATA%/Jarvis, or JARVIS_HOME for tests)."""
import os
from pathlib import Path


def data_dir() -> Path:
    base = os.environ.get("JARVIS_HOME")
    if not base:
        base = os.path.join(os.environ.get("APPDATA") or str(Path.home()), "Jarvis")
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


def sub(name: str) -> Path:
    p = data_dir() / name
    p.mkdir(parents=True, exist_ok=True)
    return p
