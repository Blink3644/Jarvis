"""Game-profile registry. Modules (Minecraft mode, future mod profiles) register a profile;
core/pc ask which one matches the focused window. Core imports nothing from pc/minecraft."""
import logging
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)


@dataclass
class GameProfile:
    name: str
    matches: Callable[[dict], bool]            # info: {exe, exe_path, title, name}
    context: Callable[[], str | None] | None = None
    priority: int = 0


_profiles: list[GameProfile] = []


def register_profile(p: GameProfile) -> None:
    unregister_profile(p.name)
    _profiles.append(p)


def unregister_profile(name: str) -> None:
    _profiles[:] = [p for p in _profiles if p.name != name]


def match(info: dict) -> GameProfile | None:
    best = None
    for p in _profiles:
        try:
            if p.matches(info) and (best is None or p.priority > best.priority):
                best = p
        except Exception:
            log.exception("game profile %s failed", p.name)
    return best
