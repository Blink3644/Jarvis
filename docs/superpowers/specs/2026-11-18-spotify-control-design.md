# Spotify control — design (agreed 2026-11-18, not built yet)

## Why
The user wants to ask Jarvis for **specific songs and playlists** by voice ("play my Gym playlist",
"play Bohemian Rhapsody"). Today Jarvis only has generic media keys (`jarvis/pc/media.py`:
play_pause / next / previous / stop) with no idea what is playing. The user has Spotify Premium.

## Approaches considered
1. **Spotify Web API (chosen).** The only official way to start a specific track/playlist/album.
2. Windows media session (GSMTC): no setup, but cannot choose what plays. Rejected for this goal.
3. Unofficial tokens / web-player `sp_dc` cookie / librespot: against Spotify ToS, account-ban risk. Rejected.
4. Sending keystrokes to the Spotify app: forbidden by CLAUDE.md safety rules. Rejected.

## Spotify rules (checked 2026-11-18, developer.spotify.com Feb 2026 migration guide)
- Development Mode app owner needs active Premium; app stops if it lapses.
- Max 5 authorized users per Client ID (owner adds each user's Spotify email in the dashboard).
- Search still works but `limit` max 10 (default 5).
- Use `GET /me/playlists` (not `/users/{id}/playlists`); playlist contents via `/playlists/{id}/items`
  (`/tracks` removed; only for playlists the user owns/collaborates on).
- Liking: `PUT /me/library` with Spotify URIs (`PUT /me/tracks` removed).
- Player endpoints (state, devices, play, queue, shuffle, repeat) not listed as removed — **verify
  against the live reference before coding.**
- Redirect URI: verify current rule (believed: loopback IP literal `http://127.0.0.1:<port>/callback`,
  `localhost` not allowed).

## Decided
- **Auth:** Authorization Code + PKCE, no client secret. One-time browser login from settings
  ("Connect" button); a tiny local loopback server catches the callback. Refresh token in keyring
  (`Jarvis` / `spotify_refresh_token`), never logged.
- **Client ID:** the user's own Client ID is **built in as the default** (in the shipped zip, so up to
  4 friends the user adds in the dashboard can just click Connect). Settings has an **optional
  override** field for anyone using their own Client ID (friend #6+, or by choice). Client ID is
  not a secret.
- **Voice commands / tools:**
  - play a song ("play Bohemian Rhapsody")
  - play one of *my* playlists by name, fuzzy-matched over `/me/playlists` (misheard names OK)
  - play an artist ("play some Daft Punk") and an album ("play the album Discovery")
  - queue a song next
  - like the current song
  - what's playing (read-only)
  - shuffle on/off, repeat off/track/context
- **No active device:** open Spotify on this PC (existing `open_app` path), wait for its device to
  appear, then transfer/start playback there.
- **Fallback:** existing `media_control` media keys stay unchanged and keep working when Spotify is
  not connected.
- Off/hidden for anyone who hasn't connected (same pattern as `clickup.py`: `available=` callback).
- Plain error messages (no Premium, not connected, offline, 429) like `ClickUpError`.

## Safety
- "What's playing" = `side_effect=False`. Playback/queue/like/shuffle are side effects but harmless:
  no confirmation needed beyond the existing tainted-turn rule in `registry.py`.
- No tool may change the Client ID or disconnect by voice (settings window only); add to
  `tests/test_safety.py`.

## Left to the builder
- Module layout: suggest `jarvis/pc/spotify.py` (client + tools) modelled on `jarvis/pc/clickup.py`
  (injectable http client for tests), settings section on the Integrations tab.
- Unit tests with fake HTTP responses (no real network); PKCE helper and fuzzy playlist match tested.
- README: click-by-click developer-app setup (create app, redirect URI, tick Web API, add friends'
  emails under User Management) and a note that friends depend on the owner's Premium.
- User must supply their Client ID before the build can be tested for real.
