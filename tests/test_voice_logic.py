from jarvis.core.voice.endpoint import DONE, SPEAKING, TIMEOUT, WAITING, EndpointSettings, Endpointer
from jarvis.core.voice.text import SentenceSplitter, is_stop_command, speakable, strip_wake_words


def test_splitter_streams_sentences():
    sp = SentenceSplitter()
    assert sp.feed("It's 3.5 degrees") == []
    assert sp.feed(" outside, sir. Bring a ") == ["It's 3.5 degrees outside, sir."]
    assert sp.feed("coat! Anything") == ["Bring a coat!"]
    assert sp.flush() == ["Anything"]


def test_splitter_releases_long_first_sentence_at_comma():
    sp = SentenceSplitter(first_max_chars=60)
    out = sp.feed("Well, the thing about this particular question, which is a long one, is")
    assert out and out[0].endswith(",")


def test_speakable_strips_markdown():
    assert speakable("**Bold** and [link](http://x) - `code`") == "Bold and link - code"


def test_stop_commands():
    for t in ["Stop.", "stop", "Jarvis, stop!", "be quiet please", "OK stop now", "That's enough."]:
        assert is_stop_command(t), t
    for t in ["stop the music in five minutes", "what time is it", ""]:
        assert not is_stop_command(t), t


def test_strip_wake_words():
    assert strip_wake_words("Hey Jarvis, what time is it?") == "what time is it?"
    assert strip_wake_words("jarvis what's up") == "what's up"
    assert strip_wake_words("what time is it") == "what time is it"


def run(ep, pattern):
    states = [ep.feed(x) for x in pattern]
    return states[-1]


def test_endpointer_start_and_end():
    ep = Endpointer(EndpointSettings(frame_ms=20, min_speech_ms=200, end_silence_ms=400))
    assert run(ep, [False] * 10) == WAITING
    assert run(ep, [True] * 10) == SPEAKING
    assert run(ep, [True] * 20 + [False] * 10) == SPEAKING   # short pause keeps going
    assert run(ep, [False] * 20) == DONE


def test_endpointer_ignores_clicks_and_times_out():
    ep = Endpointer(EndpointSettings(frame_ms=20, start_timeout_ms=1000, min_speech_ms=200))
    clicks = ([True, True] + [False] * 5) * 10      # short noise bursts never count as speech
    assert run(ep, clicks[:49]) == WAITING
    assert run(ep, [False] * 5) == TIMEOUT


def test_endpointer_max_length():
    ep = Endpointer(EndpointSettings(frame_ms=20, min_speech_ms=100, max_ms=1000))
    assert run(ep, [True] * 60) == DONE
