"""Jarvis entry point. Run:  python -m jarvis --gui   (tray + on-screen panel + settings window)
Console test front end:  python -m jarvis   (add --text for typing only)

Voice runs in the background; you can still type questions and /commands here.
The tray icon and settings window replace this in Stage A4.
"""
import getpass
import logging
import sys
import threading
import time

from jarvis.core import config, instance, logs, secrets
from jarvis.core.brain import Brain
from jarvis.core.history import HistoryStore
from jarvis.core.registry import registry

log = logging.getLogger("jarvis")

HELP = """Commands:
  /key        set or change your Gemini API key (hidden while you type)
  /usage      requests used today vs the free limit
  /personality butler|casual|direct     /reply spoken|text|both
  /history    session|log|remember      /clearhistory  delete saved history
  /clear      forget this conversation
 Voice:
  /mics, /mic N         list microphones / choose the one Jarvis listens to
  /speakers, /speaker N list speakers / choose where Jarvis talks
  /meter      show your mic level for 5 seconds
  /testwake   say "Hey Jarvis" a few times in the next 10 seconds to test it
  /wake on|off          /sensitivity 0.1-0.9 (higher = wakes more easily)
  /ptt on|off           /followup SECONDS (0 = off)    /stopword on|off
  /voice NAME  e.g. en-GB-RyanNeural
  /quit       exit
Anything else is sent to Jarvis."""

STATUS_TEXT = {"listen": "[listening...]", "ptt": "[listening (key held)...]",
               "followup": "[still listening...]", "followup_heard": "", "busy": "",
               "idle": "", "mic_lost": "[microphone missing]"}


class ConsoleUI:
    """What the voice loop shows. Stage A4's tray/overlay will implement the same methods."""

    def __init__(self):
        self.lock = threading.Lock()
        self.mid_reply = False

    def _print(self, text, end="\n"):
        with self.lock:
            print(text, end=end, flush=True)

    def status(self, state):
        msg = STATUS_TEXT.get(state, "")
        if msg:
            self._print(f"\n{msg}")

    def user_text(self, text):
        self._print(f"\nYou (voice): {text}\nJarvis: ", end="")
        self.mid_reply = True

    def reply_text(self, chunk, final=False):
        if not self.mid_reply and chunk:
            self._print("\nJarvis: ", end="")
            self.mid_reply = True
        if chunk:
            self._print(chunk, end="")
        if final and self.mid_reply:
            self._print("")
            self.mid_reply = False

    def notice(self, msg):
        self._print(f"\n[{msg}]")


PC_MODULES = ("apps", "media", "system", "weather", "timers", "notes", "routines",
              "games", "screen", "listen", "clipboard", "obs_clip", "google_calendar", "clickup")



def _device_lists(kind, cfg):
    """(usable devices, locked virtual inputs). Outputs have no virtual list."""
    from jarvis.core.voice import devices
    if kind != "input":
        return devices.list_outputs(), []
    devs = devices.list_inputs(cfg.get("allowed_virtual_mics", ()))
    names = {d["name"] for d in devs}
    return devs, [d for d in devices.list_virtual_inputs() if d["name"] not in names]


def load_modules(cfg):
    """Modules plug into the registry. If one fails or is missing, Jarvis still runs."""
    import importlib
    try:
        from jarvis.pc import basic_tools
        basic_tools.register(registry)
    except Exception:
        log.exception("pc module basic_tools failed to load")
    for name in PC_MODULES:
        try:
            importlib.import_module(f"jarvis.pc.{name}").register(registry, cfg)
        except Exception:
            log.exception("pc module %s failed to load", name)


def wire_front_end(ui, va):
    """Let tools talk to the user outside a normal reply (timer done, PC-audio banner)."""
    def announce(text):
        ui.notice(text)
        if va:
            va.speaker.say(text)

    def ui_event(name, data):
        if name == "pc_audio":
            ui.notice("Listening to PC audio..." if data.get("on") else "Stopped listening to PC audio.")
    registry.announce = announce
    registry.ui_event = ui_event


def ask_key():
    print("Copy your Gemini API key, then just press Enter here (I'll read it from the clipboard).")
    print("Or paste it with a RIGHT-click (Ctrl+V doesn't work in this window). It won't show.")
    typed = secrets.clean_key(getpass.getpass("Key: "))
    key = typed if secrets.looks_like_key(typed) else secrets.clean_key(secrets.clipboard_text())
    if not secrets.looks_like_key(key):
        print("That doesn't look like a Gemini key (it should be a long code starting with AIza or AQ.).")
        print("Nothing saved. Copy the key again and type /key.")
        return
    secrets.set_api_key(key)
    print(f"Key saved in Windows Credential Manager (ends in ...{key[-4:]}).")


