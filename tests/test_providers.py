"""Several AI providers in priority order, with fake adapters (no network)."""
import json

from google.genai import types

from jarvis.core import config, providers
from jarvis.core.brain import Brain
from jarvis.core.providers import ProviderError, to_anthropic, to_openai
from jarvis.core.registry import Registry, Tool
from jarvis.core.usage import Usage
from jarvis.pc import basic_tools

from .conftest import FakeClient, api_error, text_chunks

MAIN, BACKUP = config.DEFAULTS["model_main"], config.DEFAULTS["model_fallback"]


class FakeAdapter:
    def __init__(self, script):
        self.script = script          # list of (provider id, Part list or Exception)
        self.calls = []

    def factory(self, entry):
        outer = self

        class A:
            def generate(self, key, model, system, contents, tools, timeout=30):
                outer.calls.append((entry["id"], model, key, contents, tools))
                pid, result = outer.script.pop(0)
                assert pid == entry["id"], (pid, entry["id"])
                if isinstance(result, Exception):
                    raise result
                return result
        return A()


def text(t):
    return [types.Part(text=t)]


def make(gemini_script, other_script, entries, keys=None):
    cfg = config.load()
    cfg["providers"] = [dict(providers.GEMINI_ENTRY)] + entries
    reg = Registry()
    basic_tools.register(reg)
    fa = FakeAdapter(other_script)
    keys = keys if keys is not None else {e["id"]: "k-" + e["id"] for e in entries}
    b = Brain(cfg, reg, client_factory=lambda k: FakeClient(gemini_script), sleep=lambda s: None,
              get_key=lambda: "gem-key", get_provider_key=keys.get, adapter_factory=fa.factory)
    return b, fa


def entry(preset, models, **kw):
    e = providers.new_entry(preset)
    e["models"] = models
    e.update(kw)
    return e


def quota():
    return api_error(429, "RESOURCE_EXHAUSTED", "quota", "PerDay")


def test_falls_through_gemini_to_openai_and_announces_once():
    b, fa = make([(MAIN, quota()), (BACKUP, quota())],
                 [("openai", text("from openai")), ("openai", text("again"))],
                 [entry("openai", ["gpt-x"])])
    r = b.ask("hi")
    assert r.ok and r.text == "from openai" and r.notices == ["Switching to OpenAI for a bit."]
    assert fa.calls[0][2] == "k-openai"
    r2 = b.ask("more")
    assert r2.text == "again" and r2.notices == []
    assert b.usage.count("openai/gpt-x") == 2


def test_returns_to_gemini_when_it_recovers():
    b, fa = make([(MAIN, text_chunks("gemini back"))], [], [entry("openai", ["gpt-x"])])
    b.active, b.on_backup = "openai/gpt-x", True
    r = b.ask("hi")
    assert r.text == "gemini back" and b.active is None and not b.on_backup


def test_order_and_disabled_and_bad_key_moves_on():
    cfg_entries = [entry("anthropic", ["claude-x"]), entry("groq", ["llama-x"]),
                   entry("mistral", ["m-x"], enabled=False)]
    b, fa = make([], [("anthropic", ProviderError(401, "invalid x-api-key")),
                      ("groq", text("groq here"))], cfg_entries)
    b.cfg["providers"] = cfg_entries          # Gemini removed from the list entirely
    r = b.ask("hi")
    assert r.ok and r.text == "groq here" and "Switching to Groq for a bit." in r.notices
    assert [c[0] for c in fa.calls] == ["anthropic", "groq"]


def test_missing_key_skipped_and_reported_when_nothing_left():
    b, fa = make([(MAIN, quota()), (BACKUP, quota())], [], [entry("openai", ["gpt-x"])], keys={})
    r = b.ask("hi")
    assert not r.ok and "OpenAI" in r.text and fa.calls == []


def test_ollama_needs_no_key():
    b, fa = make([(MAIN, quota()), (BACKUP, quota())], [("ollama", text("local"))],
                 [entry("ollama", ["llama3"])], keys={})
    assert b.ask("hi").text == "local"


def test_screenshot_question_skips_provider_without_images():
    cfg_entries = [entry("groq", ["llama-x"]), entry("openai", ["gpt-x"])]
    b, fa = make([], [("openai", text("I see a boss"))], cfg_entries)
    b.cfg["providers"] = cfg_entries

    class Shot:
        data, mime = b"\x89PNG", "image/png"
    b.registry.attachments = lambda t: [Shot()]
    r = b.ask("how do I beat this boss?")
    assert r.text == "I see a boss"
    assert any("Groq can't see pictures" in n for n in r.notices)
    assert b.active is None          # a one-off skip, not a switch


