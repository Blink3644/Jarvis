"""The Gemini API key lives in Windows Credential Manager via keyring, never in a file."""
import keyring

SERVICE = "Jarvis"
USER = "gemini_api_key"


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