def start_voice(cfg, brain, ui):
    """Load the voice parts (takes a few seconds the first time: models download)."""
    from jarvis.core.voice.assistant import VoiceAssistant
    from jarvis.core.voice import wakewords
    from jarvis.core.voice.engines import Transcriber, Vad
    from jarvis.core.voice.hotkey import PushToTalk
    from jarvis.core.voice.mic import Mic
    from jarvis.core.voice.speaker import SOUNDS, Speaker

    print("Loading voice (the first start downloads about 150 MB of speech models)...")
    t = time.monotonic()
    ptt = None
    if cfg.get("ptt_enabled", True):
        try:
            ptt = PushToTalk(cfg.get("ptt_key", "ctrl_r"))
        except ValueError:
            print(f"Unknown push-to-talk key {cfg.get('ptt_key')!r}; push-to-talk is off.")
    wake, fell_back = wakewords.load(cfg.get("wake_model", wakewords.DEFAULT))
    if fell_back:
        print("Your custom wake word couldn't be loaded, so I'm using \"Hey Jarvis\" for now.")
    from .core.voice.live import make_session_factory
    va = VoiceAssistant(cfg, brain, registry, mic=Mic(cfg), wake=wake,
                        stt=Transcriber(cfg.get("stt_model", "base.en")), speaker=Speaker(cfg),
                        vad=Vad(3), ui=ui, ptt=ptt, sounds=SOUNDS,
                        live_factory=make_session_factory(cfg, registry, brain))
    va.start()
    log.info("voice ready in %.1fs", time.monotonic() - t)
    time.sleep(0.3)
    mic = va.mic.name or "(none found)"
    ways = []
    if cfg.get("wake_enabled", True):
        ways.append(f'say "{wakewords.nice_name(wakewords.DEFAULT if fell_back else cfg.get("wake_model") or wakewords.DEFAULT)}"')
    if ptt:
        ways.append(f"hold {cfg.get('ptt_key', 'ctrl_r').replace('_r', ' (right)').replace('ctrl', 'Ctrl')}")
    print(f"Voice ready. Microphone: {mic}. To talk: {' or '.join(ways) or 'voice input is off'}.")
    return va


def voice_command(cmd, arg, cfg, va):
    from jarvis.core.voice import devices
    from jarvis.core.voice.mic import level

    def onoff(key, label):
        if arg in ("on", "off"):
            cfg[key] = arg == "on"
            config.save(cfg)
            print(f"{label} {arg}.")
        else:
            print(f"Use: {cmd} on  or  {cmd} off")

    if cmd in ("/mics", "/speakers"):
        kind = "input" if cmd == "/mics" else "output"
        chosen = cfg["mic_device" if kind == "input" else "speaker_device"]
        devs, virtual = _device_lists(kind, cfg)
        for i, d in enumerate(devs + virtual, 1):
            mark = "  <- selected" if d["name"] == chosen or (not chosen and d["default"]) else ""
            vmark = "  [virtual: only if it carries just your voice]" if d in virtual else ""
            print(f"  {i}. {d['name']}{' (Windows default)' if d['default'] else ''}{vmark}{mark}")
        print(f"Choose with {cmd[:-1]} NUMBER")
    elif cmd in ("/mic", "/speaker"):
        kind = "input" if cmd == "/mic" else "output"
        devs, virtual = _device_lists(kind, cfg)
        num, _, confirm = arg.partition(" ")
        try:
            d = (devs + virtual)[int(num) - 1]
        except (ValueError, IndexError):
            print(f"Type {cmd}s to see the list, then {cmd} NUMBER.")
            return
        if d in virtual:
            if confirm.strip().lower() != "yes":
                print(f"{d['name']} is a virtual input. Use it only if it carries just your own voice "
                      f"(game, music or Discord sound in it could wake Jarvis).\n"
                      f"To use it anyway, type: /mic {num} yes")
                return
            cfg["allowed_virtual_mics"] = [*cfg.get("allowed_virtual_mics", []), d["name"]]
        cfg["mic_device" if kind == "input" else "speaker_device"] = d["name"]
        config.save(cfg)
        print(f"{'Microphone' if kind == 'input' else 'Speaker'} set to {d['name']}.")
        if va and kind == "input":
            va.events.put("reopen_mic")
    elif cmd == "/meter":
        if not va:
            print("Voice isn't running.")
            return
        peak = [0.0]

        def tap(frame):
            peak[0] = max(peak[0], level(frame))
        print("Speak now. Level (5 s):")
        va.tap = tap
        for _ in range(25):
            time.sleep(0.2)
            n = int(peak[0] * 40)
            print(f"\r  [{'#' * n}{' ' * (40 - n)}]", end="", flush=True)
            peak[0] = 0.0
        va.tap = None
        print("\n(If the bar barely moves while you talk, pick another mic with /mics.)")
    elif cmd == "/testwake":
        if not va:
            print("Voice isn't running.")
            return
        print('Say "Hey Jarvis" now (10 seconds)...')
        va.test_wake(10)
        time.sleep(10.3)
        print(f"Best score {va.wake_test_best:.2f}; it needs {va.wake_threshold():.2f}. "
              "If good tries stay below that, raise /sensitivity.")
    elif cmd == "/wake":
        onoff("wake_enabled", "Wake word")
    elif cmd == "/ptt":
        onoff("ptt_enabled", "Push-to-talk (applies after a restart):")
    elif cmd == "/stopword":
        onoff("stop_word_enabled", '"Stop" to interrupt')
    elif cmd == "/sensitivity":
        try:
            v = float(arg)
            assert 0.05 <= v <= 0.95
        except (ValueError, AssertionError):
            print("Use a number from 0.1 (strict) to 0.9 (very sensitive). Default 0.5.")
            return
        cfg["wake_sensitivity"] = v
        config.save(cfg)
        print(f"Wake sensitivity {v}.")
    elif cmd == "/followup":
        try:
            cfg["followup_seconds"] = max(0, min(30, int(arg)))
        except ValueError:
            print("Use /followup SECONDS, e.g. /followup 5 (0 turns it off).")
            return
        config.save(cfg)
        print(f"Follow-up listening: {cfg['followup_seconds']} s.")
    elif cmd == "/voice":
        if not arg:
            print(f"Current voice: {cfg['tts_voice']}. Example: /voice en-GB-RyanNeural")
            return
        cfg["tts_voice"] = arg
        config.save(cfg)
        print(f"Voice set to {arg}.")
    else:
        return False
    return True


