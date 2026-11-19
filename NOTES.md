# Jarvis — current state

**Stage A1 confirmed by user (2026-11-17). Stage A2 confirmed by user (2026-11-17, via VB-Cable mic; occasional slowness = main model stalls). Stage A3 confirmed by user (2026-11-17). Stage A4 confirmed by user (2026-11-17); custom wake-word training built 2026-11-17, waiting for the user's test. Stage A5 (Calendar + ClickUp) built 2026-11-17, waiting for the user's test (A5_CHECKLIST.md). Stage A8 (Gemini Live, experimental, off by default) built 2026-11-17, waiting for the user's test. Stage A6 (setup.bat, run.bat, make_zip.bat, README) built 2026-11-18; clean install tested (238 pass in fresh copy); pushed to https://github.com/Blink3644/Jarvis (public, `origin`, branch main) 2026-11-18. A7 (in-app updater) confirmed by user 2026-11-18 (real release). "Clip that" (real OBS) and Groq provider confirmed by user 2026-11-18.**

- Run: `console_test.bat` (or `.venv\Scripts\python -m jarvis`; `--text` = typing only). Tests: `.venv\Scripts\python -m pytest -q` (308 pass).
- SDK `google-genai` 3.4.0. Models (checked 2026-11-17): main `gemini-3.8-flash`, backup `gemini-3.5-flash-lite`.
  `gemini-2.5-*` deprecated from 2026-12-01. Daily quota reset = midnight Pacific. `daily_limits`: main 20/day confirmed from a real 429 (quotaValue 20, 2026-11-17); lite 1000 still a guess.
- A1 verified live: bad key message, key redacted in log, tool call, 503 -> backup model.

## Handover (2026-11-18)
- Code is on GitHub: https://github.com/Blink3644/Jarvis (`origin`, main tracks origin/main). Push after each commit.
- `dist\Jarvis-0.1.0.zip` built; the user publishes Release `v0.1.0` with it attached (README bottom). Check the
  repo's Releases page shows it before starting A7.
- Waiting for the user's tests: A5 checklist, A8 Live mode, wake-word mishearing fix, custom wake word (don't ask).
- A7 updater: `jarvis/core/updater.py` checks Blink3644/Jarvis releases (once a day at startup + About tab button), verifies, then runs the installed `update.ps1 -Auto` (rollback if the new version exits within 20 s). Uses GitHub's source zip if no zip is attached. Refuses releases whose files' __version__ != tag. Dev copies (.git) can't self-update. Stopgap built 2026-11-18: update.bat/update.ps1 in the zip (manual unzip + double-click, tested on a fake install). v0.1.1 = AQ. Gemini key fix + update.bat.
- Weekly usage cap 92%.
- Polish pass (branch `polish-settings-messages`, 2026-11-18): settings wording (Remove / Move up / Move down /
  "Run without asking" / provider "Understands:" / Minecraft "not built yet"); limit and server errors are
  provider-neutral when non-Gemini providers are set up ("All my AI providers are out of requests..."); the
  system prompt now asks for answer-first replies, spoken-style numbers, no URLs read aloud and short action
  confirmations (`jarvis/core/personality.py`). Still to polish: README/first run, overlay/tray, dead code.

## "Clip that" (2026-11-18) — `jarvis/pc/obs_clip.py`, confirmed with real OBS 2026-11-18
- Tool `clip_that`: OBS Replay Buffer via obs-websocket v5 (OBS 28+, port `obs_port` 4455, password in keyring
  `Jarvis/obs_password`). Protocol checked against obs-websocket protocol.md. No key presses (user chose OBS over
  Game Bar/NVIDIA hotkeys, which would break the no-keystrokes rule). Buffer off -> Jarvis starts it and says so.
  Settings > Integrations > OBS: port, password, Test OBS. "clip that" skips auto-look.

