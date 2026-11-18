# Jarvis — current state

**Stage A1 confirmed by user (2026-11-17). Stage A2 confirmed by user (2026-11-17, via VB-Cable mic; occasional slowness = main model stalls). Stage A3 confirmed by user (2026-11-17). Stage A4 confirmed by user (2026-11-17); custom wake-word training built 2026-11-17, waiting for the user's test. Stage A5 (Calendar + ClickUp) built 2026-11-17, waiting for the user's test (A5_CHECKLIST.md). Next after confirmation: Stage A6.**

- Run: `console_test.bat` (or `.venv\Scripts\python -m jarvis`; `--text` = typing only). Tests: `.venv\Scripts\python -m pytest -q` (210 pass).
- SDK `google-genai` 3.4.0. Models (checked 2026-11-17): main `gemini-3.8-flash`, backup `gemini-3.5-flash-lite`.
  `gemini-2.5-*` deprecated from 2026-12-01. Daily quota reset = midnight Pacific. `daily_limits`: main 20/day confirmed from a real 429 (quotaValue 20, 2026-11-17); lite 1000 still a guess.
- A1 verified live: bad key message, key redacted in log, tool call, 503 -> backup model.

## Stage A5 (optional integrations) — `jarvis/pc/google_calendar.py`, `jarvis/pc/clickup.py`
- Off by default. `Tool.available` (new) hides a tool from the AI and refuses calls unless on + connected, so friends never see them.
- Calendar: REST v3 via httpx, scope `calendar.events`, user's own Desktop-app OAuth client copied to %APPDATA%/Jarvis/google_credentials.json;
  sign-in via google-auth-oauthlib 1.5.0 `run_local_server(port=0)`; refresh token in keyring `Jarvis/google_calendar_token`.
  Testing-mode Google apps expire refresh tokens after 7 days (guide says how to publish). Guide: GOOGLE_CALENDAR_SETUP.md.
- ClickUp: API v2, personal token (pk_) in keyring `Jarvis/clickup_token`; first workspace used; default list picked in settings.
- add_calendar_event / create_clickup_task ask for a yes (read back date/name). Daily brief greeting adds both (app._greet extras).
- Tests use an in-memory keyring (conftest autouse) — never the real Credential Manager. NOT verified live (no real Google/ClickUp calls).

## Stage A2 (voice) — `jarvis/core/voice/`
- assistant.py state machine: idle(wake) / listen / ptt / busy(think+speak, listens only for "stop") / followup / mic_lost.
- Wake: openwakeword 0.6.0 `hey_jarvis`, ONNX. threshold = 1 - wake_sensitivity. Paused while speaker busy + 0.5 s.
- STT: faster-whisper `base.en` local CPU int8 (free, no quota, ~0.4 s); models in %APPDATA%/Jarvis/models/whisper. Hint words -> `hotwords`.
- VAD: webrtcvad mode 3, 20 ms frames; endpoint.py (pure, tested). Follow-up needs 300 ms clear speech.
- TTS: edge-tts en-GB-RyanNeural (first call 1.4 s cold, ~0.6-0.8 s warm; warmed at startup) -> miniaudio decode -> sounddevice;
  pyttsx3 fallback (David) for 60 s after an edge failure.
- Devices: WASAPI only, auto_convert; Stereo Mix / CABLE Output / loopback names filtered unless unlocked by name in cfg `allowed_virtual_mics` (user routes processed mic via VB-Cable; console `/mic N yes`, settings "Show virtual inputs"). **Every thread that opens a
  WASAPI stream must call devices.com_init() first** (else PaErrorCode -9999 WDM-KS error).
- Mic lost -> wake paused, retry every 2 s (device re-scan max every 10 s); PTT then says the mic isn't connected
  (strict reading of "only the selected mic").
- Self-test (scratch a2_selftest.py, edge-tts clips): 2 voices wake+transcribe exactly; own voice wake 0.003; "stop" works;
  hiss wake 0.33 (<0.5), no false follow-up. STT 0.3 s, backup Gemini first chunk 1.6 s, edge first chunk 0.6-1.5 s.
- Main model 503 "high demand" can take 24 s to fail -> main gets a 10 s deadline (`main_timeout_s`, server min 10 s), any server error on main goes straight to backup (no retry),
  then backup for 60 s. NOT verified: live mic, speakers, real latency (log `latency:` lines). See A2_MORNING_CHECKLIST.md.
- Single instance: named mutex; a second launch sets event `Local\JarvisShowSettings` -> running Jarvis opens settings.

## Stage A3 (PC tools) — `jarvis/pc/`, each module has `register(reg, cfg)`, loaded by `__main__.load_modules`
- apps.py: Start Menu .lnk/.url + shell:AppsFolder (Store apps) + Steam/Epic (libraries.py) + cfg app_aliases.
  Match tiers: exact > whole word (shortest wins) > close spelling (same first letter, >=85). Else candidates.
  close_app destructive (WM_CLOSE, terminate after a wait; protected system processes refused).
- media.py (pycaw `GetSpeakers().EndpointVolume`, CoInitialize per thread), system.py (shutdown/restart fixed args, sleep
  destructive), weather.py (Open-Meteo; httpx missing -> falls back to httpx2 from google-genai), timers.py (in memory,
  `register` returns the manager; announce via registry.announce), notes.py (notes.json, scopes, keyword context),
  routines.py (create/run/delete confirmed; steps validated, no listen/look/routine tools inside routines;
  routine names+steps go into the prompt context so "start my music" runs routine "start music" (verified live).
  after an open_app step it waits for the app window (max 20 s) + 2 s before the next step; `wait` tool for explicit pauses).
