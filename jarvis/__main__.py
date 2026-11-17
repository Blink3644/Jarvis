"""Temporary console text loop for testing (Stage A1). Run:  python -m jarvis"""
import getpass
import logging
import sys

from jarvis.core import config, logs, secrets
from jarvis.core.brain import Brain
from jarvis.core.history import HistoryStore
from jarvis.core.registry import registry

HELP = """Commands:
  /key        set or change your Gemini API key (hidden while you type)
  /usage      requests used today vs the free limit
  /personality butler|casual|direct
  /history    session|log|remember      /clearhistory  delete saved history
  /clear      forget this conversation
  /quit       exit
Anything else is sent to Jarvis."""


def load_modules():
    """Modules plug into the registry. If one fails or is missing, Jarvis still runs."""
    try:
        from jarvis.pc import basic_tools
        basic_tools.register(registry)
    except Exception:
        logging.getLogger(__name__).exception("pc module failed to load")


def ask_key():
    print("Copy your Gemini API key, then just press Enter here (I'll read it from the clipboard).")
    print("Or paste it with a RIGHT-click (Ctrl+V doesn't work in this window). It won't show.")
    typed = secrets.clean_key(getpass.getpass("Key: "))
    key = typed if secrets.looks_like_key(typed) else secrets.clean_key(secrets.clipboard_text())
    if not secrets.looks_like_key(key):
        print("That doesn't look like a Gemini key (it should be a long code starting with AIza).")
        print("Nothing saved. Copy the key again and type /key.")
        return
    secrets.set_api_key(key)
    print(f"Key saved in Windows Credential Manager (ends in ...{key[-4:]}).")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    logs.setup(get_key=secrets.get_api_key)
    cfg = config.load()
    load_modules()
    brain = Brain(cfg, registry)
    print(f"Jarvis console test. Main model: {cfg['model_main']}, backup: {cfg['model_fallback']}.")
    print("Type /help for commands.")
    if not secrets.looks_like_key(secrets.get_api_key() or ""):
        ask_key()
    try:
        while True:
            try:
                text = input("\nYou: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not text:
                continue
            cmd, _, arg = text.partition(" ")
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
            elif cmd == "/history" and arg in config.HISTORY_MODES:
                cfg["history_mode"] = arg
                config.save(cfg)
                print(f"History mode set to {arg} (applies fully after a restart).")
            elif cmd == "/clearhistory":
                HistoryStore(cfg).clear_all()
                print("Saved history deleted.")
            elif cmd.startswith("/"):
                print("Unknown command. Type /help.")
            else:
                print("Jarvis: ", end="", flush=True)
                streamed = []

                def on_text(t):
                    streamed.append(t)
                    print(t, end="", flush=True)

                def on_notice(n):
                    print(f"[{n}] ", end="", flush=True)

                reply = brain.ask(text, on_text=on_text, on_notice=on_notice)
                if not streamed or not reply.ok:
                    print(reply.text, end="")
                if reply.tools_used:
                    print(f"\n  (tools used: {', '.join(reply.tools_used)})", end="")
                print()
    finally:
        brain.close()
        print("Goodbye.")


if __name__ == "__main__":
    main()
