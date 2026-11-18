"""'Thanks Jarvis' ends the conversation: a quick sign-off and no follow-up listening."""
import pytest

from jarvis.core.brain import Reply
from jarvis.core.personality import sign_off
from jarvis.core.registry import Registry
from jarvis.core.voice import assistant as A
from jarvis.core.voice.text import is_closing_remark

from tests.test_voice_assistant import SIL, SPEECH, WAKE, make, wait_for


@pytest.mark.parametrize("text", [
    "Thanks Jarvis", "thanks jarvis.", "Thank you, Jarvis!", "Hey Jarvis, thanks", "cheers mate",
    "ok thanks", "Perfect, thank you", "great thanks", "That's all", "that'll be all, Jarvis",
    "No, that's it", "nope that's everything", "goodbye", "bye Jarvis", "thanks so much",
    "awesome thanks jarvis", "that's all for now", "much appreciated", "I'm good thanks",
    "That’s all",
])
def test_closing_remarks(text):
    assert is_closing_remark(text)


@pytest.mark.parametrize("text", [
    "thanks, now turn the volume down", "thank you, what's the weather tomorrow",
    "open Spotify", "great", "perfect", "ok", "no", "later", "yes thanks", "yeah thanks",
    "thanks for the help, can you also set a timer", "", "what time is it",
    "is that all you can do",
])
def test_not_closing_remarks(text):
    assert not is_closing_remark(text)


def test_sign_off_matches_personality():
    assert sign_off({"personality": "butler", "user_title": "boss"}, "thanks jarvis") == "My pleasure, boss."
    assert sign_off({"personality": "butler"}, "that's all") == "Very good, sir."
    assert sign_off({"personality": "casual"}, "cheers") == "No worries!"
    assert sign_off({"personality": "direct"}, "thanks") == "Okay."


def test_end_conversation_tool_is_read_only_and_resets_each_turn():
    reg = Registry()
    tool = reg.tools["end_conversation"]
    assert tool.side_effect is False and tool.brings_data is False
    reg.begin_turn("thanks, that's all")
    reg.call("end_conversation", {})
    assert reg.conversation_over
    reg.begin_turn("what time is it")
    assert not reg.conversation_over


def test_thanks_in_followup_signs_off_without_ai_and_stops_listening():
    va, mic, sp, brain, ui = make(["Jarvis what time is it", "Thanks Jarvis"], ["It's noon, sir."])
    try:
        mic.feed(WAKE, *[SPEECH] * 10, *[SIL] * 12)
        assert wait_for(lambda: va.state == A.FOLLOWUP)
        mic.feed(*[SIL] * 8, *[SPEECH] * 10, *[SIL] * 12)
        assert wait_for(lambda: sp.said[-1:] == ["My pleasure, sir."])
        assert wait_for(lambda: va.state == A.IDLE)
        assert brain.asked == ["what time is it"]           # no AI call for the thanks
        assert A.FOLLOWUP not in ui.states[ui.states.index(A.BUSY, ui.states.index(A.FOLLOWUP)):]
    finally:
        va.stop()


def test_thanks_after_a_question_goes_to_the_ai():
    """'Shall I close Chrome?' -> 'thanks' is ambiguous, so the AI decides with the context."""
    va, mic, sp, brain, ui = make(["Jarvis thanks"], ["You're welcome."])
    va.registry.last_reply = "Shall I close Chrome?"
    try:
        mic.feed(WAKE, *[SPEECH] * 10, *[SIL] * 12)
        assert wait_for(lambda: brain.asked == ["thanks"])
    finally:
        va.stop()


def test_ai_end_conversation_skips_followup():
    va, mic, sp, brain, ui = make(["Jarvis lovely, that sorts me out for today"], ["Glad to help, sir."])
    reg = va.registry

    def ask(text, on_text=None, on_notice=None):
        brain.asked.append(text)
        reg.begin_turn(text)
        reg.call("end_conversation", {})
        on_text("Glad to help, sir.")
        return Reply("Glad to help, sir.")

    brain.ask = ask
    try:
        mic.feed(WAKE, *[SPEECH] * 10, *[SIL] * 12)
        assert wait_for(lambda: sp.said == ["Glad to help, sir."])
        assert wait_for(lambda: va.state == A.IDLE)
        assert A.FOLLOWUP not in ui.states
    finally:
        va.stop()
