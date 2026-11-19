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
    "daily_limits": {"gemini-3.8-flash": 20, "gemini-3.5-flash-lite": 1000},  # main 20 = real free-tier 429 (2026-11-17); lite unverified
    "usage_warn_percent": 80,
    # AI providers in priority order (Gemini uses model_main/model_fallback; others list their own
    # models). Keys live in Windows Credential Manager, never here. Daily limits for other
    # providers are keyed "<provider id>/<model>".
    "providers": [{"id": "gemini", "kind": "gemini", "name": "Google Gemini", "enabled": True}],
    "personality": "butler",          # butler | casual | direct
    "user_title": "sir",
    "reply_mode": "both",             # spoken | text | both (used from Stage A2)
    "history_mode": "session",        # session | log | remember
    "max_turns": 20,                  # exchanges kept in memory
    "remember_count": 5,              # summaries kept in "remember" mode
    "learn_about_me": False,          # opt-in: learn lasting facts from what the user says (pc/profile.py)
    "rate_limit_retries": 2,
    # ---- voice (Stage A2) ----
    "mic_device": "",                 # name of the ONLY mic Jarvis listens to ("" = Windows default mic)
    "allowed_virtual_mics": [],       # virtual inputs (e.g. VB-Cable) the user unlocked: must carry only their voice
    "speaker_device": "",             # "" = Windows default output
    "ptt_enabled": True,
    "ptt_key": "ctrl_r",              # pynput key name (ctrl_r, alt_r, f8 ...) or a single character
    "wake_enabled": True,
    "wake_model": "hey_jarvis",
    "wake_sensitivity": 0.5,          # 0.1 (strict) .. 0.9 (very sensitive)
    "stt_model": "base.en",           # faster-whisper model: one of STT_MODELS
    "stt_gpu": False,                 # opt-in: Whisper large-v3-turbo on the graphics card (DirectML)
    "keep_debug_clips": False,        # opt-in: keep the last 20 voice commands (WAV + text) for tuning
    "main_timeout_s": 10,
    "smart_tools": True,              # send only the tools whose topic fits the question (fewer tokens)
    "home_city": "",                  # for weather, e.g. "Leeds"
    "app_aliases": {},                # spoken name -> app name or path, e.g. {"browser": "Google Chrome"}
    "game_list": [],                  # extra exe names that count as games, e.g. ["eldenring.exe"]
    "auto_look": "games",             # "ask" | "games" | "always": attach a screenshot to questions
    "never_screenshot": ["1password", "bitwarden", "keepass", "lastpass", "dashlane",
                         "bank", "paypal"],   # window title / exe words that are never captured
    "listen_default_s": 15,
    "listen_max_s": 120,              # hard maximum for listen_to_audio (seconds)
    "hint_words": [],                 # extra words to help speech recognition (names, jargon)
    "tts_voice": "en-GB-RyanNeural",
    "followup_seconds": 5,            # keep listening after a reply; 0 = off
    "max_record_seconds": 15,
    "end_silence_ms": 800,
    "stop_word_enabled": True,        # say "stop" while Jarvis talks to interrupt
    "listen_sound": True,
    # ---- Gemini Live voice mode (Stage A8, experimental) ----
    "live_mode": False,               # off by default; normal mode is always the fallback
    "live_model": "gemini-3.8-live",  # checked on Google's models page 2026-11-17
    "live_idle_seconds": 20,          # close the Live session after this much quiet
    # ---- tray, settings window, on-screen panel (Stage A4) ----
    "theme": "hud",                   # hud | minimal
    "overlay_hide_s": 8,              # panel hides this long after a reply
    "overlay_pos": None,              # [x, y] once the user has moved it
    "start_with_windows": False,
    "startup_greeting": "simple",     # off | simple | brief
    "spoken_sentence_limit": 3,
    # ---- optional integrations (Stage A5), off by default; secrets live in keyring ----
    "calendar_enabled": False,
    "clickup_enabled": False,
    "clickup_team_id": "",
    "clickup_list_id": "",            # default list for new tasks
    "clickup_list_name": "",
    "spotify_client_id": "",          # empty = use the built-in Client ID
    # ---- "clip that": OBS Replay Buffer via OBS's WebSocket server (password in keyring) ----
    "obs_port": 4455,
    "spoken_sentence_limit_placeholder": None,       # reply mode "both": speak this many sentences, rest on screen (0 = all)
}

PERSONALITIES = ("butler", "casual", "direct")
HISTORY_MODES = ("session", "log", "remember")
REPLY_MODES = ("spoken", "text", "both")
STT_MODELS = ("base.en", "small.en")


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
