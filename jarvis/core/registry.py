"""Tool/plugin registry. Modules (pc, minecraft, ...) register tools and prompt context here.

core never imports those modules; they call register() themselves.
Gemini can only act through tools registered here.
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)


@dataclass
class Tool:
    name: str
    description: str
    handler: Callable[..., Any]
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})
    destructive: bool = False   # needs confirmation (enforced from Stage A3)
    module: str = "core"


class Registry:
    def __init__(self):
        self.tools: dict[str, Tool] = {}
        self.context_providers: list[Callable[[], str]] = []
        self.hint_words: set[str] = set()

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

    def declarations(self) -> list[dict]:
        return [{"name": t.name, "description": t.description,
                 "parameters_json_schema": t.parameters} for t in self.tools.values()]

    def call(self, name: str, args: dict | None) -> dict:
        tool = self.tools.get(name)
        if tool is None:
            return {"error": f"Unknown tool {name}"}
        try:
            result = tool.handler(**(args or {}))
        except Exception as e:
            log.exception("tool %s failed", name)
            return {"error": f"{type(e).__name__}: {e}"}
        return result if isinstance(result, dict) else {"result": result}


registry = Registry()
