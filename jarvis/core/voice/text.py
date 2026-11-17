"""Text helpers for voice: split streamed replies into speakable sentences, spot "stop" commands."""
import re

_END = re.compile(r"([.!?]+[\"')\]]*)(\s+)")
_MD = re.compile(r"[*_`#>|~]+")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_WAKE_PREFIX = re.compile(r"^\W*(hey|hi|hay|okay|ok)?\W*(jarvis|jervis|travis)\b\W*", re.I)


def speakable(text: str) -> str:
    """Strip markdown so TTS doesn't read symbols aloud."""
    text = _LINK.sub(r"\1", text)
    text = _MD.sub("", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.M)
    return re.sub(r"\s+", " ", text).strip()


class SentenceSplitter:
    """Feed streamed text chunks; get back complete sentences as soon as they end.

    The first sentence is released early (at a comma) if it runs long, so speech starts fast.
    """

    def __init__(self, first_max_chars: int = 120):
        self.buf = ""
        self.first = True
        self.first_max = first_max_chars

    def feed(self, chunk: str) -> list[str]:
        self.buf += chunk.replace("\n", " ")
        out = []
        while True:
            m = _END.search(self.buf)
            if not m:
                break
            cut = m.end(1)
            sentence = self.buf[:cut]
            # don't split decimals / abbreviations like "3.5" or "e.g." (no space follows those)
            self.buf = self.buf[m.end():]
            out.append(sentence)
        if self.first and not out and len(self.buf) > self.first_max:
            i = self.buf.rfind(", ", 0, self.first_max)
            if i > 20:
                out.append(self.buf[: i + 1])
                self.buf = self.buf[i + 2:]
        out = [s for s in (speakable(x) for x in out) if s]
        if out:
            self.first = False
        return out

    def flush(self) -> list[str]:
        rest = speakable(self.buf)
        self.buf = ""
        return [rest] if rest else []


STOP_WORDS = {"stop", "stop it", "stop talking", "be quiet", "quiet", "shut up", "shush",
              "that's enough", "thats enough", "enough", "cancel", "never mind", "nevermind",
              "stop listening", "stop recording", "that's enough listening"}


def is_stop_command(text: str) -> bool:
    t = strip_wake_words(text).lower()
    t = re.sub(r"[^a-z' ]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"^(please |okay |ok )", "", t)
    t = re.sub(r"( please| now| thanks| thank you)$", "", t)
    return t in STOP_WORDS


def strip_wake_words(text: str) -> str:
    """'Hey Jarvis, what time is it?' -> 'what time is it?'"""
    return _WAKE_PREFIX.sub("", text.strip()).strip()
