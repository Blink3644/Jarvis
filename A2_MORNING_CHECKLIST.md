# Stage A2 (voice) — morning test checklist

Start it: double-click **`console_test.bat`**. Wait about 10 seconds after it says it's listening, while the voice warms up.

| # | Do / say | You should get |
|---|---|---|
| 1 | Type `/mics` | A list of your real microphones (no "Stereo Mix"). If the one marked default isn't your headset, type `/mic 2` (its number). |
| 2 | Type `/meter`, then talk | A bar that moves when you speak. |
| 3 | Type `/testwake`, then say "Hey Jarvis" 3 times | "best score" above the threshold (0.5). |
| 4 | Say **"Hey Jarvis, what time is it?"** | A short beep, then a spoken answer in a British voice within about 3 seconds. |
| 5 | Within 5 s of the reply ending, say **"And what's the date?"** (no "Hey Jarvis") | It answers. |
| 6 | Hold **Right Ctrl**, say **"Tell me a joke"**, let go | It answers. |
| 7 | Say **"Hey Jarvis, tell me a long story"**, then say **"stop"** while it talks | It goes quiet. Try again, pressing **Right Ctrl** instead: that should also stop it. |
| 8 | Play some game audio or a video with speech for a few minutes | Jarvis never wakes up by itself. |
| 9 | (Optional) Turn Wi-Fi off and say "Hey Jarvis, what time is it?" | A plainer offline Windows voice saying it can't reach the internet. |
| 10 | (Optional) Unplug the mic, then plug it back in | A "microphone missing" message, then it carries on after you plug it back in. |

If anything is slow, open `%APPDATA%\Jarvis\logs\jarvis.log` and paste me the lines that start with **`latency:`**.

## Already tested by me (no human needed)
- Synthesized voices (US male and UK female) saying "Hey Jarvis, what time is it?" / "…what's the weather like?":
  both woke Jarvis and were transcribed exactly. Speech-to-text takes about 0.3 s.
- Jarvis's own voice saying "Jarvis" while it talks: ignored. The wake score is only 0.003 even without the pause.
- A synthesized "stop" while it was thinking: it stopped, and nothing was spoken.
- Loud hiss during follow-up listening: no false question. The wake score on hiss was 0.33, below the 0.5 trigger.
- Real Gemini: the backup model replies in about 1.6 s. Tonight the main model was overloaded and took up to 24 s to
  fail. Jarvis now gives up on it after 10 s (Google's minimum) and uses the backup for the next minute.

## Not tested (needs you)
Your real voice and room, sound actually playing from your speakers, the Right Ctrl key, real-world speed, and unplugging the mic.

**Expected speed:** about 0.8 s to notice you stopped talking, plus about 0.3 s speech-to-text, about 1–2 s for Gemini
and about 0.6 s for the voice, so roughly 3 s. It will be slower when Google's main model is overloaded.
