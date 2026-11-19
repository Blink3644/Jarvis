"""Stage A1 test tool. The full PC tools arrive in Stage A3."""
import datetime

from jarvis.core.registry import Registry, Tool


def get_time() -> dict:
    now = datetime.datetime.now()
    return {"time": now.strftime("%H:%M"), "date": now.strftime("%A %d %B %Y")}


# Words that point to these tools; a request only describes the tools whose topic matches.
# "chat" and "question" carry no tools of their own: thanks/goodbyes and general questions then get only
# the always-sent tools (end_conversation, confirm_action) plus any other topic their words point to.
TOPICS = [
    ("time", r"time|date|day|today|clock|year|month|week", ["get_time"]),
    ("chat", r"thanks|thank|cheers|bye|goodbye|goodnight|night|that's\s+all|never\s*mind|nevermind|okay|ok|"
             r"cool|nice|great|perfect|awesome|hello|hi|hey|morning|evening", []),
    ("question", r"who|what|what's|whats|when|why|how|which|where|explain|tell\s+me|define|meaning|mean|"
                 r"is|are|does|did|can|could|should|would|will", []),
]


def register(reg: Registry) -> None:
    reg.register(Tool(
        name="get_time", side_effect=False,
        description="Get the current local time and date on the user's PC.",
        handler=get_time,
        module="pc",
    ))
