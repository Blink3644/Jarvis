from jarvis.core.voice.endpoint import DONE, SPEAKING, TIMEOUT, WAITING, EndpointSettings, Endpointer
from jarvis.core.voice.text import SentenceSplitter, is_stop_command, speakable, strip_wake_command, strip_wake_words


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


def test_strip_wake_command_drops_chatter_before_wake():
    assert strip_wake_command("Hey Jarvis, skip this song.") == "skip this song."
    assert strip_wake_command("so anyway. Hey Jarvis play something funky") == "play something funky"
    assert strip_wake_command("I made for my brother. Hey Jarvis stop") == "stop"
    assert strip_wake_command("Hey, Travis, pause music") == "pause music"
    # a later 'Jarvis' belongs to the command
    assert strip_wake_command("Hey Jarvis, open the Jarvis settings") == "open the Jarvis settings"
    assert strip_wake_command("what time is it") == "what time is it"
    assert strip_wake_command("") == ""
    # wake word not among the first words -> leave the text alone
    long = "one two three four five six seven eight nine ten jarvis eleven"
    assert strip_wake_command(long) == long


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


def test_looks_like_noise_catches_music_bleed():
    from jarvis.core.voice.text import looks_like_noise
    for junk in ["D" + "a" * 600, "Daaaaaa.", "la la la la", "Na na na na na.", "lalalala",
                 "dadadada", "Ooh, aaah!", "...", "", "  ", "♪♪", "Mm-mm.", "Uh, um.", "hmm",
                 "dum dum dum dum", "e", "Mmmmmmm"]:
        assert looks_like_noise(junk), junk


def test_looks_like_noise_keeps_real_short_replies():
    from jarvis.core.voice.text import looks_like_noise
    for real in ["Yes.", "No.", "Stop.", "Thanks.", "Thank you.", "What?", "50.", "Ok", "Yeah",
                 "Mm-hmm.", "Uh-huh.", "Huh?", "no no no no", "stop stop stop stop", "Hey Jarvis",
                 "down 10%.", "Set it to 25%.", "Yes, you should.", "go"]:
        assert not looks_like_noise(real), real
