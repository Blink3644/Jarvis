# Stage A4 (tray + settings window + on-screen panel) — morning test checklist

Do the A2 and A3 checklists first (they use `console_test.bat`). Close that window, then double-click
**`run_gui_test.bat`**. No black window appears now: Jarvis lives in the system tray (bottom-right, click the
^ arrow if you don't see it).

| # | Do | You should get |
|---|---|---|
| 1 | Start `run_gui_test.bat` | A tray icon appears, and you hear "Good morning, sir." with a glowing ring panel on screen. |
| 2 | Right-click the tray icon → **Open settings** | The settings window opens. Look through every tab. |
| 3 | General tab: press **Test key** | "Key works." |
| 4 | Voice tab: talk, then press **Test wake word** and say "Hey Jarvis" | The level bar moves while you talk; after 10 s it shows the best wake score. |
| 5 | Say "Hey Jarvis, what time is it?" | The ring changes while listening / thinking / speaking, the answer shows in the panel, the panel fades after ~8 s. |
| 6 | Say "Hey Jarvis, explain how a rainbow works in detail" | It speaks about 3 sentences, says "The rest is on screen", and the full answer stays in the panel. |
| 7 | Change something (e.g. personality to "Casual friend", home city), press **Save**, close and reopen settings | Your change is still there. Ask a question: the tone changed. |
| 8 | Theme → "Minimal", Save | The panel switches to a plain style. |
| 9 | Tray → **Mute mic**, say "Hey Jarvis" | Nothing happens. Untick it: it works again. |
| 10 | Double-click `run_gui_test.bat` again while Jarvis runs | No second Jarvis; the settings window comes to the front. |
| 11 | Play a game in borderless/windowed | No FPS drop; you can click through the panel. |
| 12 | Tick **Start with Windows**, Save, restart the PC | Jarvis starts by itself. (Untick it if you don't want that.) |
| 13 | Tray → **Quit** | The icon and panel disappear. |

## Already tested by me
- 147 automated tests. A headless run of the whole app (fake voice): tray, panel, settings opened by a
  second launch, Save, mute, greeting, usage numbers, Quit.
- "Test key": your real key → works; a fake key → "Google rejected this key." (Uses no daily quota.)

## Not done yet / not tested
- **Custom wake word training** is not built (left for later to save weekly usage). "Hey Jarvis" works as before.
- Moving the panel, how it looks on your screen, game FPS, start-with-Windows after a real restart.
- `run_gui_test.bat` is a test launcher; the final `run.bat` comes in Stage A6.
