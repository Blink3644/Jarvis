# Stage A5 test checklist: Google Calendar and ClickUp

First, once: double-click **setup.bat** again (or run `.venv\Scripts\python -m pip install -r requirements.txt`)
so the new Google sign-in library is installed. Then restart Jarvis.

## 0. Friends who don't set these up
- [ ] Settings > **Integrations**: both "Use ..." boxes are unticked by default.
- [ ] Say "Hey Jarvis, what's on my calendar today?" -> Jarvis says it can't check your calendar (no crash).

## 1. Google Calendar
- [ ] Follow **GOOGLE_CALENDAR_SETUP.md** (the "Setup guide" button opens it). About 10 minutes.
- [ ] Settings > Integrations shows "Connected." Click **Save**.
- [ ] "Hey Jarvis, what's on my calendar today?" -> reads today's events (or says it's clear).
- [ ] "Hey Jarvis, what have I got this week?" -> events for the next 7 days.
- [ ] "Hey Jarvis, add dentist to my calendar tomorrow at 3pm" -> Jarvis reads back the day and time
      and asks you to confirm. Say "yes". -> the event appears in Google Calendar at 15:00 for an hour.
- [ ] Say "no" to a second one -> nothing is added.

## 2. ClickUp
- [ ] In ClickUp: avatar (bottom left) > **Settings** > **Apps** > **API Token** > **Generate** > **Copy**.
- [ ] Settings > Integrations: paste it in the ClickUp box > **Connect** -> "Connected to the ... workspace".
- [ ] Pick the list new tasks should go in > **Save**.
- [ ] "Hey Jarvis, what are my ClickUp tasks?" -> lists your open tasks, soonest due first.
- [ ] "Hey Jarvis, what's due today in ClickUp?"
- [ ] "Hey Jarvis, add a ClickUp task to call the bank on Friday" -> asks to confirm -> "yes" -> task appears in your chosen list, due Friday.

## 3. Daily brief
- [ ] Settings > General > Greeting at startup: **Daily brief** > Save. Quit and start Jarvis.
- [ ] Jarvis greets you, then the weather (if home city is set), today's events and ClickUp tasks due today.

## 4. Turning off
- [ ] Untick "Use Google Calendar" > Save -> calendar questions are no longer answered from your calendar.
- [ ] **Disconnect** buttons remove the saved sign-in / token.

If anything goes wrong: Settings > About > Open logs folder, and paste the end of `jarvis.log` back to Claude.
