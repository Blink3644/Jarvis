# Working on Jarvis with Claude Code

You've been added as a developer on Jarvis. You don't need to know how to code: Claude Code does the coding, and the rules it follows are already in this repository (`CLAUDE.md`), so your Claude picks them up automatically.

## One-time setup
1. **Accept the invite.** Open the GitHub email "invited you to collaborate on Blink3644/Jarvis" (or go to github.com/Blink3644/Jarvis/invitations) and click **Accept**.
2. **Install the tools:** [Git for Windows](https://git-scm.com/download/win), [Python 3.11+](https://www.python.org/downloads/) (tick **"Add python.exe to PATH"**), [GitHub CLI](https://cli.github.com/), and the Claude desktop app (or Claude Code).
3. **Sign in to GitHub:** open PowerShell and type `gh auth login`, then pick GitHub.com → HTTPS → "Login with a web browser".
4. **Get the code:** in PowerShell, type `cd $HOME\Documents` then `gh repo clone Blink3644/Jarvis`.
5. Open the `Jarvis` folder, double-click `setup.bat`, then `run.bat` to check Jarvis starts.
6. Open the `Jarvis` folder in Claude Code. In your first session, Claude asks which Claude plan you're on so it can watch your usage.

## Each time you work on something
- Just tell Claude what you want ("fix the wake word triggering on music", "start Minecraft mode"). It makes a branch, does the work, runs the tests and opens a **pull request**.
- Test it yourself when Claude gives you a checklist (things like speaking to Jarvis). Claude can't hear or talk.
- The owner (or another developer) reviews and merges the pull request. You never change `main` directly.
- Only the owner publishes new versions for friends, so nothing you do reaches anyone else's Jarvis until it's merged and released.

## Rules that never change
Jarvis must never get tools that run commands or code, send messages as you, buy things, type into other apps, or follow instructions found on screen or in files. Claude knows this and will refuse. Please don't push it to.

## Your own data
Your settings, notes and API keys stay in `%APPDATA%\Jarvis` on your PC. They never go into git.
