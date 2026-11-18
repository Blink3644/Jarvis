"""Other AI providers besides Gemini: one adapter per API style, behind a common interface.

Conversations are kept as google-genai `types.Content` (Gemini's format, which the rest of
Jarvis already uses); each adapter converts them to its own API and returns `types.Part`s
(text and function calls), so the brain and tool registry work unchanged.

Formats used (plain HTTPS via urllib, no extra SDK):
- "openai": OpenAI Chat Completions, POST {base_url}/chat/completions with `tools`
  [{"type": "function", "function": {...}}], replies with `message.tool_calls`, tool results
  sent back as {"role": "tool", "tool_call_id": ...}. Groq, OpenRouter, Mistral and Ollama
  all offer this same API at their own base URL.
- "anthropic": Anthropic Messages, POST {base_url}/messages with headers x-api-key and
  anthropic-version; `tool_use` / `tool_result` content blocks.
Model names are not hard-coded: "Test key" fetches each provider's own model list (GET /models).
"""
import base64
import json
import logging
import socket
import urllib.error
import urllib.request

from google.genai import types

log = logging.getLogger(__name__)

# kind: which adapter; images/audio: what the provider's models can take as input by default
PRESETS = {
    "openai": {"name": "OpenAI", "kind": "openai", "base_url": "https://api.openai.com/v1",
               "images": True, "audio": False, "key_url": "https://platform.openai.com/api-keys"},
    "anthropic": {"name": "Anthropic Claude", "kind": "anthropic", "base_url": "https://api.anthropic.com/v1",
                  "images": True, "audio": False, "key_url": "https://console.anthropic.com/settings/keys"},
    "groq": {"name": "Groq", "kind": "openai", "base_url": "https://api.groq.com/openai/v1",
             "images": False, "audio": False, "key_url": "https://console.groq.com/keys"},
    "openrouter": {"name": "OpenRouter", "kind": "openai", "base_url": "https://openrouter.ai/api/v1",
                   "images": True, "audio": False, "key_url": "https://openrouter.ai/keys"},
    "mistral": {"name": "Mistral", "kind": "openai", "base_url": "https://api.mistral.ai/v1",
                "images": False, "audio": False, "key_url": "https://console.mistral.ai/api-keys"},
    "ollama": {"name": "Ollama (local)", "kind": "openai", "base_url": "http://localhost:11434/v1",
               "images": False, "audio": False, "key_url": "", "no_key": True},
    "custom": {"name": "Other (OpenAI-compatible)", "kind": "openai", "base_url": "",
               "images": False, "audio": False, "key_url": ""},
}
GEMINI_ENTRY = {"id": "gemini", "kind": "gemini", "name": "Google Gemini", "enabled": True}


def new_entry(preset: str, existing_ids=()) -> dict:
    p = PRESETS[preset]
    pid, n = preset, 2
    while pid in existing_ids:
        pid, n = f"{preset}{n}", n + 1
    return {"id": pid, "preset": preset, "kind": p["kind"], "name": p["name"], "base_url": p["base_url"],
            "models": [], "enabled": True, "images": p["images"], "audio": p["audio"]}


def supports(entry: dict, need: set) -> bool:
    if entry.get("kind") == "gemini":
        return True
    return ("image" not in need or entry.get("images")) and ("audio" not in need or entry.get("audio"))


class ProviderError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(f"HTTP {code}: {message[:500]}")
        self.code = code


def _http(url: str, headers: dict, body: dict | None, timeout: float) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json", "User-Agent": "Jarvis",
                                          **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            msg = e.read().decode("utf-8", "replace")
        except Exception:
            msg = str(e)
        raise ProviderError(e.code, msg) from None
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            raise TimeoutError("request timed out") from None
        raise ConnectionError(str(e.reason)) from None


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _json(obj) -> str:
    return json.dumps(obj, default=str)


def _schema(params):
    return params or {"type": "object", "properties": {}}


class _Ids:
    """Gemini function calls may have no id; other APIs need one that the result points back to."""
    def __init__(self):
        self.n = 0
        self.pending: dict[str, list[str]] = {}

    def call(self, fc) -> str:
        self.n += 1
        cid = fc.id or f"call_{self.n}"
        self.pending.setdefault(fc.name, []).append(cid)
        return cid

    def result(self, fr) -> str:
        queue = self.pending.get(fr.name) or []
        if fr.id and fr.id in queue:
            queue.remove(fr.id)
            return fr.id
        return queue.pop(0) if queue else (fr.id or "call_0")


def _visible(p) -> bool:
    return not getattr(p, "thought", False)


