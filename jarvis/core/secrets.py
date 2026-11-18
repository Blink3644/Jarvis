"""The Gemini API key lives in Windows Credential Manager via keyring, never in a file."""
import keyring

SERVICE = "Jarvis"
USER = "gemini_api_key"


def clean_key(raw: str) -> str:
    """Drop whitespace and control characters (Ctrl+V in a console types \\x16)."""
    return "".join(c for c in (raw or "") if c.isprintable() and not c.isspace())


def looks_like_key(key: str) -> bool:
    # Old Gemini keys are "AIza...", new ones (late 2025+) are "AQ.Ab..." with a dot,
    # so allow dots too. The "Test key" button is the real check.
    return len(key) >= 30 and all(c.isascii() and (c.isalnum() or c in "-_.") for c in key)


def clipboard_text() -> str:
    try:
        import tkinter
        root = tkinter.Tk()
        root.withdraw()
        try:
            return root.clipboard_get()
        finally:
            root.destroy()
    except Exception:
        return ""


def get_api_key():
    try:
        return keyring.get_password(SERVICE, USER)
    except Exception:
        return None


def set_api_key(key: str) -> None:
    keyring.set_password(SERVICE, USER, key.strip())


def delete_api_key() -> None:
    try:
        keyring.delete_password(SERVICE, USER)
    except Exception:
        pass


# ---- keys for other AI providers (one keyring entry per provider id) ----
def get_provider_key(pid: str):
    if pid == "gemini":
        return get_api_key()
    try:
        return keyring.get_password(SERVICE, f"provider:{pid}")
    except Exception:
        return None


def set_provider_key(pid: str, key: str) -> None:
    if pid == "gemini":
        set_api_key(key)
    else:
        keyring.set_password(SERVICE, f"provider:{pid}", key.strip())


def delete_provider_key(pid: str) -> None:
    try:
        keyring.delete_password(SERVICE, "gemini_api_key" if pid == "gemini" else f"provider:{pid}")
    except Exception:
        pass


# ---- spotting secrets in what the user says (notes, logs) ----
import re as _re

_SECRET_WORDS = _re.compile(
    r"\b(pass ?words?|passcodes?|pass ?phrases?|pins?|pin codes?|api ?keys?|tokens?|secrets?|"
    r"card numbers?|credit card|debit card|cvv|cvc|security codes?|social security|ssn|"
    r"recovery (codes?|phrases?)|seed phrases?|wi-?fi key|login|log-in)\b", _re.I)
_SECRET_SHAPES = _re.compile(
    r"\b(?:\d[ -]?){13,19}\b|\b(?:sk|pk|rk)-[A-Za-z0-9_-]{12,}|\bAIza[0-9A-Za-z_-]{20,}|\bAQ\.[0-9A-Za-z_.-]{20,}|"
    r"\bgsk_[A-Za-z0-9]{20,}|"
    r"\bgh[pousr]_[A-Za-z0-9]{20,}|\bxox[abp]-[A-Za-z0-9-]{10,}")


def looks_secret(text: str) -> bool:
    """Lenient: true if the text looks like it holds a password, PIN, card number, API key etc."""
    return bool(_SECRET_WORDS.search(text or "") or _SECRET_SHAPES.search(text or ""))


def secret_title(text: str) -> str:
    """A safe label, e.g. 'my router password is X' -> 'a saved secret about: my router password'."""
    m = _SECRET_WORDS.search(text or "")
    if not m:
        return "a saved secret"
    words = (text[:m.end()]).split()[-4:]
    return "a saved secret about: " + " ".join(words)


def redact(text: str) -> str:
    return "[possible secret, not logged]" if looks_secret(text) else text
