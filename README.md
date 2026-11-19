# Jarvis: a voice assistant for your Windows PC

Say **"Hey Jarvis"**, or hold **Right Ctrl**, and ask for something. Jarvis can:

- answer questions
- open and close apps
- change the volume and control music
- set timers
- tell you the weather
- look at your screen and help with a game
- remember notes
- run routines like "gaming mode"
- save a gameplay clip with OBS ("Jarvis, clip that")

Windows 10/11 only. It's free to run, but you need your own free AI key (see step 1).

---

## Install (about 15 minutes, once)

### 1. Get a free Gemini key

1. Go to **https://aistudio.google.com/apikey** and sign in with a Google account.
2. Click **Create API key** and copy the key. It's a long line of letters and numbers.

Keep the key private, like a password. You'll paste it into Jarvis in step 4.

### 2. Download Jarvis

1. On the Jarvis GitHub page, open **Releases** (on the right).
2. Download **Jarvis-x.y.z.zip**.
3. Right-click the zip, choose **Extract All…**, and pick a folder you'll keep, e.g. `Documents\Jarvis`.
   Don't run it from inside the zip.

### 3. Run setup

Double-click **setup.bat** in that folder. It needs **Python 3.12**:

- If Python 3.12 is missing, setup offers to install it. Press **Y**, and accept Microsoft's terms if you agree.
  - If that doesn't work, setup tells you how to install it from python.org. Remember to tick **"Add python.exe to PATH"**.
- The first run downloads about 1 GB (speech recognition, the wake word, voices) and takes 5–15 minutes.
- When it says **All done!**, Jarvis is in your Start Menu.

### 4. First start

1. Start **Jarvis** from the Start Menu, or double-click **run.bat**.
2. A small icon appears in the tray, near the clock. You may need to click **^** to see it.
3. Right-click the icon and choose **Open settings**. Then:
   - **General** tab:
     - Paste your key, then click **Test key**.
     - Set what Jarvis calls you ("sir" by default) and how replies appear (spoken, text, or both).
   - **Voice** tab: pick your **microphone**. Jarvis only ever listens to this one device, so game sound and Discord can't trigger it.
   - **PC** tab: set your home city for the weather.
4. Click **Save**. Then say **"Hey Jarvis, what time is it?"**

Windows SmartScreen or your antivirus may warn about a `.bat` file from the internet. That's normal for small free projects. Click **More info → Run anyway** only if you got Jarvis from someone you trust.

### Updating to a new version

**The easy way (version 0.1.3 and newer):** Jarvis checks for a new version once a day when it starts, and tells you when there is one. To update:

1. Right-click the Jarvis tray icon → **Open settings** → **About** tab.
2. Click **Check for updates**. If there's a new version, you'll see what changed and an **Update to ...** button.
3. Click it, then **Yes**. Jarvis downloads the update, checks it, closes, and a small window shows the progress (it can take a few minutes if new parts need installing). Jarvis then starts again by itself.

Nothing is ever installed until you click Update. Your settings, notes and keys are kept. The old version is saved in a `_previous` folder, and if the new version won't start, the old one is put back automatically. A log of the update is in `%APPDATA%\Jarvis\logs\update.log`.

