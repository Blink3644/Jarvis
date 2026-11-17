from jarvis.core import autostart, shortcuts


class FakeShortcut:
    def __init__(self, path, saved):
        self.path, self.saved = path, saved
        self.TargetPath = self.Arguments = self.WorkingDirectory = self.Description = self.IconLocation = ""

    def Save(self):
        open(self.path, "w").close()
        self.saved[self.path] = self


class FakeShell:
    def __init__(self):
        self.saved = {}

    def CreateShortcut(self, path):
        return FakeShortcut(path, self.saved)


def test_create_and_remove(tmp_path):
    shell = FakeShell()
    lnk = shortcuts.create(tmp_path, icon=tmp_path / "j.ico", shell=shell)
    assert lnk.name == "Jarvis.lnk" and shortcuts.exists(tmp_path)
    sc = shell.saved[str(lnk)]
    assert sc.TargetPath.lower().endswith(("pythonw.exe", "python.exe"))
    assert "Jarvis.pyw" in sc.Arguments
    assert sc.WorkingDirectory == str(shortcuts.ROOT)
    assert sc.IconLocation.endswith("j.ico,0")
    shortcuts.remove(tmp_path)
    assert not shortcuts.exists(tmp_path)
    shortcuts.remove(tmp_path)          # removing twice is fine


def test_launcher_exists_and_autostart_uses_it():
    assert shortcuts.LAUNCHER.exists()
    cmd = autostart.default_command()
    assert "Jarvis.pyw" in cmd and "-m jarvis" not in cmd


def test_icon_file(tmp_path):
    ico = shortcuts.write_icon(tmp_path / "jarvis.ico")
    assert ico and ico.stat().st_size > 0
