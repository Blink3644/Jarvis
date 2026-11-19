"""The Gemini brain: streaming replies, tool calls, model fallback, usage and friendly errors.

SDK: google-genai (checked against v3.4.0 on 2026-11-17).
"""
import datetime
import json
import logging
import re
import socket
import time
from dataclasses import dataclass, field
from typing import Callable

from google import genai
from google.genai import errors, types

from . import providers, secrets
from .history import Conversation, HistoryStore, fallback_summary
from .personality import split_prompt, system_prompt
from .registry import Registry
from .usage import Usage, next_reset

log = logging.getLogger(__name__)
MAX_TOOL_ROUNDS = 6
BACKUP_NOTICE = "Switching to my backup brain for a bit."


class FriendlyError(Exception):
    """An error whose message is safe and plain enough to show/speak to the user."""


@dataclass
class Reply:
    text: str
    ok: bool = True
    notices: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)


def classify(e: Exception) -> str:
    """rate_day | rate_minute | bad_key | model_missing | server | offline | other"""
    if isinstance(e, errors.APIError):
        code = getattr(e, "code", 0) or 0
        blob = f"{e} {getattr(e, 'details', '')}"
        low = blob.lower()
        if code == 429 or "resource_exhausted" in low:
            return "rate_day" if ("perday" in low or "per day" in low) else "rate_minute"
        # an HTML 400 page comes from Google's front door when the key header is malformed
        if ("api_key_invalid" in low or "api key not valid" in low or code in (401, 403)
                or (code == 400 and "<html" in low)):
            return "bad_key"
        if code == 404:
            return "model_missing"
        if code >= 500:
            return "server"
        return "other"
    if isinstance(e, providers.ProviderError):
        low = str(e).lower()
        if e.code == 429:
            return "rate_day" if ("per day" in low or "daily" in low) else "rate_minute"
        if e.code in (401, 403):
            return "bad_key"
        if e.code == 404:
            return "model_missing"
        if e.code >= 500:
            return "server"
        return "other"
    name = type(e).__name__.lower()
    if name in ("readtimeout", "writetimeout", "remoteprotocolerror"):
        return "server"      # connected, but Google didn't answer in time
    if isinstance(e, (socket.gaierror, ConnectionError, TimeoutError)) or \
            "connect" in name or "timeout" in name or "network" in name:
        return "offline"
    return "other"


