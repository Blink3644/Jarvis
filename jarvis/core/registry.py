"""Tool/plugin registry. Modules (pc, minecraft, ...) register tools and prompt context here.

core never imports those modules; they call register() themselves.
Gemini can only act through tools registered here.
"""
import itertools
import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)


@dataclass
class Tool:
    name: str
    description: str
    handler: Callable[..., Any]
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})
    destructive: bool | Callable[[dict], bool] = False   # needs the user's yes (or a test on the args)
    module: str = "core"
    confirm_text: Callable[[dict], str] | None = None   # e.g. "close Chrome"
    visual: bool = True         # False = obviously not about the screen (skip auto-look)
    available: Callable[[], bool] | None = None   # None = always; False hides it (e.g. not set up)
    # Safety (see CLAUDE.md): side_effect=False only for read-only tools. brings_data=True for tools that
    # bring outside text/images/sound into the chat (screen, PC audio, calendar, ClickUp); after one runs,
    # every side-effect tool in that turn needs the user's yes, so data can't trigger actions on its own.
    side_effect: bool = True
    brings_data: bool = False
    # gate(user_text) -> None if allowed, else a friendly error. Checked in code, not just the prompt.
    gate: Callable[[str], str | None] | None = None


@dataclass
class Media:
    """An image or sound a tool or the auto-look feature hands to Gemini."""
    mime: str
    data: bytes
    note: str = ""


_YES = re.compile(r"^\W*(yes|yeah|yep|yup|sure|ok|okay|do it|go ahead|confirm(ed)?|please do|affirmative|"
                  r"absolutely|of course|correct|that's right|go for it)\b", re.I)
_NO = re.compile(r"\b(no|nope|don't|do not|cancel|stop|wait|never ?mind)\b", re.I)


def is_affirmative(text: str) -> bool:
    text = re.sub(r"^\W*(hey\s+)?(jarvis|jervis)\W*", "", text or "", flags=re.I)
    return bool(_YES.search(text)) and not _NO.search(text)


