"""Wake word (openwakeword, ONNX), voice activity (webrtcvad) and speech-to-text (faster-whisper).

Speech-to-text choice: faster-whisper running locally on the CPU.
- free and unlimited: it doesn't use any of the Gemini free-tier quota
- the audio stays on the PC; only the typed-out text goes to Gemini
- fast enough: about 0.4 s for a short command with the "base.en" model
- supports hint words (names, mod items) through its `hotwords` option
"""
import logging
import os
from pathlib import Path

import numpy as np

from .. import paths

log = logging.getLogger(__name__)


class WakeWord:
    def __init__(self, model: str = "hey_jarvis"):
        import openwakeword
        from openwakeword.model import Model
        self.name = model
        if not Path(model).suffix:                       # built-in model name
            try:
                openwakeword.utils.download_models(model_names=[model])
            except Exception:
                log.warning("wake word model download failed (may already be present)", exc_info=True)
            self.key = model
        else:                                            # a custom .onnx file (Stage A4)
            self.key = Path(model).stem
        self.model = Model(wakeword_models=[model], inference_framework="onnx")

    def score(self, frame: np.ndarray) -> float:
        scores = self.model.predict(frame)
        return float(scores.get(self.key) or max(scores.values(), default=0.0))

    def reset(self):
        self.model.reset()


class Vad:
    def __init__(self, mode: int = 3):
        import webrtcvad
        self.vad = webrtcvad.Vad(mode)

    def is_speech(self, frame20ms: np.ndarray) -> bool:
        if len(frame20ms) != 320:
            return False
        return self.vad.is_speech(frame20ms.astype(np.int16).tobytes(), 16000)

    def reset(self):
        pass


class Transcriber:
    def __init__(self, model: str = "base.en"):
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        from faster_whisper import WhisperModel
        root = paths.sub("models") / "whisper"
        try:
            self.model = WhisperModel(model, device="cpu", compute_type="int8",
                                      download_root=str(root))
        except Exception:
            log.warning("loading %s from the internet failed; trying the local copy", model, exc_info=True)
            self.model = WhisperModel(model, device="cpu", compute_type="int8",
                                      download_root=str(root), local_files_only=True)
        self.english_only = model.endswith(".en")

    def transcribe(self, audio: np.ndarray, hints=None) -> str:
        if audio is None or not len(audio):
            return ""
        x = audio.astype(np.float32) / 32768.0
        segments, _ = self.model.transcribe(
            x, language="en", beam_size=3, vad_filter=True,
            condition_on_previous_text=False,
            hotwords=(" ".join(hints[:60]) if hints else None),
        )
        words = []
        for seg in segments:
            if seg.no_speech_prob > 0.6 and seg.avg_logprob < -0.8:
                continue
            words.append(seg.text.strip())
        return " ".join(w for w in words if w).strip()