def test_tool_call_through_openai_runs_registry_tool():
    call = [types.Part(function_call=types.FunctionCall(id="t1", name="get_time", args={}))]
    b, fa = make([(MAIN, quota()), (BACKUP, quota())],
                 [("openai", call), ("openai", text("It's noon."))], [entry("openai", ["gpt-x"])])
    r = b.ask("time?")
    assert r.tools_used == ["get_time"] and r.text == "It's noon."
    msgs = to_openai("sys", fa.calls[1][3])
    tool_msg = [m for m in msgs if m["role"] == "tool"][0]
    assert tool_msg["tool_call_id"] == "t1" and "time" in json.loads(tool_msg["content"])
    assert any(t["name"] == "get_time" for t in fa.calls[0][4])


def test_destructive_tool_still_needs_confirmation():
    reg_called = []
    b, fa = make([(MAIN, quota()), (BACKUP, quota())],
                 [("openai", [types.Part(function_call=types.FunctionCall(id="x", name="danger", args={}))]),
                  ("openai", text("Shall I?"))], [entry("openai", ["gpt-x"])])
    b.registry.register(Tool("danger", "a destructive test tool", {"type": "object", "properties": {}},
                             lambda: reg_called.append(1) or {"ok": True}, destructive=True))
    b.ask("do it")
    assert reg_called == []


def test_per_provider_usage_warning():
    b, fa = make([(MAIN, quota()), (BACKUP, quota())], [("openai", text("a"))],
                 [entry("openai", ["gpt-x"])])
    b.cfg["daily_limits"]["openai/gpt-x"] = 1
    r = b.ask("hi")
    assert any("openai/gpt-x" in n for n in r.notices)
    assert ("openai/gpt-x", 1, 1) in Usage(b.cfg).summary()


def test_openai_conversion_with_images_and_gemini_ids_missing():
    contents = [
        types.Content(role="user", parts=[types.Part(text="hi")]),
        types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name="look", args={"a": 1}))]),
        types.Content(role="user", parts=[
            types.Part(function_response=types.FunctionResponse(name="look", response={"ok": True})),
            types.Part.from_bytes(data=b"img", mime_type="image/png")]),
    ]
    m = to_openai("sys", contents)
    assert m[0] == {"role": "system", "content": "sys"} and m[1] == {"role": "user", "content": "hi"}
    cid = m[2]["tool_calls"][0]["id"]
    assert m[3] == {"role": "tool", "tool_call_id": cid, "content": '{"ok": true}'}
    assert m[4]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_anthropic_conversion():
    contents = [
        types.Content(role="user", parts=[types.Part(text="hi")]),
        types.Content(role="model", parts=[types.Part(text="thinking", thought=True),
                                           types.Part(function_call=types.FunctionCall(id="u1", name="t", args={}))]),
        types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
            id="u1", name="t", response={"v": 2}))]),
    ]
    m = to_anthropic(contents)
    assert m[1] == {"role": "assistant", "content": [{"type": "tool_use", "id": "u1", "name": "t", "input": {}}]}
    assert m[2]["content"][0] == {"type": "tool_result", "tool_use_id": "u1", "content": '{"v": 2}'}


