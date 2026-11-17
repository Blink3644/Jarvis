# Jarvis — current state

**Stage A1 confirmed by user (2026-11-17). Stage A2 built and self-tested (2026-11-17); waiting for the user's voice test.**

- Run: `console_test.bat` (or `.venv\Scripts\python -m jarvis`; `--text` = typing only). Tests: `.venv\Scripts\python -m pytest -q` (39 pass).
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
- Verified here: real models on a recorded "Hey Jarvis, what time is it?" -> wake 0.999 -> "What time is it?";
  real mic opens in the app. NOT verified: live mic speech, real latency (log prints `latency:` lines), stop word with speakers.
- Single instance: named mutex (focus-existing comes with the A4 UI). Settings are console /commands until A4.
