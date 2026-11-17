"""Settings in %APPDATA%/Jarvis/config.json. Missing keys fall back to defaults."""
import copy
import json
import logging

from . import paths

log = logging.getLogger(__name__)

# Model names checked against Google's model list on 2026-11-17.
# Google no longer publishes free-tier daily limits per model in its docs; these
# are editable placeholders the user should match to AI Studio's "Rate limit" page.
DEFAULTS = {
    "model_main": "gemini-3.8-flash",
    "model_fallback": "gemini-3.5-flash-lite",
    "daily_limits": {"gemini-3.8-flash": 250, "gemini-3.5-flash-lite": 1000},
    "usage_warn_percent": 80,
    "personality": "butler",          # butler | casual | direct
    "user_title": "sir",
    "reply_mode": "both",             # spoken | text | both (used from Stage A2)
    "history_mode": "session",        # session | log | remember
    "max_turns": 20,                  # exchanges kept in memory
    "remember_count": 5,              # summaries kept in "remember" mode
    "rate_limit_retries": 2,
    # ---- voice (Stage A2) ----
    "mic_device": "",                 # name of the ONLY mic Jarvis listens to ("" = Windows default mic)
    "speaker_device": "",             # "" = Windows default output
    "ptt_enabled": True,
    "ptt_key": "ctrl_r",              # pynput key name (ctrl_r, alt_r, f8 ...) or a single character
    "wake_enabled": True,
    "wake_model": "hey_jarvis",
    "wake_sensitivity": 0.5,          # 0.1 (strict) .. 0.9 (very sensitive)
    "stt_model": "base.en",           # faster-whisper model: tiny.en / base.en / small.en
    "hint_words": [],                 # extra words to help speech recognition (names, jargon)
    "tts_voice": "en-GB-RyanNeural",
    "followup_seconds": 5,            # keep listening after a reply; 0 = off
    "max_record_seconds": 15,
    "end_silence_ms": 800,
    "stop_word_enabled": True,        # say "stop" while Jarvis talks to interrupt
    "listen_sound": True,
}

PERSONALITIES = ("butler", "casual", "direct")
HISTORY_MODES = ("session", "log", "remember")
REPLY_MODES = ("spoken", "text", "both")


def _path():
    return paths.data_dir() / "config.json"


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    p = _path()
    if p.exists():
        try:
            return _merge(DEFAULTS, json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            log.exception("config.json is unreadable; using defaults (file left untouched)")
            return copy.deepcopy(DEFAULTS)
    cfg = copy.deepcopy(DEFAULTS)
    save(cfg)
    return cfg


def save(cfg: dict) -> None:
    p = _path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    tmp.replace(p)