def test_adapters_parse_replies(monkeypatch):
    seen = {}

    def fake_http(url, headers, body, timeout):
        seen["url"], seen["headers"], seen["body"] = url, headers, body
        if "anthropic" in url:
            return {"content": [{"type": "text", "text": "hey"},
                                {"type": "tool_use", "id": "u", "name": "get_time", "input": {"x": 1}}]}
        return {"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "c", "type": "function", "function": {"name": "get_time", "arguments": '{"x": 1}'}}]}}]}
    monkeypatch.setattr(providers, "_http", fake_http)
    tools = [{"name": "get_time", "description": "d", "parameters_json_schema": None}]
    parts = providers.OpenAIAdapter(entry("groq", [])).generate("K", "m", "sys", [], tools)
    assert parts[0].function_call.args == {"x": 1}
    assert seen["url"].endswith("/openai/v1/chat/completions") and seen["headers"]["Authorization"] == "Bearer K"
    assert seen["body"]["tools"][0]["function"]["parameters"]["type"] == "object"
    parts = providers.AnthropicAdapter(entry("anthropic", [])).generate("K", "m", "sys", [], tools)
    assert parts[0].text == "hey" and parts[1].function_call.id == "u"
    assert seen["headers"]["x-api-key"] == "K" and seen["body"]["system"] == "sys"
    assert seen["body"]["tools"][0]["input_schema"]["type"] == "object"


# ---- Groq first, Gemini second (bug: kept switching to Gemini, then Gemini 400'd) ----
def make_groq_first(gemini_script, groq_script):
    cfg = config.load()
    cfg["providers"] = [entry("groq", ["big", "small"]), dict(providers.GEMINI_ENTRY)]
    reg = Registry()
    basic_tools.register(reg)
    gem = FakeClient(gemini_script)
    fa = FakeAdapter(groq_script)
    slept = []
    b = Brain(cfg, reg, client_factory=lambda k: gem, sleep=slept.append,
              get_key=lambda: "gem-key", get_provider_key=lambda pid: "k", adapter_factory=fa.factory)
    return b, fa, gem, slept


def groq_tpm(secs="17.13s"):
    return ProviderError(429, '{"error":{"message":"Rate limit reached for model on tokens per minute '
                              f'(TPM): Limit 8000, Used 6835, Requested 3449. Please try again in {secs}."}}}}')


def call(name):
    return [types.Part(function_call=types.FunctionCall(id="call_1", name=name, args={}))]


def test_retry_after_parses_hints():
    from jarvis.core.brain import retry_after
    assert retry_after(groq_tpm("17.13s")) == 17.13
    assert retry_after(groq_tpm("1m30.5s")) == 90.5
    assert retry_after(groq_tpm("250ms")) == 0.25
    assert retry_after(Exception("Please retry in 6h11m46.3s")) == 6 * 3600 + 11 * 60 + 46.3
    assert retry_after(Exception("nope")) is None


def test_gemini_takeover_mid_turn_gets_groq_tool_calls_as_text():
    # Groq asks for a tool, then hits its per-minute token limit on both models; Gemini finishes.
    b, fa, gem, _ = make_groq_first(
        [(MAIN, text_chunks("It's noon."))],
        [("groq", call("get_time")), ("groq", groq_tpm()), ("groq", groq_tpm())])
    r = b.ask("what time is it?")
    assert r.ok and r.text == "It's noon.", r.text
    sent = gem.models.calls[0][1]
    assert not any(p.function_call or p.function_response for c in sent for p in c.parts or [])
    flat = " ".join(p.text or "" for c in sent for p in c.parts or [])
    assert "get_time" in flat and "returned" in flat


def test_groq_short_wait_is_waited_out_not_switched():
    b, fa, gem, slept = make_groq_first([], [("groq", groq_tpm("1.2s")), ("groq", text("hi"))])
    r = b.ask("hi")
    assert r.ok and r.text == "hi" and r.notices == [] and slept == [1.2]


def test_groq_comes_back_after_its_own_wait_not_a_full_minute():
    import datetime as dt
    now = [dt.datetime(2026, 11, 18, 20, 0, tzinfo=dt.timezone.utc)]
    b, fa, gem, _ = make_groq_first(
        [(MAIN, text_chunks("from gemini"))],
        [("groq", groq_tpm("17s")), ("groq", groq_tpm("16s")), ("groq", text("groq again"))])
    b.clock = lambda: now[0]
    assert b.ask("one").text == "from gemini"
    now[0] += dt.timedelta(seconds=20)
    assert b.ask("two").text == "groq again"


def test_gemini_own_tool_calls_keep_structure():
    from jarvis.core.brain import _for_gemini
    own = types.Content(role="model", parts=[types.Part(
        function_call=types.FunctionCall(name="get_time", args={}), thought_signature=b"sig")])
    resp = types.Content(role="user", parts=[types.Part(
        function_response=types.FunctionResponse(name="get_time", response={"t": 1}))])
    out = _for_gemini([own, resp], {"call_1"})
    assert out[0].parts[0].function_call and out[1].parts[0].function_response
    # an unsigned call that Gemini made itself (no id from another provider) is kept too
    own.parts[0].thought_signature = None
    assert _for_gemini([own, resp], {"call_1"})[0].parts[0].function_call
