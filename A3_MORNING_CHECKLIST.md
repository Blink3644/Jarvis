# Stage A3 (PC tools) — morning test checklist

Do the A2 checklist first. Then install the new parts once: open the Jarvis folder, click the address bar, type `cmd`,
press Enter, and paste `.venv\Scripts\python -m pip install -r requirements.txt`. Then start `console_test.bat`.
There's no settings window yet (that's A4), so for weather just name the city.

| # | Say "Hey Jarvis, …" | You should get |
|---|---|---|
| 1 | "open Spotify" (or any app you have) | It opens. Try "open calculator" and "open settings" too. |
| 2 | "close Spotify" | It asks first. Say "yes": Spotify closes. |
| 3 | "set the volume to 30" / "turn it up" / "mute" / "unmute" | Volume changes. |
| 4 | Play music, then "pause the music" / "next song" | Media keys work. |
| 5 | "what's the weather in London tomorrow?" | A short forecast. |
| 6 | "set a 1 minute timer for tea", then wait | After a minute it says your tea timer is done. Also try "set an alarm for 7:30 pm", "what timers do I have?", "cancel all timers". |
| 7 | "remember my router password is on the fridge". Close Jarvis, start it again, then ask "where's my router password?" | "On the fridge". The note survived the restart. |
| 8 | "take a screenshot" | A PNG appears in Pictures\Jarvis Screenshots. |
| 9 | In a game (borderless/windowed): "what am I looking at?" | It describes the game screen. If it says the picture is black, switch the game to borderless. |
| 10 | In a game: "how do I beat this boss?" (no "look") | Auto-look attaches the game window; the answer is about that game. |
| 11 | Play a song or video: "listen to this, what song is it?" | A "Listening to PC audio" line for ~15 s, then an answer. "Stop" ends it early. |
| 12 | "create a routine called gaming mode that opens Discord and Steam and sets the volume to 40" | It reads the steps back and asks. Say "yes". Then "run gaming mode". |
| 13 | "shut down the computer" | It asks first. Say **"no"**. Nothing happens. (Lock and sleep also ask or act; only test them if you want.) |

## Already tested by me
- 117 automated tests (nothing real was shut down, closed, locked or put to sleep).
- Real Gemini, with opening apps faked: weather, timer set/list/cancel, a note saved and found, volume read,
  "open calculator" picked Calculator, a routine read back → "yes" → saved, "shut down" asked first and "no" stopped it,
  "what song is playing" listened (nothing was playing, so it said so).
- On your PC: 58 Steam games and ~300 apps found; Calculator, Settings, Edge, OBS, Xbox, Spotify, Discord are matched by name.
  "Minecraft" picks the official Minecraft Launcher; to make it open Prism instead, an app alias will do it (settings, A4).
- A real screenshot of a window and a 2-second PC-audio recording, kept in memory only.

## Not tested (needs you)
Actually opening/closing apps, changing volume, media keys, a timer speaking out loud, screenshots and auto-look in a real game, listening to real audio, running a routine. Epic Games isn't installed here, so Epic games are untested.
