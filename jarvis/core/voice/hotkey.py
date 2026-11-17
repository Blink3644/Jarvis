"""Push-to-talk: hold a global key. pynput uses a normal Windows keyboard hook
(no admin rights, nothing injected into games). The key still reaches the game too."""
import logging

log = logging.getLogger(__name__)


def parse_key(name: str):
    from pynput import keyboard
    name = (name or "ctrl_r").strip().lower()
    if hasattr(keyboard.Key, name):
        return getattr(keyboard.Key, name)
    if len(name) == 1:
        return keyboard.KeyCode.from_char(name)
    raise ValueError(f"unknown key {name!r}")


class PushToTalk:
    def __init__(self, key_name: str):
        self.key = parse_key(key_name)
        self.listener = None
        self.down = False

    def _matches(self, key) -> bool:
        if key == self.key:
            return True
        ch = getattr(key, "char", None)
        return bool(ch and getattr(self.key, "char", None) and ch.lower() == self.key.char)

    def start(self, on_down, on_up):
        from pynput import keyboard

        def press(key):
            if self._matches(key) and not self.down:     # ignore key auto-repeat
                self.down = True
                on_down()

        def release(key):
            if self._matches(key) and self.down:
                self.down = False
                on_up()

        self.listener = keyboard.Listener(on_press=press, on_release=release)
        self.listener.daemon = True
        self.listener.start()

    def stop(self):
        if self.listener:
            self.listener.stop()
