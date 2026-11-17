"""The Gemini brain: streaming replies, tool calls, model fallback, usage and friendly errors.

SDK: google-genai (checked against v3.4.0 on 2026-11-17).
"""
import datetime
import logging
import socket
import time
from dataclasses import dataclass, field
from typing import Callable

from google import genai
from google.genai import errors, types

from . import secrets
from .history import Conversation, HistoryStore, fallback_summary
from .personality import system_prompt
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
                 get_key: Callable[[], str | None] = secrets.get_api_key):
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
        self.blocked_until: dict[str, datetime.datetime] = {}
        self.on_backup = False
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
        exchange = [types.Content(role="user", parts=[types.Part(text=text)])]
        final = ""
        try:
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
                    responses.append(types.Part(function_response=types.FunctionResponse(
                        id=fc.id, name=fc.name, response=result)))
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
        self.conv.commit(exchange, text, final)
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

    def _config(self) -> types.GenerateContentConfig:
        decls = [types.FunctionDeclaration(**d) for d in self.registry.declarations()]
        return types.GenerateContentConfig(
            system_instruction=system_prompt(self.cfg, self.registry.extra_context(), self.memory),
            tools=[types.Tool(function_declarations=decls)] if decls else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

    def _stream(self, client, model, contents, on_text) -> list[types.Part]:
        parts: list[types.Part] = []
        for chunk in client.models.generate_content_stream(
                model=model, contents=contents, config=self._config()):
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

    def _request(self, contents, notice, on_text) -> list[types.Part]:
        client = self._get_client()
        main, backup = self.cfg["model_main"], self.cfg["model_fallback"]
        minute_limited = server_down = False
        for model in (main, backup):
            if not self._available(model):
                continue
            retries = self.cfg.get("rate_limit_retries", 2)
            server_retry = 1
            attempt = 0
            while True:
                try:
                    parts = self._stream(client, model, contents, on_text)
                except Exception as e:
                    kind = classify(e)
                    log.warning("Gemini error on %s: %s (%s)", model, kind, e)
                    if kind == "rate_day":
                        self.blocked_until[model] = next_reset(self.clock())
                        break
                    if kind == "rate_minute":
                        if attempt < retries:
                            self.sleep(2 * (2 ** attempt))
                            attempt += 1
                            continue
                        minute_limited = True
                        self.blocked_until[model] = self.clock() + datetime.timedelta(seconds=60)
                        break
                    if kind == "server":
                        if "high demand" in str(e).lower():
                            server_retry = 0    # overloaded: don't wait, go to backup
                        if server_retry > 0:
                            server_retry -= 1
                            self.sleep(2)
                            continue
                        server_down = True      # busy/stuck: try the backup model
                        if model == main:       # skip it for a minute so replies stay quick
                            self.blocked_until[model] = self.clock() + datetime.timedelta(seconds=60)
                        break
                    raise FriendlyError(_MESSAGES.get(kind, _MESSAGES["other"]).format(model=model)) from e
                warn = self.usage.record(model)
                if model == backup and model != main:
                    if not self.on_backup:
                        notice(BACKUP_NOTICE)
                    self.on_backup = True
                elif model == main:
                    self.on_backup = False
                if warn:
                    notice(warn)
                return parts
        if server_down:
            raise FriendlyError(_MESSAGES["server"])
        if minute_limited:
            raise FriendlyError("I've hit my limit, try again in a minute.")
        raise FriendlyError("I've hit my limit for today. It resets at midnight Pacific time.")


_MESSAGES = {
    "bad_key": "My Gemini API key doesn't seem to work. Please check it in settings.",
    "model_missing": "Google doesn't recognise the model name {model}. Please pick another model in settings.",
    "server": "Google's servers are having trouble right now. Try again in a moment.",
    "offline": "I can't reach the internet right now. Check your connection and try again.",
    "other": "Sorry, something went wrong talking to Gemini. The details are in the log.",
}
