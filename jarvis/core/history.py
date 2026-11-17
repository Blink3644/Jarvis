"""Conversation memory within a session, plus the three history settings.

session  - forget everything when Jarvis closes (default)
log      - also write readable transcripts to %APPDATA%/Jarvis/history/ (never reused)
remember - on close, store a short summary; on start, load the last few into the prompt
"""
import datetime
import json
import logging
import shutil

from google.genai import types

from . import paths

log = logging.getLogger(__name__)


class Conversation:
    """History as a list of exchanges. One exchange = the user's message plus everything
    up to Jarvis's final reply (tool calls included). Trimming drops whole exchanges so a
    tool call is never separated from its result."""

    def __init__(self, max_turns: int = 20):
        self.max_turns = max_turns
        self.exchanges: list[list[types.Content]] = []
        self.transcript: list[tuple[str, str]] = []   # (who, text) for logs/summaries

    def contents(self, pending: list[types.Content] | None = None) -> list[types.Content]:
        out = [c for ex in self.exchanges for c in ex]
        return out + (pending or [])

    def commit(self, exchange: list[types.Content], user_text: str, reply_text: str) -> None:
        self.exchanges.append(exchange)
        if len(self.exchanges) > self.max_turns:
            del self.exchanges[: len(self.exchanges) - self.max_turns]
        self.transcript.append(("You", user_text))
        self.transcript.append(("Jarvis", reply_text))

    def clear(self) -> None:
        self.exchanges.clear()
        self.transcript.clear()


class HistoryStore:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.dir = paths.data_dir() / "history"
        self.summaries_path = paths.data_dir() / "recent_summaries.json"
        self.started = datetime.datetime.now()

    @property
    def mode(self) -> str:
        return self.cfg.get("history_mode", "session")

    def log_line(self, who: str, text: str) -> None:
        if self.mode != "log":
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            f = self.dir / (self.started.strftime("%Y-%m-%d_%H-%M-%S") + ".txt")
            with f.open("a", encoding="utf-8") as fh:
                fh.write(f"[{datetime.datetime.now():%H:%M}] {who}: {text}\n")
        except Exception:
            log.exception("could not write transcript")

    def load_summaries(self) -> str:
        if self.mode != "remember" or not self.summaries_path.exists():
            return ""
        try:
            items = json.loads(self.summaries_path.read_text(encoding="utf-8"))
        except Exception:
            log.exception("recent_summaries.json unreadable")
            return ""
        n = self.cfg.get("remember_count", 5)
        return "\n".join(f"- {i['date']}: {i['summary']}" for i in items[-n:])

    def save_summary(self, summary: str) -> None:
        if self.mode != "remember" or not summary.strip():
            return
        items = []
        if self.summaries_path.exists():
            try:
                items = json.loads(self.summaries_path.read_text(encoding="utf-8"))
            except Exception:
                items = []
        items.append({"date": self.started.strftime("%Y-%m-%d %H:%M"), "summary": summary.strip()[:600]})
        items = items[-self.cfg.get("remember_count", 5):]
        self.summaries_path.write_text(json.dumps(items, indent=1), encoding="utf-8")

    def clear_all(self) -> None:
        """The settings window's "Clear history" button."""
        shutil.rmtree(self.dir, ignore_errors=True)
        self.summaries_path.unlink(missing_ok=True)


def fallback_summary(transcript: list[tuple[str, str]]) -> str:
    """Used when Gemini can't summarise at shutdown: the user's last few questions."""
    asks = [t for who, t in transcript if who == "You"][-3:]
    return "User asked about: " + "; ".join(a[:80] for a in asks) if asks else ""