def run_gui(cfg, brain):
    """Tray icon + on-screen panel + settings window (Stage A4)."""
    import os
    if sys.stdout is None:                  # pythonw has no console
        sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")
    from jarvis.core.ui.app import App

    def voice(cfg, brain, ui):
        va = start_voice(cfg, brain, ui)
        wire_front_end(ui, va)
        return va
    App(cfg, brain, registry, voice).run()


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    logs.setup(get_key=secrets.get_api_key)
    gui = "--gui" in sys.argv
    if not instance.acquire():
        instance.signal_existing()          # the running Jarvis opens its settings window
        if not gui:
            print("Jarvis is already running in another window.")
            time.sleep(3)
        return
    cfg = config.load()
    load_modules(cfg)
    brain = Brain(cfg, registry)
    if gui:
        return run_gui(cfg, brain)
    print(f"Jarvis console test. Main model: {cfg['model_main']}, backup: {cfg['model_fallback']}.")
    print("Type /help for commands.")
    if not secrets.looks_like_key(secrets.get_api_key() or ""):
        ask_key()
    ui = ConsoleUI()
    va = None
    if "--text" not in sys.argv:
        try:
            va = start_voice(cfg, brain, ui)
        except Exception:
            log.exception("voice failed to start")
            print("Voice couldn't start (details in the log). You can still type.")
    wire_front_end(ui, va)
    try:
        while True:
            try:
                text = input("\nYou: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not text:
                continue
            cmd, _, arg = text.partition(" ")
            arg = arg.strip()
            if cmd == "/quit":
                break
            if cmd == "/help":
                print(HELP)
            elif cmd == "/key":
                ask_key()
            elif cmd == "/usage":
                for model, used, lim in brain.usage.summary():
                    print(f"  {model}: {used} / {lim or '?'} today")
            elif cmd == "/clear":
                brain.clear_conversation()
                print("Conversation forgotten.")
            elif cmd == "/personality" and arg in config.PERSONALITIES:
                cfg["personality"] = arg
                config.save(cfg)
                print(f"Personality set to {arg}.")
            elif cmd == "/reply" and arg in config.REPLY_MODES:
                cfg["reply_mode"] = arg
                config.save(cfg)
                print(f"Replies: {arg}.")
            elif cmd == "/history" and arg in config.HISTORY_MODES:
                cfg["history_mode"] = arg
                config.save(cfg)
                print(f"History mode set to {arg} (applies fully after a restart).")
            elif cmd == "/clearhistory":
                HistoryStore(cfg).clear_all()
                print("Saved history deleted.")
            elif cmd.startswith("/"):
                if not voice_command(cmd, arg, cfg, va):
                    print("Unknown command. Type /help.")
            else:
                ask_typed(text, brain, cfg, va)
    finally:
        if va:
            va.stop()
        brain.close()
        print("Goodbye.")


def ask_typed(text, brain, cfg, va):
    """Typed questions are answered in text, and also spoken if replies are set to spoken/both."""
    from jarvis.core.voice.text import SentenceSplitter
    speak = va is not None and cfg.get("reply_mode", "both") != "text"
    if va:
        va.speaker.interrupt()
    splitter = SentenceSplitter()
    print("Jarvis: ", end="", flush=True)
    streamed = []

    def on_text(t):
        streamed.append(t)
        print(t, end="", flush=True)
        if speak:
            for s in splitter.feed(t):
                va.speaker.say(s)

    def on_notice(n):
        print(f"[{n}] ", end="", flush=True)
        if speak:
            va.speaker.say(n)

    with (va.turn_lock if va else threading.Lock()):
        reply = brain.ask(text, on_text=on_text, on_notice=on_notice)
    if not streamed or not reply.ok:
        print(reply.text, end="")
        if speak:
            va.speaker.say(reply.text)
    elif speak:
        for s in splitter.flush():
            va.speaker.say(s)
    if reply.tools_used:
        print(f"\n  (tools used: {', '.join(reply.tools_used)})", end="")
    print()


if __name__ == "__main__":
    main()
