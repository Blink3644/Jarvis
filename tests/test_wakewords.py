from jarvis.core import config
from jarvis.core.ui import settings_window as sw
from jarvis.core.voice import wakewords as ww


def ok(_path):
    return None


def make(tmp_path, name, data=b"fake-onnx"):
    p = tmp_path / "dl" / name
    p.parent.mkdir(exist_ok=True)
    p.write_bytes(data)
    return p


def test_nice_name():
    assert ww.nice_name("hey_computer.onnx") == "Hey Computer"
    assert ww.nice_name("hey_jarvis") == "Hey Jarvis"
    assert ww.nice_name("ok-friday v2.onnx") == "Ok Friday V2"


def test_default_choices_and_resolve():
    assert ww.choices() == {"hey_jarvis": "Hey Jarvis (built in)"}
    assert ww.resolve(None) == ww.resolve("") == ww.resolve("hey_jarvis") == "hey_jarvis"
    assert ww.resolve("missing.onnx") == "hey_jarvis"


def test_import_lists_and_resolves(tmp_path):
    name, msg = ww.import_model(make(tmp_path, "hey computer!.onnx"), check=ok)
    assert name == "hey_computer.onnx" and "Imported" in msg
    assert ww.list_custom() == ["hey_computer.onnx"]
    assert ww.choices()["hey_computer.onnx"] == "Hey Computer (trained)"
    assert ww.resolve("hey_computer.onnx") == str(ww.folder() / "hey_computer.onnx")
    # a path can't reach outside the wakewords folder
    assert ww.resolve("../hey_computer.onnx") == str(ww.folder() / "hey_computer.onnx")
    assert ww.remove_model("hey_computer.onnx") and ww.list_custom() == []


def test_import_rejects(tmp_path):
    assert "not the .tflite" in ww.import_model(make(tmp_path, "a.tflite"), check=ok)[1]
    assert ww.import_model(make(tmp_path, "a.txt"), check=ok)[0] is None
    assert ww.import_model(tmp_path / "nope.onnx", check=ok)[0] is None
    assert ww.import_model(make(tmp_path, "empty.onnx", b""), check=ok)[0] is None
    assert ww.import_model(make(tmp_path, "bad.onnx"), check=lambda p: "broken")[1] == "broken"
    assert ww.list_custom() == []


def test_real_check_rejects_garbage(tmp_path):
    assert ww.check_onnx(make(tmp_path, "junk.onnx", b"not a model")) is not None


def test_load_falls_back(tmp_path):
    made = []

    def factory(target):
        made.append(target)
        if target != "hey_jarvis":
            raise RuntimeError("broken model")
        return "engine"

    assert ww.load("hey_jarvis", factory) == ("engine", False)
    assert ww.load("gone.onnx", factory) == ("engine", True)          # file missing
    ww.import_model(make(tmp_path, "broken.onnx"), check=ok)
    assert ww.load("broken.onnx", factory) == ("engine", True)        # file broken
    assert made[-2:] == [str(ww.folder() / "broken.onnx"), "hey_jarvis"]


def test_load_custom_ok(tmp_path):
    ww.import_model(make(tmp_path, "hey_pal.onnx"), check=ok)
    assert ww.load("hey_pal.onnx", lambda t: t) == (str(ww.folder() / "hey_pal.onnx"), False)


def test_build_cfg_wake_model():
    cfg, errors = sw.build_cfg(dict(config.DEFAULTS), {"wake_model": "hey_pal.onnx"})
    assert errors == [] and cfg["wake_model"] == "hey_pal.onnx"
    cfg, errors = sw.build_cfg(dict(config.DEFAULTS), {"wake_model": ""})
    assert cfg["wake_model"] == "hey_jarvis"
    for bad in ("evil.exe", "..\\x.onnx", "a/b.onnx"):
        assert sw.build_cfg(dict(config.DEFAULTS), {"wake_model": bad})[1]
