"""Custom wake words (Stage A4): import trained openWakeWord models and pick which one to use.

How training works (checked 2026-11-17 against github.com/dscripka/openWakeWord, v0.6.0):
- openWakeWord's own Google Colab notebook trains a model from typed text (no recordings needed).
  It runs in the browser on Google's free T4 GPU, takes under an hour, and saves
  `my_custom_model/<name>.onnx` (plus a .tflite we don't use; ONNX is what works on Windows).
- The full training notebook only runs on Linux, so on Windows the Colab notebook is the way.

cfg["wake_model"] is "hey_jarvis" (built in) or the file name of a model in %APPDATA%/Jarvis/wakewords/.
"""
import logging
import re
import shutil
from pathlib import Path

from .. import paths

log = logging.getLogger(__name__)

DEFAULT = "hey_jarvis"
COLAB_URL = "https://colab.research.google.com/drive/1q1oe2zOyZp7UsB3jJiQ1IFn8z5YfjwEb?usp=sharing"
MAX_BYTES = 50 * 1024 * 1024        # real models are well under 1 MB

TRAINING_STEPS = [
    "What you need: a Google account and about an hour (mostly waiting). No graphics card needed: "
    "training runs for free on Google's computers, in your web browser.",
    "1. Click 'Open the training page'. It opens openWakeWord's free Google Colab notebook. Sign in to Google.",
    "2. Near the top, type your wake phrase the way it sounds, for example 'hey computer'. "
    "Two or three syllables or more works best. Short single words wake by mistake.",
    "3. Click Runtime > Run all (if asked, choose 'Run anyway'). Leave the tab open. It takes 30 to 60 minutes.",
    "4. When it finishes, it downloads a file ending in .onnx (if it also gives a .tflite file, ignore that one). "
    "If nothing downloads, open the folder icon on the left, find my_custom_model, right-click the .onnx file "
    "and pick Download.",
    "5. Back here, click 'Import a trained model' and pick that .onnx file. Then choose it in the "
    "Wake word list and click Save.",
]


def folder() -> Path:
    return paths.sub("wakewords")


def nice_name(name: str) -> str:
    """'hey_computer.onnx' -> 'Hey Computer'."""
    stem = Path(name).stem if name.lower().endswith(".onnx") else name
    words = [w for w in re.split(r"[_\-\s]+", stem) if w]
    return " ".join(w.capitalize() for w in words) or stem


def list_custom() -> list[str]:
    """File names of the imported models, sorted."""
    d = folder()
    return sorted(p.name for p in d.glob("*.onnx") if p.is_file())


def choices() -> dict[str, str]:
    """{cfg value: label} for the settings menu. Built-in first."""
    out = {DEFAULT: "Hey Jarvis (built in)"}
    for name in list_custom():
        out[name] = f"{nice_name(name)} (trained)"
    return out


def resolve(value) -> str:
    """cfg value -> what WakeWord() loads: 'hey_jarvis' or the full path of an existing .onnx file."""
    if not value or value == DEFAULT:
        return DEFAULT
    name = Path(str(value)).name               # only files inside our own folder
    p = folder() / name
    if name.lower().endswith(".onnx") and p.is_file():
        return str(p)
    log.warning("wake word model %r not found; using Hey Jarvis", value)
    return DEFAULT


def check_onnx(path) -> str | None:
    """Return None if `path` looks like a working openWakeWord model, else a short reason."""
    try:
        import onnxruntime as ort
        sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        shape = sess.get_inputs()[0].shape          # openWakeWord models take [batch, 16, 96]
        if len(shape) != 3 or shape[-1] != 96:
            return "That file isn't an openWakeWord model."
    except Exception as e:
        log.warning("model check failed for %s: %s", path, e)
        return "That file couldn't be opened as a wake word model."
    return None


def safe_file_name(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_\- ]+", "", Path(name).stem).strip().replace(" ", "_")
    return (stem or "custom_wake_word")[:60] + ".onnx"


def import_model(src, check=check_onnx) -> tuple[str | None, str]:
    """Copy a trained .onnx into the wakewords folder. Returns (cfg value or None, message)."""
    src = Path(src)
    if src.suffix.lower() != ".onnx":
        msg = "Please pick the .onnx file (not the .tflite one)." if src.suffix.lower() == ".tflite" \
            else "Please pick a file ending in .onnx."
        return None, msg
    if not src.is_file():
        return None, "That file doesn't exist."
    size = src.stat().st_size
    if size == 0 or size > MAX_BYTES:
        return None, "That file doesn't look like a wake word model."
    reason = check(src) if check else None
    if reason:
        return None, reason
    name = safe_file_name(src.name)
    dest = folder() / name
    if dest.resolve() != src.resolve():
        shutil.copyfile(src, dest)
    log.info("imported wake word model %s", name)
    return name, f'Imported "{nice_name(name)}". Pick it in the Wake word list and click Save.'


def remove_model(name: str) -> bool:
    p = folder() / Path(name).name
    if p.suffix.lower() == ".onnx" and p.is_file():
        p.unlink()
        return True
    return False


def load(value, factory=None):
    """Load the chosen wake word; fall back to Hey Jarvis if the file is missing or broken.

    Returns (wake engine, fell_back: bool).
    """
    if factory is None:
        from .engines import WakeWord as factory
    target = resolve(value)
    if target != DEFAULT:
        try:
            return factory(target), False
        except Exception:
            log.exception("custom wake word %s failed to load; using Hey Jarvis", target)
    return factory(DEFAULT), bool(value) and value != DEFAULT
