import datetime
import json
import logging

from jarvis.core import config, logs, paths
from jarvis.core.history import HistoryStore, fallback_summary
from jarvis.core.registry import Registry, Tool
from jarvis.core.usage import Usage, next_reset, pacific_today

UTC = datetime.timezone.utc


def test_config_defaults_and_merge(jarvis_home):
    cfg = config.load()
    assert (jarvis_home / "config.json").exists()
    (jarvis_home / "config.json").write_text(json.dumps({"personality": "casual",
                                                         "daily_limits": {"x": 5}}))
    cfg = config.load()
    assert cfg["personality"] == "casual" and cfg["model_main"] == config.DEFAULTS["model_main"]
    assert cfg["daily_limits"]["x"] == 5 and config.DEFAULTS["model_main"] in cfg["daily_limits"]


def test_broken_config_falls_back(jarvis_home):
    (jarvis_home / "config.json").write_text("{not json")
    assert config.load()["personality"] == "butler"


def test_pacific_day_and_reset():
    # 07:30 UTC on the 18th is still the 17th in California (PST, UTC-8)
    t = datetime.datetime(2026, 11, 18, 7, 30, tzinfo=UTC)
    assert pacific_today(t) == "2026-11-17"
    assert next_reset(t) == datetime.datetime(2026, 11, 18, 8, 0, tzinfo=UTC)


def test_usage_warns_once_at_80_percent_and_rolls_over():
    now = [datetime.datetime(2026, 11, 17, 12, 0, tzinfo=UTC)]
    cfg = config.load()
    cfg["daily_limits"] = {"m": 10}
    u = Usage(cfg, clock=lambda: now[0])
    msgs = [u.record("m") for _ in range(10)]
    assert [i for i, m in enumerate(msgs) if m] == [7]   # 8th request = 80%
    assert u.exhausted("m")
    u2 = Usage(cfg, clock=lambda: now[0])                 # persisted
    assert u2.count("m") == 10
    now[0] += datetime.timedelta(days=1)
    assert u2.count("m") == 0 and not u2.exhausted("m")


def test_registry_errors_are_contained():
    reg = Registry()
    reg.register(Tool("boom", "x", handler=lambda: 1 / 0))
    reg.register(Tool("plain", "x", handler=lambda: 42))
    assert "ZeroDivisionError" in reg.call("boom", {})["error"]
    assert reg.call("plain", None) == {"result": 42}
    assert "Unknown tool" in reg.call("nope", {})["error"]
    reg.add_context(lambda: 1 / 0)
    reg.add_context(lambda: "ctx")
    assert reg.extra_context() == "ctx"


def test_log_redacts_key(jarvis_home):
    key = "AIzaSyFAKEFAKEFAKEFAKEFAKEFAKE1234"
    root = logging.getLogger()
    for h in [h for h in root.handlers if getattr(h, "_jarvis", False)]:
        root.removeHandler(h)
    logs.setup(get_key=lambda: key)
    logging.getLogger("t").error("bad call with %s and url ?key=abc123", key)
    for h in root.handlers:
        h.flush()
    text = (jarvis_home / "logs" / "jarvis.log").read_text()
    assert key not in text and "abc123" not in text and "[KEY]" in text
    for h in [h for h in root.handlers if getattr(h, "_jarvis", False)]:
        h.close()
        root.removeHandler(h)


def test_history_modes(jarvis_home):
    cfg = config.load()
    cfg["history_mode"] = "log"
    hs = HistoryStore(cfg)
    hs.log_line("You", "hello")
    assert any((jarvis_home / "history").glob("*.txt"))
    cfg["history_mode"] = "remember"
    cfg["remember_count"] = 2
    for i in range(3):
        HistoryStore(cfg).save_summary(f"s{i}")
    loaded = HistoryStore(cfg).load_summaries()
    assert "s0" not in loaded and "s1" in loaded and "s2" in loaded
    cfg["history_mode"] = "session"
    assert HistoryStore(cfg).load_summaries() == ""
    hs.clear_all()
    assert not (jarvis_home / "history").exists()
    assert not (jarvis_home / "recent_summaries.json").exists()


def test_fallback_summary():
    assert fallback_summary([("You", "weather?"), ("Jarvis", "Sunny")]) == "User asked about: weather?"
    assert fallback_summary([]) == ""


def test_core_does_not_import_minecraft():
    import pathlib
    import re
    core = pathlib.Path(paths.__file__).parent
    for f in core.glob("*.py"):
        assert not re.search(r"^\s*(from|import)\s+\S*minecraft", f.read_text(), re.M), f.name