**The manual way (older versions, or if the button doesn't work):**

1. Download the new Jarvis zip and unzip it somewhere **other than** your Jarvis folder (Downloads is fine).
2. Open the unzipped folder and double-click **update.bat**.
3. It finds your Jarvis, shows the old and new version numbers, and asks **Y/N**. Type `Y` and press Enter.

It closes Jarvis, swaps in the new files and starts Jarvis again. Your settings, notes and keys are kept. The old version is saved in a `_previous` folder inside your Jarvis folder. If it can't find Jarvis, drag your Jarvis folder (the one with `run.bat`) into the window and press Enter. You can delete the unzipped update folder afterwards.

---

## Things to try

| Say | What happens |
|---|---|
| "Hey Jarvis, open Spotify" | Opens an app from your Start Menu, Steam or Epic |
| "Hey Jarvis, play my Gym playlist" / "play some Daft Punk" | Plays it on Spotify (needs Spotify Premium, connected in Settings > Integrations). Also: "queue ...", "like this song", "what's playing?", shuffle, repeat |
| "Set the volume to 30" / "next song" | Volume and media control |
| "Set a timer for 10 minutes" | Speaks when the timer is done |
| "Remind me at 6 to stretch" / "remind me in 20 minutes to check the oven" | Jarvis says it at that time. Reminders are saved, so they survive a restart (if Jarvis was off, it tells you what you missed). "Cancel the stretch reminder" removes one |
| (nothing: Jarvis speaks first) | Turn on **Morning brief** (Settings → General) to hear the Daily brief at a set time each day, and **Heads-up** (Settings → Integrations, needs Google Calendar) to hear "Heads up: Standup starts in 10 minutes" |
| "How do I beat this boss?" (in a game) | Looks at the game window and answers |
| "Listen to this, what song is it?" | Records your PC's sound for 15 s and answers |
| "Remember my locker code is 1234" | Saves a note on your PC. "What did I ask you to remember?" reads them back |
| "Clip that" | Saves the last stretch of gameplay with OBS. Settings → Integrations → OBS explains the setup |
| "Stop" | Stops Jarvis talking. Pressing Right Ctrl does the same |

Jarvis always asks first before it closes apps, shuts down or restarts.

---

## Privacy: please read

What you say to Jarvis is turned into text **on your PC**. The text of your question is then sent to the AI service(s) you added in settings (Google Gemini by default; OpenAI, Anthropic, Groq and others if you add them). Some extra things can be sent along with it:

- **Notes** that match your question.
- **Screenshots:** in games by default, or only when you ask (Settings → PC → "Let Jarvis look at your screen").
- **Recordings of your PC's sound:** only when you say "listen to this".
- **Calendar or ClickUp items:** only if you set those up.
- **Spotify:** if you connect it, Jarvis uses your Spotify login to search and control playback. Spotify only lets Premium accounts that the Jarvis app owner has added (5 people max) use the built-in connection; anyone else can paste their own Client ID in settings.

**Free tiers may use your data to improve the provider's products.** On Google's free tier, people at Google may read it. Don't share anything you wouldn't want seen.

Screenshots are never taken in the background. Password managers and banking apps are on a "never screenshot" list, which you can edit.

"Listen to this" records everything your PC plays, **including other people's voices on Discord or calls**. Please use it considerately.

Your settings, notes and keys stay on your PC:

- Settings and notes: `%APPDATA%\Jarvis`
- Keys: Windows Credential Manager

---

## "I've hit my limit"

Free AI keys allow only a certain number of questions per minute and per day.

- When the main model runs out, Jarvis switches to a lighter backup model and says so.
- When both run out, it says **"I've hit my limit"**. Wait a minute, or until the daily reset.
- The settings window shows how many questions you've used today. You can add other AI services as backups on the **AI providers** tab.

---

## Troubleshooting

- **Jarvis doesn't hear me:** Settings → Voice → check the microphone. The level bar should move when you talk. Click **Test wake word**.
- **It wakes up by itself / doesn't wake up:** Settings → Voice → change the wake-word **sensitivity**.
- **Screenshot questions show a black picture:** switch the game to **borderless** or **windowed** mode.
- **Nothing happens when I start it:** look for the tray icon first (Jarvis only runs once). Then run `console_test.bat`, which shows a console with error messages.
- **Something else:** send the end of the log file to whoever gave you Jarvis. Open it by pasting `%APPDATA%\Jarvis\logs` into the File Explorer address bar, then open `jarvis.log`. Your key is never written to the log.

**To uninstall:**

1. Quit Jarvis from the tray.
2. Delete the Jarvis folder.
3. Delete `%APPDATA%\Jarvis`.
4. Remove the Start Menu entry.

---

## For the owner: publishing a new version

1. Raise `__version__` in `jarvis/__init__.py` and commit.
2. Double-click **make_zip.bat**. It makes `dist\Jarvis-<version>.zip` from the last commit. Your notes and keys aren't in it.
3. On GitHub: **Releases → Draft a new release**. Make a tag that matches the new version exactly, with a `v` in front (version `0.2.0` → tag `v0.2.0`). Write what changed (friends see this text in settings), attach the zip, and click **Publish release**. Don't tick "Set as a pre-release" unless you want friends' Jarvis to ignore it.
4. Friends' Jarvis finds the release by itself (once a day, or when they click **Check for updates**). Friends on 0.1.2 or older update once by hand with update.bat.

Every release needs a higher version number than the last. If the tag says a different version than the files inside, Jarvis refuses to install it and says so, so don't forget step 1. Optional: Jarvis also checks a SHA-256 checksum when GitHub shows one for the zip (it does for new uploads) or when you attach a `Jarvis-<version>.zip.sha256` file.
