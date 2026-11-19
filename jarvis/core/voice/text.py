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


# "Thanks Jarvis", "that's all", "goodbye": the user is done, so no follow-up listening.
# Only a whole short remark counts; "thanks, now turn it up" is a new request.
_CLOSING = re.compile(
    r"^(?:(?:ok|okay|alright|right|great|perfect|cool|nice|brilliant|awesome|lovely|good|"
    r"excellent|cheers|no|nope) )*"
    r"(?:(?:thanks|thank you|thank you very much|thanks a lot|thanks so much|many thanks|ta|cheers|"
    r"much appreciated|appreciate it|that's all|that'll be all|that will be all|that's it|"
    r"that's everything|that's all i needed|that's all for now|that'll do|that will do|"
    r"nothing else|i'm good|i'm done|all good|all done|we're done|we're good|"
    r"goodbye|good bye|bye|bye bye|see you|see ya|later|good night|night|"
    r"perfect|great|cool|awesome|brilliant|lovely|excellent)"
    r"(?: (?:that's all|that's it|that's everything|for now|thanks|thank you|bye|goodbye))*)$")
_CLOSING_TAIL = re.compile(r"( (jarvis|jervis|travis|sir|mate|buddy|man|then|again|so much|very much))+$")


def is_closing_remark(text: str) -> bool:
    """True when the whole utterance just wraps things up ('Thanks Jarvis', 'ok that's all')."""
    t = strip_wake_words(text or "").lower().replace("’", "'")
    t = re.sub(r"[^a-z' ]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = _CLOSING_TAIL.sub("", t).strip()
    if not t or len(t.split()) > 8:
        return False
    # bare praise ("great", "perfect") is too ambiguous on its own: it needs a thanks/bye with it
    if t in {"perfect", "great", "cool", "awesome", "brilliant", "lovely", "excellent", "good",
             "ok", "okay", "right", "alright", "no", "nope", "nice", "later", "night"}:
        return False
    return bool(_CLOSING.match(t))


def strip_wake_words(text: str) -> str:
    """'Hey Jarvis, what time is it?' -> 'what time is it?'"""
    return _WAKE_PREFIX.sub("", text.strip()).strip()


_WAKE_ANYWHERE = re.compile(r"\b(?:(?:hey|hi|hay|okay|ok)\W+)?(jarvis|jervis|travis)\b\W*", re.I)
WAKE_LOOKBACK_WORDS = 8   # the pre-roll is ~1.6 s, so the wake phrase is among the first few words


def strip_wake_command(text: str) -> str:
    """For wake-word turns, whose audio starts ~1.6 s before the wake word fired.

    Drops the wake phrase and anything said just before it:
    'so anyway. Hey Jarvis, skip this song' -> 'skip this song'.
    Only the first few words are searched, so a 'Jarvis' later in the command is kept.
    """
    t = (text or "").strip()
    m = _WAKE_ANYWHERE.search(t)
    if m and len(t[:m.start()].split()) <= WAKE_LOOKBACK_WORDS:
        return t[m.end():].strip()
    return strip_wake_words(t)
