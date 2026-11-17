from jarvis.core import autostart


class FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ, KEY_SET_VALUE, REG_SZ = 1, 2, 1

    def __init__(self):
        self.store = {}

    class _K:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def OpenKey(self, root, path, res=0, access=0):
        return self._K()

    CreateKeyEx = OpenKey

    def QueryValueEx(self, k, name):
        if name not in self.store:
            raise FileNotFoundError(name)
        return self.store[name], 1

    def SetValueEx(self, k, name, r, t, val):
        self.store[name] = val

    def DeleteValue(self, k, name):
        if name not in self.store:
            raise FileNotFoundError(name)
        del self.store[name]


def test_enable_disable(monkeypatch):
    fake = FakeWinreg()
    monkeypatch.setattr(autostart, "winreg", fake)
    assert not autostart.is_enabled()
    autostart.enable("cmd x")
    assert fake.store == {"Jarvis": "cmd x"} and autostart.is_enabled()
    autostart.disable()
    assert not autostart.is_enabled()
    autostart.disable()  # no error when already off


def test_default_command_quoted():
    cmd = autostart.default_command()
    assert cmd.startswith('"') and cmd.endswith('Jarvis.pyw"')