## Safety rules (2026-11-17) — `jarvis/core/registry.py`, `tests/test_safety.py`
- `Tool.side_effect` (default True), `Tool.brings_data`, `Tool.gate`. `registry.tainted` is set when a data tool runs or an auto-look screenshot is attached; then side-effect tools need confirm_action in a later turn.
- Secret notes: `jarvis/core/secrets.py` `looks_secret` (lenient: 'password', 'PIN', card/key shapes) -> note gets `secret`+`title`; AI sees the title only (`notes.shown_text`); settings list shows 'SECRET (local only)'; heard-text log lines are redacted. The 'Save a readable log' history file still holds what was said (user's own choice). Privacy line is at the top of the Brain/key settings tab. Not done: search-grounding taint isn't detected.
- **For the A6 README privacy note:** questions, transcripts, notes sent in prompts, Calendar/ClickUp data, and screenshots/PC audio (only when asked) go to whichever AI providers the user added (Gemini, OpenAI, Anthropic, Groq...), not just Google. Free tiers may use data for training.

## Stage A8 (Gemini Live, experimental) — `jarvis/core/voice/live.py`, state LIVE in assistant.py
- Off by default (`live_mode`). Settings > Voice: toggle, `live_model` (default `gemini-3.8-live`, GA per Google models page), `live_idle_seconds` (20).
- **Live only works on API version `v1alpha`** (`live_api_version`): v1beta and v1 give HTTP 404 at the websocket handshake (checked 2026-11-17, all model names).
- Smoke test 2026-11-17: connection + our config (system prompt, all tools, Google Search, transcriptions) accepted, then 1011 "You exceeded your current quota" -> this free key may have no Live quota for gemini-3.8-live. If so, try `gemini-3.1-flash-live-preview` in settings. NOT verified: real audio reply, latency, tool call by voice.
- Flow: wake/PTT -> LIVE: mic frames streamed (16 kHz PCM), Gemini audio (24 kHz) -> speaker.play_now. Wake word is off in LIVE (only runs in IDLE). Gemini's own VAD handles turns and barge-in (`interrupted` -> speaker.interrupt). PTT press in LIVE = cut Jarvis off. "stop" in the input transcript ends the session. Idle `live_idle_seconds` -> close, back to wake word. Mute closes it.
- Tools: same Registry; tool results sent with send_tool_response, `_media` images sent as realtime video frames. Confirmations: the input transcript is passed to registry.begin_turn at turn end / before a tool call, so a spoken "yes" works (if the transcript arrives late the model just asks again).
- Fallback: error before Gemini answered -> notice ("I've hit the Live limit, so I'm using normal mode") and the audio heard so far is replayed into the normal endpointer -> whisper -> brain (any provider). Live paused 10 min after a quota error, 2 min after other errors. Error after an answer -> "Live mode dropped out".
- Known limits: with speakers (no headphones) Gemini may hear itself and cut itself off; Live requests are not counted in the usage meter.

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
- GPU speech (opt-in `stt_gpu`, "Use graphics card for speech", branch stt-accuracy): `gpu_stt.py` + `gpu_worker.py`.
  Whisper large-v3-turbo ONNX fp16 (HF onnx-community, ~1.9 GB) on onnxruntime-directml 1.24.4 (AMD/Intel/NVIDIA).
  Own venv in %APPDATA%/Jarvis/gpu_speech, built + downloaded in the background on first use (CPU engine meanwhile),
  because faster-whisper's `onnxruntime` overwrites the DirectML package. Worker talks over stdin/stdout; main process
  does Silero VAD (silence never reaches the GPU, else Whisper says "you"), log-mel features and token decoding.
  DirectML gotchas: use the split decoders (merged one = garbage), ORT_ENABLE_BASIC (ALL crashes), float32 I/O.
  Any error/timeout (20 s) -> CPU engine for the rest of the session. Real run (RX 7900 XT): load 2 s, 0.3-0.6 s/command.
  Not built: Groq cloud STT option (owner approved, skipped to save usage).
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
- Mis-hearing (fixed 2026-11-18, waiting for the user's test): real levels were fine (-4 to -14 dB). Cause: wake turns
  started with only 0.24 s before the wake fired = the tail of "Jarvis", which Whisper heard as "Just ..." / "Please ..." /
  "service, ..." / "this, ..." (reproduced exactly with edge-tts clips + the real wake model). Now wake turns keep 1.6 s
  (`WAKE_PREROLL`), "Jarvis" is a hint word, and `text.strip_wake_command` cuts the wake phrase and any chatter before it
  (first 8 words only, so a later "Jarvis" stays). 24/24 synthetic clips clean (3 voices). PTT keeps 0.24 s. Live mode unchanged.
  Peak level is still in "heard" lines; below about -30 dB = too quiet.
  Owner's real test (2026-11-18): base.en heard all 15 real commands exactly after this fix. Remaining problems were
  behaviour (Spotify volume, playlists, repeated confirm), not hearing. small.en: too slow, no better.

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
- Per-minute limits: the provider's "try again in Xs" hint (`retry_after`) is used; <= 3 s is waited out,
  otherwise the slot is blocked for that long (max 60 s). Groq free tier = 8,000 tokens/min per model and
  a Jarvis request is ~3,500 tokens (~3,000 = the 40 tool declarations, ~490 = instructions).
- Prompt caching (all providers in `Brain._call_slot`; Live mode unchanged): `personality.split_prompt`
  gives fixed instructions (no clock) + a `[Context]` note (time, routines, notes, game) that
  `_with_context_note` puts before the latest question, for that request only (not saved in history). So
  instructions + tools + earlier turns stay identical and can be cached. Groq: cached tokens don't count
  toward its per-minute limit (docs). Gemini free tier limits requests, not tokens, so there it's only a
  possible speed-up. Log line "<model> tokens: prompt N (cached M)". Checked live 2026-11-18: Groq
  sometimes reports 512 cached tokens, often 0; a small help at best.
- Smart tool list (`smart_tools`, default on, checkbox on the settings main page): each PC module has a
  `TOPICS` list (topic, words regex or function, tool names, optional send_all), registered by
  `__main__.load_modules`. `Registry.pick_tools` sends only matching topics + topics used in the last 3
  turns + tools in no topic (confirm_action, end_conversation). No topic matched, or "create a routine"
  (send_all) → every tool. Cuts tool text from ~3,000 to ~200–900 tokens for most questions. Live mode
  always gets every tool. Tests: `tests/test_tool_picking.py`.
- Earlier tool calls/results for tools not sent this request, and calls made by non-Gemini providers
  (tracked by id, `Brain._foreign_calls`), are sent as text notes (`_calls_as_text`); otherwise the API
  rejects unknown tool names / Gemini 400s with "missing a thought_signature".
- Settings: "AI providers" tab (add preset, key, Test key = lists models, models comma list, Up/Down,
  on/off, Remove deletes key). Capabilities per preset: images on for OpenAI/Anthropic/OpenRouter,
  audio only Gemini (flags `images`/`audio` in config if a model differs).
- Groq confirmed working by user 2026-11-18. OpenAI/Anthropic/others: formats from public docs, NOT verified live.
- Not done: streaming for non-Gemini providers; Test key doesn't fill the models field automatically.

## Team setup (2026-11-18)
- Shared rules: `CLAUDE.md` (developers use branches + PRs; only the owner bumps versions and releases). Owner-only rules: `CLAUDE.local.md` (gitignored, owner's PC). Human guide for developers: `CONTRIBUTING.md`.
- Zip friends get `packaging/CLAUDE_zip.md` as `Jarvis/CLAUDE.md` (added by `make_zip.bat`); CONTRIBUTING and packaging/ are export-ignored.