class Registry:
    def __init__(self):
        self.tools: dict[str, Tool] = {}
        self.context_providers: list[Callable[[], str]] = []
        self.hint_words: set[str] = set()
        self.attachment_providers: list[Callable[[str], list[Media]]] = []
        # set by the front end: speak + show text later (timers); UI events (e.g. "pc_audio", {"on": True})
        self.announce: Callable[[str], None] = lambda text: log.info("announce: %s", text)
        self.ui_event: Callable[[str, dict], None] = lambda name, data: log.debug("ui event %s %s", name, data)
        # set when the user interrupts (says "stop" / presses push-to-talk); long tools should check it
        self.cancel = threading.Event()
        self.last_user_text = ""
        self.last_reply = ""        # Jarvis's previous answer (set by the brain), e.g. for "copy that"
        self.turn = 0
        self.tainted = ""           # what outside data entered this turn ("" = none)
        self.pending: dict[str, tuple[str, dict, int]] = {}
        self._ids = itertools.count(1)
        self.register(Tool(
            name="confirm_action",
            description="Run an action that was waiting for the user's confirmation. Call this ONLY "
                        "right after the user clearly said yes to your confirmation question. "
                        "If they said no, don't call anything.",
            handler=self._confirm, side_effect=False,
            parameters={"type": "object", "properties": {"confirm_id": {"type": "string"}},
                        "required": ["confirm_id"]},
        ))
        # The model decides from context that the user is finished; the voice loop then
        # stops follow-up listening (and closes a Live session) once the sign-off is spoken.
        self.conversation_over = False
        self.register(Tool(
            name="end_conversation",
            description="Call this when the user is wrapping up and wants nothing more right now "
                        "(e.g. 'thanks Jarvis', 'that's all', 'perfect, cheers', 'goodbye', 'never mind'). "
                        "Jarvis then stops listening for follow-ups. Reply with a very short sign-off. "
                        "If the same message also asks for something, do that first. Don't call it while "
                        "you are waiting for an answer from the user (e.g. a confirmation).",
            handler=self._end_conversation, side_effect=False, visual=False,
        ))

    # ---------- turns & confirmations ----------
    def begin_turn(self, user_text: str) -> None:
        """Called by the brain at the start of every user request."""
        self.turn += 1
        self.last_user_text = user_text or ""
        self.tainted = ""
        self.conversation_over = False
        self.cancel.clear()
        for k, (_, _, t) in list(self.pending.items()):
            if self.turn - t > 2:               # a confirmation question expires after 2 turns
                del self.pending[k]

    def _confirm(self, confirm_id: str) -> dict:
        item = self.pending.get(str(confirm_id))
        if not item:
            return {"error": "Nothing is waiting for confirmation (it may have expired). Ask again."}
        name, args, t = item
        if t >= self.turn:
            return {"error": "Ask the user first and wait for their answer."}
        if not is_affirmative(self.last_user_text):
            return {"error": "The user did not clearly say yes, so nothing was done."}
        del self.pending[str(confirm_id)]
        return self.call(name, args, confirmed=True)

    def _end_conversation(self) -> dict:
        self.conversation_over = True
        return {"ok": True, "note": "Follow-up listening is off. Give a brief sign-off, nothing more."}

    def describe(self, name: str, args: dict) -> str:
        tool = self.tools[name]
        if tool.confirm_text:
            try:
                return tool.confirm_text(args)
            except Exception:
                log.exception("confirm_text failed")
        return name.replace("_", " ")

    def attachments(self, user_text: str) -> list[Media]:
        out: list[Media] = []
        for p in self.attachment_providers:
            try:
                out += p(user_text) or []
            except Exception:
                log.exception("attachment provider failed")
        if out:
            self.tainted = self.tainted or "screen"     # an auto-look screenshot is outside data too
        return out

    def register(self, tool: Tool) -> None:
        if tool.name in self.tools:
            raise ValueError(f"tool {tool.name!r} already registered")
        self.tools[tool.name] = tool

    def add_context(self, provider: Callable[[], str]) -> None:
        self.context_providers.append(provider)

    def add_hint_words(self, words) -> None:
        self.hint_words.update(w for w in words if w)

    def extra_context(self) -> str:
        parts = []
        for p in self.context_providers:
            try:
                text = p()
            except Exception:
                log.exception("context provider failed")
                continue
            if text:
                parts.append(text.strip())
        return "\n\n".join(parts)

    def add_attachments(self, provider: Callable[[str], list[Media]]) -> None:
        self.attachment_providers.append(provider)

    @staticmethod
    def is_available(tool: Tool) -> bool:
        if tool.available is None:
            return True
        try:
            return bool(tool.available())
        except Exception:
            log.exception("availability check failed for %s", tool.name)
            return False

    def declarations(self) -> list[dict]:
        return [{"name": t.name, "description": t.description,
                 "parameters_json_schema": t.parameters}
                for t in self.tools.values() if self.is_available(t)]

    def call(self, name: str, args: dict | None, confirmed: bool = False) -> dict:
        tool = self.tools.get(name)
        if tool is None or not self.is_available(tool):
            return {"error": f"Unknown tool {name}"}
        args = dict(args or {})
        try:
            needs_yes = tool.destructive(args) if callable(tool.destructive) else tool.destructive
        except Exception:
            log.exception("destructive check failed for %s", name)
            needs_yes = True
        if tool.gate and not confirmed:
            try:
                why = tool.gate(self.last_user_text)
            except Exception:
                log.exception("gate check failed for %s", name)
                why = "I can only do that when you ask for it directly."
            if why:
                return {"error": why}
        if self.tainted and tool.side_effect and name != "confirm_action":
            needs_yes = True    # this turn read outside data (screen/audio/calendar...): ask the user first
        if needs_yes and not confirmed:
            cid = str(next(self._ids))
            self.pending[cid] = (name, args, self.turn)
            return {"status": "needs_confirmation", "confirm_id": cid,
                    "instruction": f"Nothing has been done yet. Ask the user to confirm: "
                                   f"{self.describe(name, args)}? If they say yes, call "
                                   f"confirm_action with confirm_id {cid}."}
        try:
            result = tool.handler(**args)
            if tool.brings_data:
                self.tainted = self.tainted or name
        except Exception as e:
            log.exception("tool %s failed", name)
            return {"error": f"{type(e).__name__}: {e}"}
        return result if isinstance(result, dict) else {"result": result}


registry = Registry()
