import json

import numpy as np

from jarvis.core import config
from jarvis.core.ui import settings_window as sw
from jarvis.core.voice import debug_clips


def test_off_by_default():
    assert config.DEFAULTS["keep_debug_clips"] is False


def test_save_keeps_only_newest(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    audio = (np.sin(np.arange(1600)) * 1000).astype(np.int16)
    for i in range(5):
        p = debug_clips.save(audio, f"clip {i}", "wake", keep=3)
        p.rename(p.with_name(f"{i:02d}.wav"))
        p.with_suffix(".json").rename(p.with_name(f"{i:02d}.json"))
    debug_clips.prune(3)
    wavs = sorted(x.name for x in debug_clips.folder().glob("*.wav"))
    assert wavs == ["02.wav", "03.wav", "04.wav"]
    assert json.loads((debug_clips.folder() / "04.json").read_text())["heard"] == "clip 4"
    assert debug_clips.clear() == 3
    assert not list(debug_clips.folder().iterdir())


def test_empty_audio_not_saved(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    assert debug_clips.save(np.zeros(0, np.int16), "") is None


def test_setting_saved_from_form():
    cfg, errors = sw.build_cfg(dict(config.DEFAULTS), {"keep_debug_clips": True})
    assert errors == [] and cfg["keep_debug_clips"] is True
