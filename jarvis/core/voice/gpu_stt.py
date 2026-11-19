"""Opt-in "Use graphics card for speech": Whisper large-v3-turbo on the GPU through DirectML.

DirectML works with AMD, Intel and NVIDIA cards on Windows 10/11. On first use Jarvis builds a
small separate venv (%APPDATA%/Jarvis/gpu_speech) with onnxruntime-directml and downloads the
model (~1.9 GB) in the background; until it's ready, and after any GPU error, the normal CPU
engine (faster-whisper) answers instead. See gpu_worker.py for why it runs in its own process.
"""
import json
import logging
import os
import struct
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np

from jarvis.core import paths

log = logging.getLogger(__name__)

ORT_PACKAGE = "onnxruntime-directml==1.24.4"   # checked on PyPI 2026-11-18 (cp311/cp312 win_amd64)
MODEL_REPO = "onnx-community/whisper-large-v3-turbo"
MODEL_FILES = ("onnx/encoder_model_fp16.onnx", "onnx/decoder_model_fp16.onnx",
               "onnx/decoder_with_past_model_fp16.onnx", "tokenizer.json")
START_OF_PREV = 50362
MAX_PROMPT = 100
FRAMES = 3000                                  # Whisper always encodes 30 s windows
REPLY_TIMEOUT = 20.0
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def home() -> Path:
    return paths.sub("gpu_speech")


def model_dir() -> Path:
    return paths.sub("models") / "whisper-large-v3-turbo-onnx"


def venv_python() -> Path:
    return home() / "venv" / "Scripts" / "python.exe"


def is_installed() -> bool:
    return (home() / "ready.json").exists() and venv_python().exists() and \
        all((model_dir() / f).exists() for f in MODEL_FILES)


def _base_python() -> str:
    base = Path(getattr(sys, "_base_executable", sys.executable))
    console = base.with_name("python.exe")     # run.bat uses pythonw; venv/pip want python.exe
    return str(console if console.exists() else base)


def install():
    """Build the worker venv and download the model. Slow (minutes); call from a thread."""
    from importlib.metadata import version
    py = venv_python()
    if not py.exists():
        log.info("GPU speech: creating its venv")
        subprocess.run([_base_python(), "-m", "venv", str(home() / "venv")], check=True,
                       creationflags=NO_WINDOW, capture_output=True)
    log.info("GPU speech: installing %s", ORT_PACKAGE)
    subprocess.run([str(py), "-m", "pip", "install", "-q", "--disable-pip-version-check",
                    ORT_PACKAGE, f"numpy=={version('numpy')}"], check=True,
                   creationflags=NO_WINDOW, capture_output=True)
    from huggingface_hub import hf_hub_download
    for f in MODEL_FILES:
        log.info("GPU speech: downloading %s", f)
        hf_hub_download(MODEL_REPO, f, local_dir=str(model_dir()))
    (home() / "ready.json").write_text(json.dumps({"ort": ORT_PACKAGE, "repo": MODEL_REPO}))
    log.info("GPU speech: installed")


def build_prompt(tokenizer, hints) -> list:
    """Hint words go in as Whisper's 'previous text' prompt, which biases it towards those spellings."""
    if not hints:
        return []
    ids = tokenizer.encode(" " + ", ".join(hints[:60]), add_special_tokens=False).ids
    return [START_OF_PREV] + ids[-MAX_PROMPT:]


def speech_only(x):
    """Keep only the speech parts (same Silero VAD the CPU engine uses), or None if there's no speech.
    Without this, Whisper turns silence or noise into words like "you"."""
    from faster_whisper.vad import collect_chunks, get_speech_timestamps
    chunks = get_speech_timestamps(x)
    if not chunks:
        return None
    parts, _ = collect_chunks(x, chunks)
    return np.concatenate(parts) if parts else None


def features(audio_int16):
    from faster_whisper.feature_extractor import FeatureExtractor
    x = speech_only(np.asarray(audio_int16, np.float32) / 32768.0)
    if x is None:
        return None
    f = FeatureExtractor(feature_size=128)(x[:16000 * 30])[:, :FRAMES]
    return np.pad(f, ((0, 0), (0, FRAMES - f.shape[1]))).astype(np.float32)


def pack_request(prompt, feats) -> bytes:
    head = json.dumps({"prompt": list(prompt), "n": int(feats.shape[1])}).encode()
    return struct.pack("<I", len(head)) + head + np.ascontiguousarray(feats, np.float32).tobytes()


def accept(reply) -> bool:
    """Same silence rule as the CPU engine: drop likely 'no speech' hallucinations."""
    return not (reply.get("no_speech", 0) > 0.6 and reply.get("logprob", 0) < -0.8)


class GpuTranscriber:
    """Drop-in for Transcriber. Anything going wrong -> the CPU Transcriber answers instead."""

    def __init__(self, cpu, start=True):
        self.cpu = cpu
        self.english_only = getattr(cpu, "english_only", True)
        self.proc = None
        self.tokenizer = None
        self.failed = False
        self.lock = threading.Lock()
        if start:
            threading.Thread(target=self._start, daemon=True, name="gpu-speech-start").start()

    @property
    def ready(self):
        return self.proc is not None and not self.failed

    def _start(self):
        try:
            if not is_installed():
                install()
            from tokenizers import Tokenizer
            self.tokenizer = Tokenizer.from_file(str(model_dir() / "tokenizer.json"))
            worker = Path(__file__).with_name("gpu_worker.py")
            proc = subprocess.Popen([str(venv_python()), "-I", str(worker), str(model_dir() / "onnx")],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
            first = json.loads(proc.stdout.readline() or b'{"error": "worker exited"}')
            if "error" in first:
                proc.kill()
                raise RuntimeError(first["error"])
            self.proc = proc
            log.info("GPU speech ready on %s", first.get("providers"))
        except Exception:
            self.failed = True
            log.warning("GPU speech couldn't start; using the CPU speech engine", exc_info=True)

    def _ask(self, payload) -> dict:
        result = {}

        def talk():
            self.proc.stdin.write(payload)
            self.proc.stdin.flush()
            result.update(json.loads(self.proc.stdout.readline() or b'{"error": "worker exited"}'))
        t = threading.Thread(target=talk, daemon=True)
        t.start()
        t.join(REPLY_TIMEOUT)
        if t.is_alive():
            raise TimeoutError("GPU speech took too long")
        if "error" in result:
            raise RuntimeError(result["error"])
        return result

    def transcribe(self, audio, hints=None) -> str:
        if audio is None or not len(audio):
            return ""
        if not self.ready:
            return self.cpu.transcribe(audio, hints=hints)
        try:
            feats = features(audio)
            if feats is None:
                return ""
            with self.lock:
                reply = self._ask(pack_request(build_prompt(self.tokenizer, hints), feats))
            return self.tokenizer.decode(reply["ids"]).strip() if accept(reply) else ""
        except Exception:
            self.failed = True
            log.warning("GPU speech failed; switching to the CPU speech engine", exc_info=True)
            try:
                self.proc.kill()
            except Exception:
                pass
            return self.cpu.transcribe(audio, hints=hints)
