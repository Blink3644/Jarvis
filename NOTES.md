# Jarvis — current state

**Stage A1 built, waiting for the user's test** (commit `Stage A1: ...`). Stage A2 not started.

- Run: `console_test.bat` (or `.venv\Scripts\python -m jarvis`). Tests: `.venv\Scripts\python -m pytest -q` (22 pass).
- SDK `google-genai` 3.4.0. Models (checked 2026-11-17): main `gemini-3.8-flash`, backup `gemini-3.5-flash-lite`.
  `gemini-2.5-*` deprecated from 2026-12-01. Daily quota reset = midnight Pacific.
- Google's docs no longer list free-tier RPD per model; `daily_limits` in config.json are placeholders (250 / 1000).
  User should copy real numbers from AI Studio's rate-limit page.
- Verified live: bad key -> friendly message; key redacted in log.
- Not yet verified: a real answer + tool call (needs the user's API key).
- Open for later stages: single-instance check and offline TTS come with A2/A4; the console loop is temporary.
