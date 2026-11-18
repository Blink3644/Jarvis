"""Stage A8: Gemini Live voice mode (experimental, off by default).

One LiveSession = one Gemini Live connection, run on its own asyncio thread.
The voice loop pushes 16 kHz mic frames in with send_audio(); Gemini's 24 kHz speech comes
back through callbacks. Tool calls go through the same Registry as normal mode, so the
same tools, safety rules and confirmations apply.

Checked 2026-11-17 against google-genai 3.4.0 (`client.aio.live.connect`,
`send_realtime_input(audio=...)`, `send_tool_response`) and Google's models page
(`gemini-3.8-live`, GA 2026-09-15).
"""
import asyncio
import logging
import threading
import time

import numpy as np

log = logging.getLogger(__name__)

IN_MIME = "audio/pcm;rate=16000"


def classify_live_error(e: Exception) -> str:
    """'quota' for limits, 'key' for a bad key, otherwise 'connection'."""
    text = f"{type(e).__name__} {e}".lower()
    if "429" in text or "quota" in text or "resource_exhausted" in text or "rate" in text and "limit" in text:
        return "quota"
    if "api key" in text or "api_key" in text or "permission" in text or "401" in text or "403" in text:
        return "key"
    return "connection"


def _media_blob(m):
    from google.genai import types
    return types.Blob(data=m.data, mime_type=m.mime_type)


