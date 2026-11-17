import pytest

from jarvis.core.registry import Registry, Tool
from jarvis.pc import routines

LOG = []


def make_reg():
    LOG.clear()
    reg = Registry()
    reg.register(Tool("set_volume", "v", lambda level: LOG.append(("vol", level)) or {"ok": True},
                      {"type": "object", "properties": {"level": {"type": "integer"}}, "required": ["level"]},
                      module="pc"))
    reg.register(Tool("open_app", "o", lambda name: LOG.append(("open", name)) or {"ok": True},
                      {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
                      confirm_text=lambda a: f"open {a['name']}", module="pc"))
    reg.register(Tool("close_app", "c", lambda name: LOG.append(("close", name)) or {"ok": True},
                      {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
                      destructive=True, confirm_text=lambda a: f"close {a['name']}", module="pc"))
    reg.register(Tool("flaky", "f", lambda: {"error": "boom"}, module="pc"))
    reg.register(Tool("look_at_screen", "l", lambda: {"ok": 1}, module="pc"))
    routines.register(reg, {})
    return reg


GAMING = [{"tool": "close_app", "args": {"name": "Chrome"}}, {"tool": "open_app", "args": {"name": "Discord"}},
          {"tool": "set_volume", "args": {"level": 40}}]


def test_create_confirm_readback_and_save():
    reg = make_reg()
    r = reg.call("create_routine", {"name": "gaming mode", "steps": GAMING})
    assert r["status"] == "needs_confirmation"
    assert reg.describe("create_routine", {"name": "gaming mode", "steps": GAMING}) == \
        "save routine 'gaming mode': close Chrome, open Discord, set volume (level 40)"
    assert routines.load_routines() == {}
    r = reg.call("create_routine", {"name": "gaming mode", "steps": GAMING}, confirmed=True)
    assert r["saved"] and "gaming mode" in routines.load_routines()


@pytest.mark.parametrize("steps,frag", [
    ([{"tool": "nope"}], "no tool called"),
    ([{"tool": "run_routine", "args": {"name": "x"}}], "can't be used"),
    ([{"tool": "look_at_screen"}], "can't be used"),
    ([{"tool": "set_volume", "args": {"lvl": 1}}], "no argument"),
    ([{"tool": "set_volume"}], "needs 'level'"),
    ([], "at least one"),
])
def test_invalid_steps_rejected_without_confirmation(steps, frag):
    reg = make_reg()
    r = reg.call("create_routine", {"name": "bad", "steps": steps})
    assert "status" not in r and frag in r["error"]
    assert routines.load_routines() == {}


def test_run_with_one_confirmation_and_continue_on_error():
    reg = make_reg()
    steps = GAMING + [{"tool": "flaky"}]
    reg.call("create_routine", {"name": "gaming mode", "steps": steps}, confirmed=True)
    r = reg.call("run_routine", {"name": "gamin mode"})        # fuzzy name
    assert r["status"] == "needs_confirmation"
    assert "close Chrome" in reg.describe("run_routine", {"name": "gaming mode"})
    assert "open Discord" not in reg.describe("run_routine", {"name": "gaming mode"})
    assert LOG == []
    r = reg.call("run_routine", {"name": "gaming mode"}, confirmed=True)
    assert LOG == [("close", "Chrome"), ("open", "Discord"), ("vol", 40)]
    assert r["steps_run"] == 4 and r["failed"] == 1


def test_non_destructive_runs_directly_and_skip_confirm():
    reg = make_reg()
    reg.call("create_routine", {"name": "quiet", "steps": [GAMING[2]]}, confirmed=True)
    assert reg.call("run_routine", {"name": "quiet"})["steps_run"] == 1
    reg.call("create_routine", {"name": "gaming mode", "steps": GAMING}, confirmed=True)
    data = routines.load_routines()
    data["gaming mode"]["skip_confirm"] = True
    routines.save_routines(data)
    r = reg.call("run_routine", {"name": "gaming mode"})
    assert "status" not in r and r["failed"] == 0


def test_unknown_routine_and_delete():
    reg = make_reg()
    assert "error" in reg.call("run_routine", {"name": "nothing"})
    reg.call("create_routine", {"name": "a", "steps": [GAMING[2]]}, confirmed=True)
    assert reg.call("delete_routine", {"name": "a"})["status"] == "needs_confirmation"
    assert reg.call("delete_routine", {"name": "a"}, confirmed=True)["deleted"] == "a"
    assert reg.call("list_routines", {})["count"] == 0


def test_overwrite_keeps_skip_confirm_and_hand_edited_bad_step_fails_safely():
    reg = make_reg()
    reg.call("create_routine", {"name": "a", "steps": [GAMING[2]]}, confirmed=True)
    d = routines.load_routines()
    d["a"]["skip_confirm"] = True
    d["a"]["steps"].append({"tool": "run_routine", "args": {"name": "a"}})   # hand-edited, forbidden
    routines.save_routines(d)
    r = reg.call("run_routine", {"name": "a"})
    assert r["failed"] == 1 and r["steps_run"] == 2
    reg.call("create_routine", {"name": "A", "steps": [GAMING[2]]}, confirmed=True)
    assert list(routines.load_routines()) == ["A"] and routines.load_routines()["A"]["skip_confirm"] is True
