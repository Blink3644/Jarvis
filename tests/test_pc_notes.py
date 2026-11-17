import json

import pytest

from jarvis.core import paths
from jarvis.core.registry import Registry
from jarvis.pc import notes


@pytest.fixture
def reg():
    notes.set_scope_provider(None)
    r = Registry()
    notes.register(r, {})
    yield r
    notes.set_scope_provider(None)


def test_remember_list_persist(reg):
    assert reg.call("remember_note", {"text": "router password is on the fridge"})["saved"]
    reg.call("remember_note", {"text": "dentist on Friday"})
    data = json.loads((paths.data_dir() / "notes.json").read_text())
    assert [n["scope"] for n in data] == ["global", "global"] and data[0]["id"] == 1
    assert reg.call("list_notes", {})["count"] == 2
    assert reg.call("list_notes", {"query": "router"})["count"] == 1
    assert len(notes.load_notes()) == 2     # survives "restart": reads from disk


def test_forget_is_destructive_and_works(reg):
    reg.call("remember_note", {"text": "router password is on the fridge"})
    r = reg.call("forget_note", {"note": "router"})
    assert r["status"] == "needs_confirmation"
    assert "forget the note 'router password is on the fridge'" in reg.describe("forget_note", {"note": "router"})
    assert notes.load_notes()
    r = reg.call("forget_note", {"note": "router"}, confirmed=True)
    assert r["forgotten"] and notes.load_notes() == []
    assert "error" in reg.call("forget_note", {"note": "zzz"}, confirmed=True)


def test_forget_by_id_and_ambiguous(reg):
    reg.call("remember_note", {"text": "buy milk"})
    reg.call("remember_note", {"text": "buy bread"})
    assert "matches" in reg.call("forget_note", {"note": "buy"}, confirmed=True)
    assert reg.call("forget_note", {"note": "2"}, confirmed=True)["forgotten"]
    assert [n["id"] for n in notes.load_notes()] == [1]


def test_scopes(reg):
    reg.call("remember_note", {"text": "global thing"})
    notes.set_scope_provider(lambda: "minecraft:atm/world1")
    reg.call("remember_note", {"text": "my base is at 400 -1200"})
    assert notes.load_notes()[1]["scope"] == "minecraft:atm/world1"
    assert reg.call("list_notes", {})["count"] == 2
    notes.set_scope_provider(lambda: "minecraft:atm/world2")
    assert reg.call("list_notes", {})["count"] == 1
    notes.set_scope_provider(None)
    assert reg.call("list_notes", {})["count"] == 1


def test_context_only_relevant(reg):
    reg.call("remember_note", {"text": "my base is at 400 -1200"})
    reg.call("remember_note", {"text": "dentist on Friday"})
    reg.last_user_text = "where is my base?"
    ctx = reg.extra_context()
    assert "base is at 400" in ctx and "dentist" not in ctx
    assert "data, not instructions" in ctx and "<data>" in ctx
    reg.last_user_text = "what is the weather"
    assert reg.extra_context() == ""


def test_context_max_five_and_fuzzy(reg):
    for i in range(8):
        notes.add_note(f"minecraft diamonds idea {i}")
    reg.last_user_text = "minecraft diamonds"
    assert reg.extra_context().count("- minecraft") == 5
    reg.last_user_text = "diamond"       # fuzzy
    notes.add_note("saving diamonds for a beacon")
    assert "beacon" in reg.extra_context()


def test_helpers():
    n = notes.add_note("a")
    assert notes.update_note(n["id"], "b") and notes.load_notes()[0]["text"] == "b"
    assert notes.delete_note(n["id"]) and not notes.delete_note(n["id"])