# ---------------- OpenAI-compatible ----------------
def to_openai(system: str, contents: list) -> list[dict]:
    msgs = [{"role": "system", "content": system}] if system else []
    ids = _Ids()
    for c in contents:
        parts = [p for p in (c.parts or []) if _visible(p)]
        if c.role == "model":
            text = "".join(p.text for p in parts if p.text)
            calls = [{"id": ids.call(p.function_call), "type": "function",
                      "function": {"name": p.function_call.name,
                                   "arguments": _json(dict(p.function_call.args or {}))}}
                     for p in parts if p.function_call]
            m = {"role": "assistant", "content": text or None}
            if calls:
                m["tool_calls"] = calls
            msgs.append(m)
            continue
        user = []
        for p in parts:
            if p.function_response:
                msgs.append({"role": "tool", "tool_call_id": ids.result(p.function_response),
                             "content": _json(p.function_response.response)})
            elif p.inline_data is not None:
                mime = p.inline_data.mime_type or ""
                if mime.startswith("image/"):
                    user.append({"type": "image_url",
                                 "image_url": {"url": f"data:{mime};base64,{_b64(p.inline_data.data)}"}})
                elif mime.startswith("audio/"):
                    fmt = "mp3" if "mp" in mime else "wav"
                    user.append({"type": "input_audio",
                                 "input_audio": {"data": _b64(p.inline_data.data), "format": fmt}})
            elif p.text:
                user.append({"type": "text", "text": p.text})
        if user:
            only_text = all(u["type"] == "text" for u in user)
            msgs.append({"role": "user",
                         "content": "\n".join(u["text"] for u in user) if only_text else user})
    return msgs


class OpenAIAdapter:
    def __init__(self, entry: dict):
        self.base = (entry.get("base_url") or "").rstrip("/")

    def _headers(self, key):
        return {"Authorization": f"Bearer {key}"} if key else {}

    def generate(self, key, model, system, contents, tools, timeout=30) -> list:
        body = {"model": model, "messages": to_openai(system, contents)}
        if tools:
            body["tools"] = [{"type": "function", "function": {
                "name": t["name"], "description": t.get("description", ""),
                "parameters": _schema(t.get("parameters_json_schema"))}} for t in tools]
        data = _http(self.base + "/chat/completions", self._headers(key), body, timeout)
        msg = ((data.get("choices") or [{}])[0].get("message") or {})
        parts = []
        if msg.get("content"):
            parts.append(types.Part(text=msg["content"]))
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            parts.append(types.Part(function_call=types.FunctionCall(
                id=tc.get("id"), name=fn.get("name"), args=args if isinstance(args, dict) else {})))
        return parts

    def list_models(self, key, timeout=15) -> list[str]:
        data = _http(self.base + "/models", self._headers(key), None, timeout)
        return sorted(m.get("id") for m in data.get("data") or [] if m.get("id"))


# ---------------- Anthropic ----------------
ANTHROPIC_VERSION = "2023-06-01"


def to_anthropic(contents: list) -> list[dict]:
    msgs = []
    ids = _Ids()
    for c in contents:
        blocks = []
        for p in (c.parts or []):
            if not _visible(p):
                continue
            if p.function_call:
                blocks.append({"type": "tool_use", "id": ids.call(p.function_call),
                               "name": p.function_call.name, "input": dict(p.function_call.args or {})})
            elif p.function_response:
                blocks.append({"type": "tool_result", "tool_use_id": ids.result(p.function_response),
                               "content": _json(p.function_response.response)})
            elif p.inline_data is not None and (p.inline_data.mime_type or "").startswith("image/"):
                blocks.append({"type": "image", "source": {"type": "base64",
                               "media_type": p.inline_data.mime_type, "data": _b64(p.inline_data.data)}})
            elif p.text:
                blocks.append({"type": "text", "text": p.text})
        if blocks:
            msgs.append({"role": "assistant" if c.role == "model" else "user", "content": blocks})
    return msgs


class AnthropicAdapter:
    def __init__(self, entry: dict):
        self.base = (entry.get("base_url") or PRESETS["anthropic"]["base_url"]).rstrip("/")

    def _headers(self, key):
        return {"x-api-key": key or "", "anthropic-version": ANTHROPIC_VERSION}

    def generate(self, key, model, system, contents, tools, timeout=30) -> list:
        body = {"model": model, "max_tokens": 2048, "messages": to_anthropic(contents)}
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [{"name": t["name"], "description": t.get("description", ""),
                              "input_schema": _schema(t.get("parameters_json_schema"))} for t in tools]
        data = _http(self.base + "/messages", self._headers(key), body, timeout)
        parts = []
        for b in data.get("content") or []:
            if b.get("type") == "text" and b.get("text"):
                parts.append(types.Part(text=b["text"]))
            elif b.get("type") == "tool_use":
                parts.append(types.Part(function_call=types.FunctionCall(
                    id=b.get("id"), name=b.get("name"), args=b.get("input") or {})))
        return parts

    def list_models(self, key, timeout=15) -> list[str]:
        data = _http(self.base + "/models", self._headers(key), None, timeout)
        return sorted(m.get("id") for m in data.get("data") or [] if m.get("id"))


def make_adapter(entry: dict):
    return AnthropicAdapter(entry) if entry.get("kind") == "anthropic" else OpenAIAdapter(entry)


def media_needs(contents: list) -> set:
    need = set()
    for c in contents:
        for p in c.parts or []:
            if p.inline_data is not None:
                kind = (p.inline_data.mime_type or "").split("/")[0]
                if kind in ("image", "audio"):
                    need.add(kind)
    return need
