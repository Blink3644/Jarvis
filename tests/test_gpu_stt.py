import json
import struct

import numpy as np

from jarvis.core import config
from jarvis.core.ui import settings_window as sw
from jarvis.core.voice import gpu_stt


class FakeCpu:
    english_only = True

    def __init__(self):
        self.calls = 0

    def transcribe(self, audio, hints=None):
        self.calls += 1
        return "cpu text"


class FakeTok:
    def encode(self, text, add_special_tokens=False):
        return type("E", (), {"ids": list(range(len(text.split())))})()

    def decode(self, ids):
        return " gpu text"


def test_off_by_default_and_saved_from_form():
    assert config.DEFAULTS["stt_gpu"] is False
    cfg, errors = sw.build_cfg(dict(config.DEFAULTS), {"stt_gpu": True})
    assert errors == [] and cfg["stt_gpu"] is True


def test_not_ready_uses_cpu():
    cpu = FakeCpu()
    g = gpu_stt.GpuTranscriber(cpu, start=False)
    assert g.transcribe(np.ones(1600, np.int16)) == "cpu text" and cpu.calls == 1
    assert g.transcribe(np.zeros(0, np.int16)) == ""


def test_gpu_reply_and_failure_falls_back(monkeypatch):
    monkeypatch.setattr(gpu_stt, "features", lambda a: np.zeros((128, gpu_stt.FRAMES), np.float32))
    cpu = FakeCpu()
    g = gpu_stt.GpuTranscriber(cpu, start=False)
    g.proc, g.tokenizer = object(), FakeTok()
    g._ask = lambda payload: {"ids": [1, 2], "no_speech": 0.01, "logprob": -0.2}
    assert g.transcribe(np.ones(1600, np.int16), hints=["Jarvis"]) == "gpu text" and cpu.calls == 0

    def boom(payload):
        raise TimeoutError("hung")
    g._ask = boom
    assert g.transcribe(np.ones(1600, np.int16)) == "cpu text"
    assert g.failed and not g.ready
    assert g.transcribe(np.ones(1600, np.int16)) == "cpu text" and cpu.calls == 2


def test_silence_rule():
    assert gpu_stt.accept({"no_speech": 0.1, "logprob": -2})
    assert not gpu_stt.accept({"no_speech": 0.9, "logprob": -1.5})


def test_prompt_and_request_format():
    assert gpu_stt.build_prompt(FakeTok(), None) == []
    p = gpu_stt.build_prompt(FakeTok(), ["Spotify", "Discord"])
    assert p[0] == gpu_stt.START_OF_PREV and len(p) <= gpu_stt.MAX_PROMPT + 1
    feats = np.zeros((128, gpu_stt.FRAMES), np.float32)
    raw = gpu_stt.pack_request(p, feats)
    n = struct.unpack("<I", raw[:4])[0]
    head = json.loads(raw[4:4 + n])
    assert head == {"prompt": p, "n": gpu_stt.FRAMES}
    assert len(raw) == 4 + n + 128 * gpu_stt.FRAMES * 4


def test_features_shape(monkeypatch):
    monkeypatch.setattr(gpu_stt, "speech_only", lambda x: x)
    f = gpu_stt.features((np.sin(np.arange(16000)) * 3000).astype(np.int16))
    assert f.shape == (128, gpu_stt.FRAMES) and f.dtype == np.float32


def test_silence_is_not_sent_to_the_gpu():
    cpu = FakeCpu()
    g = gpu_stt.GpuTranscriber(cpu, start=False)
    g.proc, g.tokenizer = object(), FakeTok()
    g._ask = lambda payload: (_ for _ in ()).throw(AssertionError("sent silence"))
    assert g.transcribe(np.zeros(32000, np.int16)) == "" and not g.failed
