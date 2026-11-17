"""Rotating log at %APPDATA%/Jarvis/logs/jarvis.log. The API key is never written."""
import logging
import re
from logging.handlers import RotatingFileHandler

from . import paths

_KEY_RE = re.compile(r"AIza[0-9A-Za-z_\-]{20,}|(key=)[^&\s'\"]+")


def redact(msg: str, key: str | None = None) -> str:
    if key:
        msg = msg.replace(key, "[KEY]")
    return _KEY_RE.sub(lambda m: (m.group(1) or "") + "[KEY]", msg)


class RedactFilter(logging.Filter):
    def __init__(self, get_key=None):
        super().__init__()
        self.get_key = get_key

    def filter(self, record):
        msg = record.getMessage()
        if record.exc_info:
            msg += "\n" + logging.Formatter().formatException(record.exc_info)
            record.exc_info = None
            record.exc_text = None
        record.msg = redact(msg, self.get_key() if self.get_key else None)
        record.args = ()
        return True


def setup(get_key=None, level=logging.INFO) -> None:
    root = logging.getLogger()
    if any(getattr(h, "_jarvis", False) for h in root.handlers):
        return
    h = RotatingFileHandler(paths.sub("logs") / "jarvis.log", maxBytes=1_000_000,
                            backupCount=3, encoding="utf-8")
    h._jarvis = True
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    h.addFilter(RedactFilter(get_key))
    root.addHandler(h)
    root.setLevel(level)
    for noisy in ("httpx", "httpx2", "httpcore", "google_genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
