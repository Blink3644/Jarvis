# Jarvis — current state

**Stage A1 confirmed by user (2026-11-17). Stages A2 and A3 built and self-tested overnight (2026-11-17); waiting for the user's tests (A2_/A3_MORNING_CHECKLIST.md).**

- Run: `console_test.bat` (or `.venv\Scripts\python -m jarvis`; `--text` = typing only). Tests: `.venv\Scripts\python -m pytest -q` (117 pass).
- SDK `google-genai` 3.4.0. Models (checked 2026-11-17): main `gemini-3.8-flash`, backup `gemini-3.5-flash-lite`.
  `gemini-2.5-*` deprecated from 2026-12-01. Daily quota reset = midnight Pacific. `daily_limits` are placeholders (250/1000).
- A1 verified live: bad key message, key redacted in log, tool call, 503 -> backup model.

## Stage A2 (voice) — `jarvis/core/voice/`
- assistant.py state machine: idle(wake) / listen / ptt / busy(think+speak, listens only for "stop") / followup / mic_lost.
- Wake: openwakeword 0.6.0 `hey_jarvis`, ONNX. threshold = 1 - wake_sensitivity. Paused while speaker busy + 0.5 s.
- STT: faster-whisper `base.en` local CPU int8 (free, no quota, ~0.4 s); models in %APPDATA%/Jarvis/models/whisper. Hint words -> `hotwords`.
- VAD: webrtcvad mode 3, 20 ms frames; endpoint.py (pure, tested). Follow-up needs 300 ms clear speech.
- TTS: edge-tts en-GB-RyanNeural (first call 1.4 s cold, ~0.6-0.8 s warm; warmed at startup) -> miniaudio decode -> sounddevice;
  pyttsx3 fallback (David) for 60 s after an edge failure.
- Devices: WASAPI only, auto_convert; Stereo Mix / CABLE Output / loopback names filtered. **Every thread that opens a
  WASAPI stream must call devices.com_init() first** (else PaErrorCode -9999 WDM-KS error).
- Mic lost -> wake paused, retry every 2 s (device re-scan max every 10 s); PTT then says the mic isn't connected
  (strict reading of "only the selected mic").
- Self-test (scratch a2_selftest.py, edge-tts clips): 2 voices wake+transcribe exactly; own voice wake 0.003; "stop" works;
  hiss wake 0.33 (<0.5), no false follow-up. STT 0.3 s, backup Gemini first chunk 1.6 s, edge first chunk 0.6-1.5 s.
- Main model 503 "high demand" can take 24 s to fail -> main gets a 10 s deadline (`main_timeout_s`, server min 10 s),
  then backup for 60 s. NOT verified: live mic, speakers, real latency (log `latency:` lines). See A2_MORNING_CHECKLIST.md.
- Single instance: named mutex (focus-existing comes with the A4 UI). Settings are console /commands until A4.

## Stage A3 (PC tools) — `jarvis/pc/`, each module has `register(reg, cfg)`, loaded by `__main__.load_modules`
- apps.py: Start Menu .lnk/.url + shell:AppsFolder (Store apps) + Steam/Epic (libraries.py) + cfg app_aliases.
  Match tiers: exact > whole word (shortest wins) > close spelling (same first letter, >=85). Else candidates.
  close_app destructive (WM_CLOSE, terminate after a wait; protected system processes refused).
- media.py (pycaw `GetSpeakers().EndpointVolume`, CoInitialize per thread), system.py (shutdown/restart fixed args, sleep
  destructive), weather.py (Open-Meteo; httpx missing -> falls back to httpx2 from google-genai), timers.py (in memory,
  `register` returns the manager; announce via registry.announce), notes.py (notes.json, scopes, keyword context),
  routines.py (create/run/delete confirmed; steps validated, no listen/look/routine tools inside routines).
- core/game_profiles.py + games.py (foreground exe vs libraries / game_list / fullscreen heuristic, context line),
  screen.py (look_at_screen + auto-look "games"; mss.MSS; black-capture and never_screenshot checks),
  listen.py (PyAudioWPatch loopback; only when the request asks to listen/hear/song; loopback gives no frames in silence).
- Live with Gemini (backup model, startfile faked): 13 requests all correct incl. confirmations. Not verified: real
  open/close, volume set, media keys, spoken timer, in-game screenshot/auto-look, real audio, Epic.
