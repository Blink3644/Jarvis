import pytest

from jarvis.core import game_profiles as gp


@pytest.fixture(autouse=True)
def clean():
    saved = list(gp._profiles)
    gp._profiles.clear()
    yield
    gp._profiles[:] = saved


def test_match_none_and_best_priority():
    assert gp.match({"exe": "a.exe"}) is None
    gp.register_profile(gp.GameProfile("Low", lambda i: True, priority=0))
    gp.register_profile(gp.GameProfile("High", lambda i: True, priority=5))
    gp.register_profile(gp.GameProfile("Never", lambda i: False, priority=9))
    assert gp.match({"exe": "a.exe"}).name == "High"


def test_reregister_replaces_and_unregister():
    gp.register_profile(gp.GameProfile("X", lambda i: True))
    gp.register_profile(gp.GameProfile("X", lambda i: False))
    assert len(gp._profiles) == 1 and gp.match({}) is None
    gp.unregister_profile("X")
    assert gp._profiles == []


def test_failing_matcher_is_ignored():
    def boom(i):
        raise RuntimeError
    gp.register_profile(gp.GameProfile("Bad", boom))
    gp.register_profile(gp.GameProfile("Ok", lambda i: True))
    assert gp.match({}).name == "Ok"
