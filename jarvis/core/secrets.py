"""The Gemini API key lives in Windows Credential Manager via keyring, never in a file."""
import keyring

SERVICE = "Jarvis"
USER = "gemini_api_key"


def clean_key(raw: str) -> str:
    """Drop whitespace and control characters (Ctrl+V in a console types \\x16)."""
    return "".join(c for c in (raw or "") if c.isprintable() and not c.isspace())


def looks_like_key(key: str) -> bool:
    return len(key) >= 30 and all(c.isalnum() or c in "-_" for c in key)


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
