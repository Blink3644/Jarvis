"""Stage A1 test tool. The full PC tools arrive in Stage A3."""
import datetime

from jarvis.core.registry import Registry, Tool


def get_time() -> dict:
    now = datetime.datetime.now()
    return {"time": now.strftime("%H:%M"), "date": now.strftime("%A %d %B %Y")}


def register(reg: Registry) -> None:
    reg.register(Tool(
        name="get_time", side_effect=False,
        description="Get the current local time and date on the user's PC.",
        handler=get_time,
        module="pc",
    ))