- core/game_profiles.py + games.py (foreground exe vs libraries / game_list / fullscreen heuristic, context line),
  screen.py (look_at_screen + auto-look "games"; mss.MSS; black-capture and never_screenshot checks),
  listen.py (PyAudioWPatch loopback; only when the request asks to listen/hear/song; loopback gives no frames in silence).
- clipboard.py (added 2026-11-17, waiting for the user's test): copy_to_clipboard(text | last_answer) via ctypes Win32,
  sets the clipboard only (never pastes/types), max 20k chars, text never logged; brain sets registry.last_reply.
- Live with Gemini (backup model, startfile faked): 13 requests all correct incl. confirmations. Not verified: real
  open/close, volume set, media keys, spoken timer, in-game screenshot/auto-look, real audio, Epic.

## Stage A4 (UI) — `jarvis/core/ui/`, run `python -m jarvis --gui` (`run_gui_test.bat`, pythonw)
- app.py ties it together: overlay.py owns the only Tk thread; settings_window.py (customtkinter) is built on it via
  `overlay.call(fn)`; tray.py (pystray) own thread; FrontEnd forwards voice status to panel + tray.
- Settings hooks: test_key (models.get, no quota; keep the Client object alive or the SDK closes it), test_wake,
  mic_level (va.tap), reopen_mic, apply (updates the shared cfg dict in place), clear_history, check_usage, tool_names.
- Mute = `va.muted` (frames dropped, PTT ignored). Long answers in reply mode "both": speak `spoken_sentence_limit`
  (3) sentences + "The rest is on screen." autostart.py = HKCU Run "Jarvis". greeting.py off/simple/brief.
- Custom wake word: voice/wakewords.py. Training = openWakeWord's Colab notebook (free T4 GPU, <1 h, outputs
  my_custom_model/<name>.onnx; full notebook is Linux-only). Settings > Voice: "Wake word" list + "Train a custom wake
  word" guide (open page / import .onnx / remove). Import checks .onnx input shape [*,16,96] via onnxruntime, copies to
  %APPDATA%/Jarvis/wakewords/. cfg wake_model = "hey_jarvis" or a file name there; wakewords.load() falls back to
  hey_jarvis if missing/broken; Save hot-swaps the model (app._swap_wake). Smoke-tested with a copy of the built-in ONNX;
  NOT verified: a real Colab-trained model.
- NOT verified: real look on screen, dragging the panel, game FPS, autostart reboot.

## Voice fixes (2026-11-17, merged to main)
- Log evidence: edge-tts `NoAudioReceived` -> pyttsx3 fallback; the NEXT reply was never spoken (pyttsx3
  runAndWait hangs on a reused engine) -> Jarvis stuck "busy" until restart.
- Fixed: edge-tts tried twice per sentence; unspeakable chunks (```, **, ---) skipped; offline voice runs in a
  child process with a 20 s timeout (real test: 3 calls ok, 0.35 s each); online voice retried after 30 s.
- STT: base.en and small.en both transcribe a clean synthetic clip perfectly (30% volume + noise): base.en 0.4 s,
  small.en 1.2 s, so mis-hearings likely come from the VB-Cable signal chain. Settings now offer stt_model.
- Flaky test_wake_command_reply_followup_then_idle: GC ran leftover Tk font __del__ (dead Tk thread, ~1 s per
  call) inside the voice-turn thread. Test-only; fixed with gc.collect() in make().
- Mis-hearing: faster-whisper vad_filter is NOT the cause (same text on/off; in the log it removed 0 s in 47/60
  clips, otherwise trailing silence). Very quiet input reproduces the errors ("via git" -> "by a GIC" at 3% volume).
  "heard" log lines now include the peak level (e.g. "level -12 dB"); below about -30 dB means the VB-Cable chain is
  too quiet. Next: read those levels from the user's next session before changing any code.

## Multiple AI providers (2026-11-17)
- `core/providers.py`: adapters behind `generate(key, model, system, contents, tools) -> list[types.Part]`
  and `list_models(key)`. History stays in google-genai `types.Content`; adapters convert it.
  `OpenAIAdapter` = Chat Completions at any base URL (OpenAI, Groq, OpenRouter, Mistral, Ollama, custom);
  `AnthropicAdapter` = Messages API (`anthropic-version: 2023-06-01`). Plain urllib, no new packages.
  Non-Gemini replies are not streamed (whole reply at once), so first-word latency is higher there.
- Config `providers` = ordered list; Gemini entry uses `model_main`/`model_fallback`, others have `models`.
  Keys: keyring user `provider:<id>` (`secrets.get/set/delete_provider_key`). Usage/limits keyed `<id>/<model>`.
- `Brain._request` walks slots in order: skips disabled, blocked, quota-full, missing/bad key (moves on),
  and providers lacking image/audio input for this question (one-off notice, no "switch").
  Notices: Gemini backup = old BACKUP_NOTICE; other provider = "Switching to X for a bit." once.
  Gemini-only setups behave exactly as before (all old tests unchanged).
- Settings: "AI providers" tab (add preset, key, Test key = lists models, models comma list, Up/Down,
  on/off, Remove deletes key). Capabilities per preset: images on for OpenAI/Anthropic/OpenRouter,
  audio only Gemini (flags `images`/`audio` in config if a model differs).
- API formats taken from the providers' long-standing public docs; NOT verified live this session
  (no keys, budget). First real test per provider = the user's checklist.
- Not done: streaming for non-Gemini providers; Test key doesn't fill the models field automatically.
