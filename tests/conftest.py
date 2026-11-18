import pytest
from google.genai import errors, types


@pytest.fixture(autouse=True)
def jarvis_home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    return tmp_path


def text_chunks(*texts):
    return [types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[types.Part(text=t)]))]) for t in texts]


def call_chunk(name, args=None, id="c1"):
    return [types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[types.Part(
            function_call=types.FunctionCall(id=id, name=name, args=args or {}))]))])]


def api_error(code, status="", message="", quota=""):
    body = {"error": {"code": code, "status": status, "message": message,
                      "details": [{"quotaId": quota}] if quota else []}}
    cls = errors.ClientError if code < 500 else errors.ServerError
    return cls(code, body)


class FakeModels:
    def __init__(self, script):
        self.script = script          # list of (model or None, chunks | Exception)
        self.calls = []

    def generate_content_stream(self, *, model, contents, config=None):
        self.calls.append((model, list(contents), config))
        want, item = self.script.pop(0)
        assert want is None or want == model, f"expected {want}, got {model}"
        if isinstance(item, Exception):
            raise item
        return iter(item)


class FakeClient:
    def __init__(self, script):
        self.models = FakeModels(script)


class MemoryKeyring:
    """In-memory stand-in so tests never touch the real Windows Credential Manager."""
    def __init__(self):
        self.store = {}

    def get_password(self, service, user):
        return self.store.get((service, user))

    def set_password(self, service, user, pw):
        self.store[(service, user)] = pw

    def delete_password(self, service, user):
        self.store.pop((service, user), None)


@pytest.fixture(autouse=True)
def memory_keyring(monkeypatch):
    import keyring
    kr = MemoryKeyring()
    for name in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(keyring, name, getattr(kr, name))
    return kr
