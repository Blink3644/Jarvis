"""Smart tool list: each request only describes the tools whose topic fits the question."""
from google.genai import types

from jarvis.core import config, providers
from jarvis.core.brain import Brain
from jarvis.core.registry import Registry, Tool
from jarvis.pc import basic_tools, routines

from .conftest import FakeClient
from .test_providers import FakeAdapter, entry, text


def tool(name):
    return Tool(name=name, description=name, handler=lambda: {"ok": True}, side_effect=False)


def make_reg():
    reg = Registry()
    basic_tools.register(reg)
    for t in basic_tools.TOPICS:
        reg.add_topic(*t)
    for n in ("spotify_play", "set_volume", "set_timer", "create_routine", "loose_tool"):
        reg.register(tool(n))
    reg.add_topic("music", r"play|song|spotify", ["spotify_play"])
    reg.add_topic("volume", r"volume|louder", ["set_volume"])
    reg.add_topic("timers", r"timer|minutes?", ["set_timer"])
    reg.add_topic("routine_edit", r"create\b.*\broutine", ["create_routine"], True)
    return reg


def names(reg):
    return {d["name"] for d in reg.declarations(for_turn=True)}


def test_matching_topic_sends_its_tools_plus_untopiced_ones():
    reg = make_reg()
    reg.begin_turn("play something funky")
    got = names(reg)
    assert "spotify_play" in got
    assert {"confirm_action", "end_conversation", "loose_tool"} <= got     # in no topic: always sent
    assert not {"set_volume", "set_timer", "create_routine", "get_time"} & got


def test_unclear_request_sends_every_tool():
    reg = make_reg()
    reg.begin_turn("Pick for me.")
    assert reg.turn_tools is None
    assert names(reg) == set(reg.tools)


def test_send_all_topic_sends_every_tool():
    reg = make_reg()
    reg.begin_turn("create a routine called movie night that plays a song")
    assert names(reg) == set(reg.tools)


def test_recently_used_topic_stays_for_follow_ups():
    reg = make_reg()
    reg.begin_turn("play a song")
    reg.call("spotify_play", {})
    reg.begin_turn("set a timer for 5 minutes")
    assert {"set_timer", "spotify_play"} <= names(reg)
    for _ in range(4):
        reg.begin_turn("set a timer for 5 minutes")
    assert "spotify_play" not in names(reg)            # forgotten after a few turns


def test_wake_word_does_not_count_as_a_topic():
    reg = make_reg()
    reg.begin_turn("Hey Jarvis, pick for me")
    assert reg.turn_tools is None                      # 'hey' alone mustn't narrow the list


def test_general_question_gets_only_the_basics():
    reg = make_reg()
    reg.begin_turn("who won the world cup in 2022?")
    assert names(reg) == {"confirm_action", "end_conversation", "loose_tool"}


def test_live_mode_and_off_switch_still_get_everything():
    reg = make_reg()
    reg.begin_turn("play a song")
    assert {d["name"] for d in reg.declarations()} == set(reg.tools)


def test_routine_names_point_to_the_routine_tools(monkeypatch):
    monkeypatch.setattr(routines, "load_routines", lambda: {"Gaming Mode": {}, "start music": {}})
    assert routines._mentions_routine("start my music please")
    assert routines._mentions_routine("gaming mode")
    assert not routines._mentions_routine("what's the weather")


def test_real_modules_cover_common_requests():
    from jarvis import __main__ as m
    reg = Registry()
    monkey = m.registry
    m.registry = reg
    try:
        m.load_modules(config.load())
    finally:
        m.registry = monkey
    for q, want in [("play something funky", "spotify_play"), ("turn it up a bit", "change_volume"),
                    ("set a timer for 5 minutes", "set_timer"), ("what's the weather like", "get_weather"),
                    ("remember my router password is on the fridge", "remember_note"),
                    ("how do I beat this boss", "look_at_screen"), ("open discord", "open_app"),
                    ("skip this song", "media_control"), ("what time is it?", "get_time"),
                    ("i like this song", "spotify_like_current")]:
        reg.begin_turn(q)
        assert reg.turn_tools is None or want in reg.turn_tools, (q, want)
        assert reg.turn_tools is not None, q            # and it did narrow the list


def make_brain(groq_script, smart=True):
    cfg = config.load()
    cfg["providers"] = [entry("groq", ["big"]), dict(providers.GEMINI_ENTRY)]
    cfg["smart_tools"] = smart
    reg = make_reg()
    fa = FakeAdapter(groq_script)
    b = Brain(cfg, reg, client_factory=lambda k: FakeClient([]), sleep=lambda s: None,
              get_key=lambda: "gem-key", get_provider_key=lambda pid: "k", adapter_factory=fa.factory)
    return b, fa


def fcall(name, cid="call_1"):
    return [types.Part(function_call=types.FunctionCall(id=cid, name=name, args={}))]


def test_brain_sends_picked_tools_and_old_calls_to_hidden_tools_as_text():
    b, fa = make_brain([("groq", fcall("spotify_play")), ("groq", text("Playing.")),
                        ("groq", text("Set."))])
    b.ask("play a song")
    first_tools = {t["name"] for t in fa.calls[0][4]}
    assert "spotify_play" in first_tools and "set_timer" not in first_tools
    for _ in range(4):                                 # let the music topic go stale
        b.registry.begin_turn("set a timer for 5 minutes")
    b.registry.turn -= 4                               # (keep the brain's own turn count honest)
    b.registry.topic_used["music"] = -10
    b.ask("set a timer for 5 minutes")
    tools = {t["name"] for t in fa.calls[-1][4]}
    assert "set_timer" in tools and "spotify_play" not in tools
    sent = fa.calls[-1][3]
    parts = [p for c in sent for p in c.parts or []]
    assert not any(p.function_call or p.function_response for p in parts)    # hidden tool -> text
    assert any("spotify_play" in (p.text or "") for p in parts)


def test_brain_off_switch_sends_every_tool():
    b, fa = make_brain([("groq", text("Hi."))], smart=False)
    b.ask("play a song")
    assert {t["name"] for t in fa.calls[0][4]} == set(b.registry.tools)


def test_like_in_a_question_is_not_about_music():
    from jarvis.pc import spotify
    reg = Registry()
    for n in ("spotify_like_current", "get_weather"):
        reg.register(tool(n))
    for t in spotify.TOPICS:
        reg.add_topic(t[0], t[1], [x for x in t[2] if x in reg.tools])
    reg.add_topic("weather", r"weather", ["get_weather"])
    reg.begin_turn("what's the weather like?")
    assert "spotify_like_current" not in reg.turn_tools