class Brain:
    def __init__(self, cfg: dict, registry: Registry, usage: Usage | None = None,
                 history: HistoryStore | None = None,
                 client_factory: Callable[[str], object] | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], datetime.datetime] | None = None,
                 get_key: Callable[[], str | None] = secrets.get_api_key,
                 get_provider_key: Callable[[str], str | None] = secrets.get_provider_key,
                 adapter_factory: Callable[[dict], object] = providers.make_adapter):
        self.cfg = cfg
        self.registry = registry
        self.clock = clock or (lambda: datetime.datetime.now(datetime.timezone.utc))
        self.usage = usage or Usage(cfg, clock=self.clock)
        self.history = history or HistoryStore(cfg)
        self.conv = Conversation(cfg.get("max_turns", 20))
        # 30 s timeout (milliseconds) so a stuck request can't freeze Jarvis
        self.client_factory = client_factory or (lambda key: genai.Client(
            api_key=key, http_options=types.HttpOptions(timeout=30_000)))
        self.sleep = sleep
        self.get_key = get_key
        self.get_provider_key = get_provider_key
        self.adapter_factory = adapter_factory
        self.active: str | None = None      # usage key of the fallback slot in use (None = top choice)
        self.blocked_until: dict[str, datetime.datetime] = {}
        self.on_backup = False
        self._foreign_calls: set[str] = set()   # ids of tool calls made by non-Gemini providers
        self._n_foreign = 0
        self._last_usage = None
        self._client = None
        self._client_key = None
        self.memory = self.history.load_summaries()

    # ---------- public ----------
    def ask(self, text: str, on_text: Callable[[str], None] | None = None,
            on_notice: Callable[[str], None] | None = None) -> Reply:
        notices: list[str] = []

        def notice(msg):
            notices.append(msg)
            if on_notice:
                on_notice(msg)

        tools_used: list[str] = []
        self.registry.begin_turn(text)
        final = ""
        try:
            first = [types.Part(text=text)]
            for m in self.registry.attachments(text):     # e.g. auto-look screenshot
                first.append(_media_part(m))
            exchange = [types.Content(role="user", parts=first)]
            for _ in range(MAX_TOOL_ROUNDS):
                parts = self._request(self.conv.contents(exchange), notice, on_text)
                exchange.append(types.Content(role="model", parts=parts))
                calls = [p.function_call for p in parts if p.function_call]
                if not calls:
                    final = "".join(p.text for p in parts if p.text and not p.thought).strip()
                    break
                responses = []
                for fc in calls:
                    tools_used.append(fc.name)
                    log.info("tool call: %s", fc.name)
                    result = self.registry.call(fc.name, dict(fc.args or {}))
                    media = result.pop("_media", None) or []
                    responses.append(types.Part(function_response=types.FunctionResponse(
                        id=fc.id, name=fc.name, response=result)))
                    responses += [_media_part(m) for m in media]
                exchange.append(types.Content(role="user", parts=responses))
            else:
                final = "Sorry, I got stuck going back and forth with my tools. Could you rephrase that?"
        except FriendlyError as e:
            return Reply(str(e), ok=False, notices=notices, tools_used=tools_used)
        except Exception:
            log.exception("unexpected error while answering")
            return Reply("Sorry, something went wrong on my end. The details are in the log.",
                         ok=False, notices=notices, tools_used=tools_used)
        if not final:
            final = "Done." if tools_used else "Sorry, I didn't get an answer back. Could you try again?"
        self.conv.commit(_without_media(exchange), text, final)
        self.registry.last_reply = final
        self.history.log_line("You", text)
        self.history.log_line("Jarvis", final)
        return Reply(final, notices=notices, tools_used=tools_used)

    def clear_conversation(self) -> None:
        self.conv.clear()

    def close(self) -> None:
        """Call on shutdown. In "remember" mode, store a short summary of this session."""
        if self.history.mode != "remember" or not self.conv.transcript:
            return
        summary = ""
        try:
            lines = "\n".join(f"{w}: {t}" for w, t in self.conv.transcript[-30:])
            client = self._get_client()
            model = self.cfg["model_fallback"]
            if not self._available(model):
                raise FriendlyError("no quota")
            resp = client.models.generate_content(
                model=model,
                contents="Summarise this conversation in at most two short sentences, "
                         "keeping facts the user may refer to later:\n" + lines,
            )
            self.usage.record(model)
            summary = (resp.text or "").strip()
        except Exception:
            log.info("summary at shutdown failed; using fallback summary", exc_info=True)
        self.history.save_summary(summary or fallback_summary(self.conv.transcript))

    def status(self) -> str:
        return "backup" if self.on_backup else "main"

    # ---------- internals ----------
    def _get_client(self):
        key = self.get_key()
        if not key:
            raise FriendlyError("I don't have a Gemini API key yet. Please add one in settings.")
        if self._client is None or key != self._client_key:
            self._client = self.client_factory(key)
            self._client_key = key
        return self._client

    def _available(self, model: str) -> bool:
        until = self.blocked_until.get(model)
        if until and until > self.clock():
            return False
        return not self.usage.exhausted(model)

    def _tool_decls(self) -> list[dict]:
        """Only the tools picked for this question (registry.pick_tools), unless that's turned off."""
        return self.registry.declarations(for_turn=bool(self.cfg.get("smart_tools", True)))

    def _config(self, timeout_s: float | None = None, system: str | None = None) -> types.GenerateContentConfig:
        decls = [types.FunctionDeclaration(**d) for d in self._tool_decls()]
        return types.GenerateContentConfig(
            # Google's minimum deadline is 10 s
            http_options=types.HttpOptions(timeout=int(max(10, timeout_s) * 1000)) if timeout_s else None,
            system_instruction=system if system is not None else
            system_prompt(self.cfg, self.registry.extra_context(), self.memory),
            tools=[types.Tool(function_declarations=decls)] if decls else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

    def _stream(self, client, model, contents, on_text, timeout=None, system=None) -> list[types.Part]:
        parts: list[types.Part] = []
        self._last_usage = None
        # An overloaded top choice can take 20+ s just to fail; the caller passes a short
        # timeout when there's a backup to move on to.
        for chunk in client.models.generate_content_stream(
                model=model, contents=contents, config=self._config(timeout, system)):
            um = getattr(chunk, "usage_metadata", None)
            if um is not None and getattr(um, "prompt_token_count", None):
                self._last_usage = (um.prompt_token_count, um.cached_content_token_count or 0,
                                    um.candidates_token_count)
            for cand in (chunk.candidates or [])[:1]:
                for p in (cand.content.parts if cand.content and cand.content.parts else []):
                    if p.text and not p.thought and on_text:
                        on_text(p.text)
                    plain = p.text is not None and not p.thought and not p.thought_signature \
                        and not p.function_call
                    if plain and parts and parts[-1].text is not None and not parts[-1].thought \
                            and not parts[-1].thought_signature and not parts[-1].function_call:
                        parts[-1] = types.Part(text=parts[-1].text + p.text)
                    else:
                        parts.append(p)
        return parts

    def _slots(self) -> list[tuple[dict, str, str]]:
        """(provider entry, model, usage key) in the user's priority order."""
        main, backup = self.cfg["model_main"], self.cfg["model_fallback"]
        out = []
        for p in self.cfg.get("providers") or [providers.GEMINI_ENTRY]:
            if not p.get("enabled", True):
                continue
            if p.get("kind") == "gemini":
                out += [(p, main, main), (p, backup, backup)]
            else:
                out += [(p, m, f"{p['id']}/{m}") for m in p.get("models") or [] if m]
        return out

    def _call_slot(self, entry, model, contents, on_text, quick):
        # The time/notes/game go in a [Context] note before the latest question, so the fixed
        # instructions stay identical between requests (helps providers that cache the start of a
        # request). Only the tools picked for this question are described (smart_tools); earlier
        # calls to tools not described now are passed on as text notes so no API rejects them.
        system, note = split_prompt(self.cfg, self.registry.extra_context(), self.memory)
        tools = self._tool_decls()
        sent_names = {t["name"] for t in tools}
        if entry.get("kind") == "gemini":
            timeout = self.cfg.get("main_timeout_s", 10) if quick else None
            foreign = self._foreign_calls
            sent = _with_context_note(_calls_as_text(
                contents, lambda x: x.id in foreign or x.name not in sent_names), note)
            parts = self._stream(self._get_client(), model, sent, on_text, timeout, system)
            if self._last_usage:
                log.info("%s tokens: prompt %s (cached %s), reply %s", model, *self._last_usage)
            return parts
        api_key = self.get_provider_key(entry["id"])
        if not api_key and not providers.PRESETS.get(entry.get("preset"), {}).get("no_key"):
            raise FriendlyError(f"I don't have an API key for {entry['name']} yet.")
        timeout = self.cfg.get("main_timeout_s", 10) if quick else 30
        sent = _calls_as_text(contents, lambda x: x.name not in sent_names)
        parts = self.adapter_factory(entry).generate(api_key, model, system,
                                                     _with_context_note(sent, note), tools,
                                                     timeout=max(10, timeout))
        for p in parts:                 # remember these so Gemini gets them as text later
            if p.function_call:
                if not p.function_call.id:
                    self._n_foreign += 1
                    p.function_call.id = f"jarvis_call_{self._n_foreign}"
                self._foreign_calls.add(p.function_call.id)
        text = "".join(p.text for p in parts if p.text)
        if text and on_text:
            on_text(text)       # these APIs are called without streaming: the whole reply at once
        return parts

    def _request(self, contents, notice, on_text) -> list[types.Part]:
        slots = self._slots()
        main, backup = self.cfg["model_main"], self.cfg["model_fallback"]
        need = providers.media_needs(contents)
        minute_limited = server_down = False
        fatal: str | None = None
        dead: set[str] = set()          # providers whose key is missing/wrong this time
        skipped_media: list[str] = []
        first_key = next((k for e, _, k in slots if providers.supports(e, need)), None)
        for i, (entry, model, ukey) in enumerate(slots):
            pid, name = entry.get("id", "gemini"), entry.get("name", "Gemini")
            if pid in dead or not self._available(ukey):
                continue
            if not providers.supports(entry, need):
                if name not in skipped_media:
                    skipped_media.append(name)
                continue
            more = any(k != ukey for _, _, k in slots[i + 1:])
            others = any(e.get("id") != pid for e, _, _ in slots[i + 1:])
            retries = self.cfg.get("rate_limit_retries", 2)
            server_retry = 1
            attempt = 0
            while True:
                try:
                    parts = self._call_slot(entry, model, contents, on_text,
                                            quick=(ukey == first_key and more))
                except FriendlyError as e:          # no key for this provider
                    if not others:
                        if fatal:
                            raise FriendlyError(fatal) from e
                        raise
                    fatal = fatal or str(e)
                    dead.add(pid)
                    break
                except Exception as e:
                    kind = classify(e)
                    log.warning("%s error on %s: %s (%s)", name, model, kind, e)
                    if kind == "rate_day":
                        self.blocked_until[ukey] = next_reset(self.clock())
                        break
                    if kind == "rate_minute":
                        wait = retry_after(e)
                        if attempt < retries and (not others or (wait is not None and wait <= SHORT_WAIT_S)):
                            # nowhere else to go, or the limit frees up in a moment: wait for it
                            self.sleep(wait if wait is not None else 2 * (2 ** attempt))
                            attempt += 1
                            continue
                        minute_limited = True
                        # Groq says exactly when its per-minute token limit frees up (often
                        # ~15 s); use that so Jarvis goes back to the top choice quickly.
                        block = min(60.0, wait) if wait else 60.0
                        self.blocked_until[ukey] = self.clock() + datetime.timedelta(seconds=block)
                        break
                    if kind == "server":
                        if "high demand" in str(e).lower() or more:
                            server_retry = 0    # busy/stuck: don't retry, the next choice is quicker
                        if server_retry > 0:
                            server_retry -= 1
                            self.sleep(2)
                            continue
                        server_down = True
                        if ukey == first_key:   # skip it for a minute so replies stay quick
                            self.blocked_until[ukey] = self.clock() + datetime.timedelta(seconds=60)
                        break
                    msg = _message(kind, entry, model)
                    if not others:
                        raise FriendlyError(fatal or msg) from e
                    fatal = fatal or msg
                    if kind == "bad_key":
                        dead.add(pid)
                    break
                warn = self.usage.record(ukey)
                if skipped_media:
                    what = "see pictures" if "image" in need else "listen to audio"
                    notice(f"{' and '.join(skipped_media)} can't {what}, so {name} is answering this one.")
                elif ukey == first_key:
                    self.on_backup = False
                    self.active = None
                elif self.active != ukey:
                    gem_backup = entry.get("kind") == "gemini" and model == backup and backup != main
                    same_provider = self.active is not None and self.active.split("/")[0] == pid
                    if gem_backup and not self.on_backup:
                        notice(BACKUP_NOTICE)
                    elif not gem_backup and not same_provider:
                        notice(f"Switching to {name} for a bit.")
                    self.on_backup = True
                    self.active = ukey
                if warn:
                    notice(warn)
                return parts
        if server_down:
            raise FriendlyError(_MESSAGES["server"])
        if minute_limited:
            raise FriendlyError("I've hit my limit, try again in a minute.")
        if fatal:
            raise FriendlyError(fatal)
        if skipped_media and not any(providers.supports(e, need) for e, _, _ in slots):
            what = "pictures" if "image" in need else "audio"
            raise FriendlyError(f"None of my AI providers can handle {what}. Turn on Gemini in settings.")
        if all(e.get("kind") == "gemini" for e, _, _ in slots):
            raise FriendlyError("I've hit my limit for today. It resets at midnight Pacific time.")
        raise FriendlyError("All my AI providers are out of requests for now. Try again later, "
                            "or add another provider in settings.")


def _message(kind: str, entry: dict, model: str) -> str:
    if entry.get("kind") == "gemini":
        return _MESSAGES.get(kind, _MESSAGES["other"]).format(model=model)
    name = entry.get("name", "that provider")
    return {"bad_key": f"My {name} API key doesn't seem to work. Please check it in settings.",
            "model_missing": f"{name} doesn't recognise the model name {model}. Please pick another in settings.",
            "offline": _MESSAGES["offline"],
            }.get(kind, f"Sorry, something went wrong talking to {name}. The details are in the log.")


SHORT_WAIT_S = 3.0
_RETRY_RE = re.compile(r"(?:try again|retry) in\s+(?:(\d+)h)?\s*(?:(\d+)m(?!s))?\s*(?:([\d.]+)(ms|s))?",
                       re.IGNORECASE)


def retry_after(e: Exception) -> float | None:
    """Seconds until a per-minute limit frees up, from the error text ("try again in 17.1s")."""
    m = _RETRY_RE.search(str(e))
    if not m or not any(m.groups()[:3]):
        return None
    h, mins, num, unit = m.groups()
    secs = int(h or 0) * 3600 + int(mins or 0) * 60
    if num:
        secs += float(num) / (1000 if unit.lower() == "ms" else 1)
    return secs


def _with_context_note(contents: list[types.Content], note: str) -> list[types.Content]:
    """Put the [Context] note in front of the user's latest question (the last user message
    that has text, not tool results). Only for this request: it isn't saved in the history,
    so earlier turns stay identical and cacheable."""
    for i in range(len(contents) - 1, -1, -1):
        c = contents[i]
        if c.role == "user" and any(p.text for p in c.parts or []) \
                and not any(p.function_response for p in c.parts or []):
            out = list(contents)
            out[i] = types.Content(role="user", parts=[types.Part(text=note)] + list(c.parts or []))
            return out
    return list(contents) + [types.Content(role="user", parts=[types.Part(text=note)])]


def _for_gemini(contents: list[types.Content], foreign: set[str]) -> list[types.Content]:
    """Tool calls made by another provider (Groq, OpenAI...) have no thought signature, and
    Gemini rejects those ("Function call is missing a thought_signature"). Turn them and
    their results (matched by call id) into plain text notes so Gemini can take over
    mid-conversation. Gemini's own calls are left exactly as they were."""
    return _calls_as_text(contents, lambda x: x.id in foreign)


def _calls_as_text(contents: list[types.Content], convert) -> list[types.Content]:
    """Replace the tool calls and results for which convert(function_call_or_response) is true
    with plain text notes (same information, no tool structure). For the request only."""
    def hit(x):
        return bool(x) and convert(x)
    out: list[types.Content] = []
    for c in contents:
        parts = list(c.parts or [])
        if not any(hit(p.function_call) or hit(p.function_response) for p in parts):
            out.append(c)
            continue
        new = []
        for p in parts:
            fc, fr = p.function_call, p.function_response
            if hit(fc):
                args = json.dumps(dict(fc.args or {}), default=str)
                new.append(types.Part(text=f"[I called the tool {fc.name} with {args}]"))
            elif hit(fr):
                res = json.dumps(fr.response, default=str)
                new.append(types.Part(text=f"[The tool {fr.name} returned: {res}]"))
            else:
                new.append(p)
        out.append(types.Content(role=c.role, parts=new))
    return out


def _media_part(m) -> types.Part:
    return types.Part.from_bytes(data=m.data, mime_type=m.mime)


def _without_media(exchange: list[types.Content]) -> list[types.Content]:
    """Screenshots and recordings are sent once, then replaced by a note so they don't
    use up tokens (and quota) on every later question."""
    out = []
    for c in exchange:
        parts = []
        for p in c.parts or []:
            if p.inline_data is not None:
                kind = (p.inline_data.mime_type or "file").split("/")[0]
                parts.append(types.Part(text=f"[{kind} shown earlier]"))
            else:
                parts.append(p)
        out.append(types.Content(role=c.role, parts=parts))
    return out


_MESSAGES = {
    "bad_key": "My Gemini API key doesn't seem to work. Please check it in settings.",
    "model_missing": "Google doesn't recognise the model name {model}. Please pick another model in settings.",
    "server": "My AI provider's servers are having trouble right now. Try again in a moment.",
    "offline": "I can't reach the internet right now. Check your connection and try again.",
    "other": "Sorry, something went wrong talking to Gemini. The details are in the log.",
}