class LiveSession:
    """Callbacks (all called from the session thread):
    on_audio(pcm int16 @24 kHz), on_user_text(str), on_reply_text(str), on_turn_complete(),
    on_interrupted(), on_closed(error_or_None, replied: bool).
    """

    def __init__(self, cfg, registry, *, connect, config_factory, on_audio, on_user_text=None,
                 on_reply_text=None, on_turn_complete=None, on_interrupted=None, on_closed=None,
                 clock=time.monotonic):
        self.cfg, self.registry = cfg, registry
        self.connect = connect                    # fn(model, config) -> async context manager
        self.config_factory = config_factory      # fn() -> LiveConnectConfig (or dict)
        self.on_audio = on_audio
        self.on_user_text = on_user_text or (lambda t: None)
        self.on_reply_text = on_reply_text or (lambda t: None)
        self.on_turn_complete = on_turn_complete or (lambda: None)
        self.on_interrupted = on_interrupted or (lambda: None)
        self.on_closed = on_closed or (lambda err, replied: None)
        self.clock = clock
        self.loop: asyncio.AbstractEventLoop | None = None
        self.queue: asyncio.Queue | None = None
        self.thread: threading.Thread | None = None
        self.ready = threading.Event()
        self.closed = threading.Event()
        self.replied = False                      # Gemini has sent at least some audio/text
        self.last_activity = clock()
        self.user_text = ""                       # transcript of the current user turn
        self.error: Exception | None = None

    # ---------- called from other threads ----------
    def start(self):
        self.thread = threading.Thread(target=self._run, name="live-session", daemon=True)
        self.thread.start()

    def send_audio(self, frame: np.ndarray):
        if self.closed.is_set() or self.loop is None or self.queue is None:
            return
        try:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, frame.astype(np.int16).tobytes())
        except RuntimeError:
            pass                                  # loop already closed

    def close(self):
        if self.loop is not None and not self.closed.is_set():
            try:
                self.loop.call_soon_threadsafe(self.queue.put_nowait, None)
            except RuntimeError:
                pass

    def idle_for(self) -> float:
        return self.clock() - self.last_activity

    # ---------- session thread ----------
    def _run(self):
        try:
            asyncio.run(self._main())
        except Exception as e:                    # pragma: no cover - _main catches its own
            self.error = self.error or e
        finally:
            self.closed.set()
            self.ready.set()
            try:
                self.on_closed(self.error, self.replied)
            except Exception:
                log.exception("live on_closed failed")

    async def _main(self):
        self.loop = asyncio.get_running_loop()
        self.queue = asyncio.Queue()
        model = self.cfg.get("live_model") or "gemini-3.8-live"
        try:
            async with self.connect(model, self.config_factory()) as session:
                log.info("live session open (%s)", model)
                self.ready.set()
                sender = asyncio.create_task(self._send_loop(session))
                receiver = asyncio.create_task(self._receive_loop(session))
                done, pending = await asyncio.wait({sender, receiver},
                                                   return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
                for t in done:
                    if t.exception():
                        raise t.exception()
        except Exception as e:
            log.warning("live session error: %s", e, exc_info=True)
            self.error = e
        log.info("live session closed")

    async def _send_loop(self, session):
        from google.genai import types
        while True:
            data = await self.queue.get()
            if data is None:
                return
            await session.send_realtime_input(audio=types.Blob(data=data, mime_type=IN_MIME))

    async def _receive_loop(self, session):
        while True:
            got_any = False
            async for msg in session.receive():    # one model turn per iteration
                got_any = True
                await self._handle(session, msg)
            if not got_any:
                return                             # connection closed by the server

    async def _handle(self, session, msg):
        sc = getattr(msg, "server_content", None)
        if sc is not None:
            it = getattr(sc, "input_transcription", None)
            if it is not None and getattr(it, "text", None):
                self.user_text += it.text
                self.last_activity = self.clock()
                self.on_user_text(it.text)
            mt = getattr(sc, "model_turn", None)
            for part in (getattr(mt, "parts", None) or []):
                inline = getattr(part, "inline_data", None)
                if inline is not None and inline.data:
                    self.replied = True
                    self.last_activity = self.clock()
                    self.on_audio(np.frombuffer(inline.data, dtype=np.int16))
            ot = getattr(sc, "output_transcription", None)
            if ot is not None and getattr(ot, "text", None):
                self.replied = True
                self.last_activity = self.clock()
                self.on_reply_text(ot.text)
            if getattr(sc, "interrupted", False):
                self.last_activity = self.clock()
                self.on_interrupted()
            if getattr(sc, "turn_complete", False):
                self._begin_user_turn()
                self.last_activity = self.clock()
                self.on_turn_complete()
        tc = getattr(msg, "tool_call", None)
        if tc is not None and getattr(tc, "function_calls", None):
            await self._tool_calls(session, tc.function_calls)
        if getattr(msg, "go_away", None) is not None:
            log.info("live: server asked to end the session soon")

    def _begin_user_turn(self):
        """Tell the registry what the user just said (confirmations check it for a 'yes')."""
        text = self.user_text.strip()
        if text:
            self.registry.begin_turn(text)
            self.user_text = ""

    async def _tool_calls(self, session, calls):
        from google.genai import types
        self._begin_user_turn()
        responses, media = [], []
        for fc in calls:
            log.info("live tool call: %s", fc.name)
            self.last_activity = self.clock()
            result = await asyncio.to_thread(self.registry.call, fc.name, dict(fc.args or {}))
            media += result.pop("_media", None) or []
            responses.append(types.FunctionResponse(id=fc.id, name=fc.name, response=result))
            self.last_activity = self.clock()
        await session.send_tool_response(function_responses=responses)
        for m in media:                            # e.g. a screenshot from look_at_screen
            if str(getattr(m, "mime_type", "")).startswith("image/"):
                await session.send_realtime_input(video=_media_blob(m))


def build_config(cfg, registry, brain):
    """LiveConnectConfig with the same system prompt and tools as normal mode."""
    from google.genai import types
    from ..personality import system_prompt
    decls = [types.FunctionDeclaration(**d) for d in registry.declarations()]
    tools = [types.Tool(function_declarations=decls)] if decls else []
    tools.append(types.Tool(google_search=types.GoogleSearch()))
    prompt = system_prompt(cfg, registry.extra_context(), getattr(brain, "memory", ""))
    prompt += ("\n\nYou are in live voice mode: the user hears your voice directly. Keep replies "
               "short and natural. Before any action that needs confirmation, ask and wait for "
               "the user's spoken answer.")
    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=prompt,
        tools=tools,
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )


def make_session_factory(cfg, registry, brain):
    """Returns fn(**callbacks) -> LiveSession using the real Gemini client."""
    def connect(model, config):
        from google import genai
        brain._get_client()                        # same key check / friendly error as normal mode
        # Live only answers on v1alpha (v1beta and v1 give HTTP 404, checked 2026-11-17).
        client = genai.Client(api_key=brain.get_key(),
                              http_options={"api_version": cfg.get("live_api_version", "v1alpha")})
        return client.aio.live.connect(model=model, config=config)

    def factory(**callbacks):
        return LiveSession(cfg, registry, connect=connect,
                           config_factory=lambda: build_config(cfg, registry, brain), **callbacks)
    return factory
