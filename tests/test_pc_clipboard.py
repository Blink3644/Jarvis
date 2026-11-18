import logging
import sys

import pytest

from jarvis.core.registry import Registry
from jarvis.pc import clipboard


def make_reg(fail=False):
    got = []

    def setter(text):
        if fail:
            raise OSError("busy")
        got.append(text)

    reg = Registry()
    clipboard.register(reg, {}, setter=setter)
    return reg, got


def test_copies_given_text():
    reg, got = make_reg()
    r = reg.call("copy_to_clipboard", {"text": "Sunny, 21 degrees"})
    assert r["copied"] and r["characters"] == 17 and got == ["Sunny, 21 degrees"]


def test_last_answer_uses_previous_reply():
    reg, got = make_reg()
    reg.last_reply = "The capital of France is Paris."
    assert reg.call("copy_to_clipboard", {"last_answer": True})["copied"]
    assert reg.call("copy_to_clipboard", {})["copied"]          # no text at all -> last reply too
    assert got == ["The capital of France is Paris."] * 2


def test_nothing_to_copy():
    reg, got = make_reg()
    assert "error" in reg.call("copy_to_clipboard", {"last_answer": True})
    assert got == []


def test_long_text_is_cut_to_max():
    reg, got = make_reg()
    r = reg.call("copy_to_clipboard", {"text": "x" * (clipboard.MAX_CHARS + 50)})
    assert r["truncated_to"] == clipboard.MAX_CHARS and len(got[0]) == clipboard.MAX_CHARS


def test_failure_is_friendly_and_text_never_logged(caplog):
    reg, _ = make_reg(fail=True)
    with caplog.at_level(logging.DEBUG):
        r = reg.call("copy_to_clipboard", {"text": "secret router password 1234"})
    assert "error" in r and "clipboard" in r["error"]
    assert "secret" not in caplog.text
    reg2, _ = make_reg()
    with caplog.at_level(logging.DEBUG):
        reg2.call("copy_to_clipboard", {"text": "secret router password 1234"})
    assert "secret" not in caplog.text


def test_not_destructive_and_not_visual():
    reg, _ = make_reg()
    tool = reg.tools["copy_to_clipboard"]
    assert tool.destructive is False and tool.visual is False and tool.module == "pc"


def test_brain_sets_last_reply():
    from jarvis.core import brain
    assert "self.registry.last_reply = final" in open(brain.__file__, encoding="utf-8").read()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows clipboard")
def test_real_clipboard_round_trip():
    import win32clipboard as wc

    def read():
        wc.OpenClipboard()
        try:
            return wc.GetClipboardData(wc.CF_UNICODETEXT) if \
                wc.IsClipboardFormatAvailable(wc.CF_UNICODETEXT) else None
        finally:
            wc.CloseClipboard()

    before = read()
    try:
        clipboard.set_clipboard("Jarvis test ✓ café")
        assert read() == "Jarvis test ✓ café"
    finally:
        if before is not None:                     # put the user's clipboard back
            clipboard.set_clipboard(before)
