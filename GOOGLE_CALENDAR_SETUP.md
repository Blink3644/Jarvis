# Connecting Google Calendar to Jarvis (optional, about 10 minutes, once)

You only need this if you want Jarvis to read and add calendar events. It's free.
Google needs you to make your own small "app" so that only *your* copy of Jarvis can see *your* calendar.

## Part 1: make the Google Cloud project
1. Go to **https://console.cloud.google.com** and sign in with the Google account that has your calendar.
2. At the top, click the project picker (next to "Google Cloud") > **New project**. Name it `Jarvis` > **Create**.
   Make sure the new project is selected at the top.
3. In the search bar at the top type **Google Calendar API**, open it, and click **Enable**.

## Part 2: the sign-in screen ("consent screen")
4. In the search bar type **Google Auth Platform** and open it. Click **Get started**.
   - App name: `Jarvis`. Support email: your email. **Next**.
   - Audience: **External**. **Next**.
   - Contact email: your email. **Next**, tick the agreement, **Create**.
5. In the left menu click **Audience**. Under **Test users** click **Add users**, type your own
   Google email, and **Save**. (Only test users can sign in. Leave the app in "Testing".)

## Part 3: the credentials file
6. In the left menu click **Clients** > **Create client**.
   - Application type: **Desktop app**. Name: `Jarvis`. **Create**.
7. In the box that appears, click **Download JSON**. Save the file somewhere you'll find it
   (for example Downloads). This is your `credentials.json` (its name may be long, that's fine).
   Don't share this file.

## Part 4: connect Jarvis
8. Open Jarvis settings (tray icon > Open settings) > **Integrations** tab.
9. Click **1. Choose credentials.json** and pick the file from step 7.
10. Click **2. Connect Google account**. Your browser opens.
    - Pick your Google account.
    - Google warns **"Google hasn't verified this app"**. That's normal: it's your own app.
      Click **Continue**.
    - Tick the calendar permission and click **Continue**.
    - The page says Jarvis is connected. Close the tab.
11. Back in settings, make sure **Use Google Calendar** is ticked, then click **Save**.

## Notes
- **Sign-in expires after 7 days** while the Google app is in "Testing" mode. If Jarvis says
  "Google sign-in has expired", click **2. Connect Google account** again. To stop that, in
  Google Auth Platform > **Audience** click **Publish app** (for personal use you don't need
  Google's review; you'll still see the "hasn't verified" warning when connecting).
- To disconnect, click **Disconnect Google** in settings. You can also remove access at
  https://myaccount.google.com/permissions.
- Event titles and times go to your AI provider (e.g. Gemini) when you ask about your calendar.
