"""Spotify tools with a fake HTTP layer (no network)."""
import base64
import hashlib
import urllib.parse

from jarvis.pc import spotify as sp


class R:
    def __init__(self, status=200, data=None):
        self.status_code, self._d = status, data
        self.content = b"x" if data is not None else b""

    def json(self):
        return self._d


class FakeHttp:
    def __init__(self, routes):
        self.routes, self.calls, self.token_posts = routes, [], []

    def post(self, url, data=None, timeout=None):
        self.token_posts.append(data)
        return R(200, {"access_token": "AT", "expires_in": 3600})

    def request(self, method, url, params=None, json=None, timeout=None, headers=None):
        path = url.replace(sp.API, "")
        self.calls.append((method, path, params, json))
        r = self.routes.get((method, path), R(204))
        return r.pop(0) if isinstance(r, list) else r


def tools(routes, opened=None):
    http = FakeHttp(routes)
    c = sp.Client("cid", http=http, refresh_fn=lambda: "RT", save_fn=lambda t: None)
    t = sp.make_tools({}, client_fn=lambda: c, sleep=lambda s: None, wait_s=3,
                      open_spotify=lambda: (opened.append(1) if opened is not None else None))
    return t, http


PC = {"devices": [{"id": "phone", "type": "Smartphone"}, {"id": "pc", "type": "Computer"}]}


def test_pkce_challenge_matches_verifier():
    v, ch = sp.make_pkce()
    assert 43 <= len(v) <= 128
    assert ch == base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    q = urllib.parse.parse_qs(urllib.parse.urlparse(sp.auth_url("cid", ch, "st")).query)
    assert q["redirect_uri"] == ["http://127.0.0.1:8765/callback"] and q["code_challenge_method"] == ["S256"]
    assert "localhost" not in sp.REDIRECT_URI


def test_play_song_on_computer_device():
    t, http = tools({("GET", "/search"): R(200, {"tracks": {"items": [
        {"uri": "spotify:track:1", "name": "Bohemian Rhapsody", "artists": [{"name": "Queen"}]}]}}),
        ("GET", "/me/player/devices"): R(200, PC)})
    assert t["spotify_play"]("bohemian rhapsody") == {"playing": "Bohemian Rhapsody by Queen", "kind": "song"}
    assert ("PUT", "/me/player/play", {"device_id": "pc"}, {"uris": ["spotify:track:1"]}) in http.calls


def test_my_playlist_fuzzy_and_context_uri():
    t, http = tools({("GET", "/me/playlists"): R(200, {"items": [
        {"uri": "spotify:playlist:a", "name": "Chill"}, None, {"uri": "spotify:playlist:g", "name": "Gym Mix"}],
        "next": None}),
        ("GET", "/me/player/devices"): R(200, {"devices": [{"id": "d", "is_active": True}]})})
    assert t["spotify_play"]("gim mix", "my_playlist")["playing"] == "Gym Mix"
    assert ("PUT", "/me/player/play", {"device_id": "d"}, {"context_uri": "spotify:playlist:g"}) in http.calls
    assert "couldn't find" in t["spotify_play"]("zzzz qqq", "my_playlist")["error"]


def test_playlist_search_skips_null_items():
    t, http = tools({("GET", "/search"): R(200, {"playlists": {"items": [None, {"uri": "spotify:playlist:x",
                                                                              "name": "Lofi"}]}}),
                     ("GET", "/me/player/devices"): R(200, PC)})
    assert t["spotify_play"]("lofi", "playlist")["playing"] == "Lofi"


def test_no_device_opens_spotify_then_waits():
    opened = []
    t, http = tools({("GET", "/search"): R(200, {"artists": {"items": [{"uri": "spotify:artist:d",
                                                                         "name": "Daft Punk"}]}}),
                     ("GET", "/me/player/devices"): [R(200, {"devices": []}), R(200, {"devices": []}), R(200, PC)]},
                    opened)
    assert t["spotify_play"]("daft punk", "artist")["playing"] == "Daft Punk"
    assert opened == [1]


def test_no_device_gives_up_plainly():
    t, _ = tools({("GET", "/search"): R(200, {"tracks": {"items": [{"uri": "u", "name": "x"}]}}),
                  ("GET", "/me/player/devices"): R(200, {"devices": []})}, [])
    assert "didn't show up" in t["spotify_play"]("x")["error"]


def test_queue_like_now_playing_shuffle_repeat():
    song = {"uri": "spotify:track:9", "name": "One More Time", "artists": [{"name": "Daft Punk"}],
            "album": {"name": "Discovery"}}
    t, http = tools({("GET", "/search"): R(200, {"tracks": {"items": [song]}}),
                     ("GET", "/me/player/currently-playing"): R(200, {"item": song, "is_playing": True})})
    assert t["spotify_queue"]("one more time") == {"queued": "One More Time by Daft Punk"}
    assert ("POST", "/me/player/queue", {"uri": "spotify:track:9"}, None) in http.calls
    assert t["spotify_now_playing"]()["album"] == "Discovery"
    assert t["spotify_like_current"]() == {"liked": "One More Time by Daft Punk"}
    assert ("PUT", "/me/library", {"uris": "spotify:track:9"}, None) in http.calls
    assert t["spotify_shuffle"](True) == {"shuffle": True}
    assert t["spotify_repeat"]("track") == {"repeat": "track"}
    assert t["spotify_repeat"]("bogus") == {"repeat": "off"}


def test_nothing_playing_and_errors_are_plain():
    t, _ = tools({("GET", "/me/player/currently-playing"): R(204),
                  ("POST", "/me/player/queue"): R(404, {}),
                  ("GET", "/search"): R(200, {"tracks": {"items": [{"uri": "u", "name": "x"}]}}),
                  ("PUT", "/me/player/shuffle"): R(403, {})})
    assert t["spotify_now_playing"]()["playing"] is None
    assert "nothing to like" in t["spotify_like_current"]()["error"]
    assert "Start something playing" in t["spotify_queue"]("x")["error"]
    assert "Premium" in t["spotify_shuffle"](False)["error"]


def test_403_says_the_real_reason():
    # Premium account that isn't on the owner's Spotify app user list (Development Mode allowlist)
    t, _ = tools({("PUT", "/me/player/shuffle"): R(403, {"error": {
        "status": 403,
        "message": "Check settings on developer.spotify.com/dashboard, the user may not be registered."}})})
    err = t["spotify_shuffle"](False)["error"]
    assert "isn't on the Jarvis Spotify app's user list" in err and "Premium" not in err
    # Free account
    t, _ = tools({("PUT", "/me/player/shuffle"): R(403, {"error": {
        "status": 403, "message": "Player command failed: Premium required", "reason": "PREMIUM_REQUIRED"}})})
    err = t["spotify_shuffle"](False)["error"]
    assert "needs Spotify Premium" in err and "user list" not in err
    # Body that isn't JSON: falls back to naming both causes, never crashes
    class Bad(R):
        def json(self):
            raise ValueError("not json")
    t, _ = tools({("PUT", "/me/player/shuffle"): Bad(403, "x")})
    err = t["spotify_shuffle"](False)["error"]
    assert "Premium" in err and "user list" in err


def test_401_refreshes_token_once():
    t, http = tools({("PUT", "/me/player/shuffle"): [R(401, {}), R(204)]})
    assert t["spotify_shuffle"](True) == {"shuffle": True}
    assert len(http.token_posts) == 2 and http.token_posts[0]["grant_type"] == "refresh_token"


def test_not_connected_message(monkeypatch):
    c = sp.Client("cid", http=FakeHttp({}), refresh_fn=lambda: None)
    t = sp.make_tools({}, client_fn=lambda: c)
    assert "isn't connected" in t["spotify_shuffle"](True)["error"]


def test_client_id_override_and_availability(monkeypatch):
    monkeypatch.setattr(sp, "BUILTIN_CLIENT_ID", "built")
    assert sp.client_id({}) == "built" and sp.client_id({"spotify_client_id": " mine "}) == "mine"
    monkeypatch.setattr(sp, "get_refresh_token", lambda: None)
    assert not sp.available({})
    monkeypatch.setattr(sp, "get_refresh_token", lambda: "RT")
    assert sp.available({})
    monkeypatch.setattr(sp, "BUILTIN_CLIENT_ID", "")
    assert not sp.available({})


def test_volume_set_change_read_and_clamp():
    player = {"device": {"id": "pc", "name": "PC", "volume_percent": 50, "supports_volume": True}}
    t, http = tools({("GET", "/me/player"): R(200, player)})
    assert t["spotify_volume"](percent=30) == {"spotify_volume": 30}
    assert ("PUT", "/me/player/volume", {"volume_percent": 30, "device_id": "pc"}, None) in http.calls
    assert t["spotify_volume"](delta=-10) == {"spotify_volume": 40}
    assert t["spotify_volume"](delta=80) == {"spotify_volume": 100}
    assert t["spotify_volume"]() == {"spotify_volume": 50}


def test_volume_no_device_and_unsupported_device():
    t, _ = tools({("GET", "/me/player"): R(204)})
    assert "isn't playing" in t["spotify_volume"](percent=20)["error"]
    t, _ = tools({("GET", "/me/player"): R(200, {"device": {"id": "x", "name": "Speaker",
                                                             "supports_volume": False}})})
    assert "Speaker" in t["spotify_volume"](percent=20)["error"]


def test_music_volume_words_pick_spotify_volume():
    from jarvis.core.registry import Registry
    from jarvis.pc import media
    r = Registry()
    media.register(r, {})
    sp.register(r, {})
    for mod in (media, sp):
        for name, words, names in mod.TOPICS:
            r.add_topic(name, words, names)
    for said in ("turn the music down", "make the music louder", "spotify volume to 30"):
        assert "spotify_volume" in r.pick_tools(said), said


def test_closed_spotify_is_opened_before_searching_and_announced():
    order, said = [], []
    http = FakeHttp({("GET", "/search"): R(200, {"tracks": {"items": [{"uri": "u", "name": "x"}]}}),
                     ("GET", "/me/player/devices"): [R(200, {"devices": []}), R(200, PC)]})
    real_request = http.request

    def request(method, url, **kw):
        order.append(url.replace(sp.API, ""))
        return real_request(method, url, **kw)
    http.request = request
    c = sp.Client("cid", http=http, refresh_fn=lambda: "RT", save_fn=lambda t: None)
    t = sp.make_tools({}, client_fn=lambda: c, sleep=lambda s: None, wait_s=3,
                      open_spotify=lambda: order.append("OPEN"), announce=said.append)
    assert t["spotify_play"]("x")["playing"] == "x"
    assert order.index("OPEN") < order.index("/search")      # Spotify loads while we search
    assert said == ["Opening Spotify, one moment."]
    assert ("PUT", "/me/player/play", {"device_id": "pc"}, {"uris": ["u"]}) in http.calls


def test_running_spotify_is_not_announced():
    said = []
    http = FakeHttp({("GET", "/search"): R(200, {"tracks": {"items": [{"uri": "u", "name": "x"}]}}),
                     ("GET", "/me/player/devices"): R(200, PC)})
    c = sp.Client("cid", http=http, refresh_fn=lambda: "RT", save_fn=lambda t: None)
    t = sp.make_tools({}, client_fn=lambda: c, open_spotify=lambda: None, announce=said.append)
    t["spotify_play"]("x")
    assert said == []


def test_register_adds_a_warm_up_and_warm_ups_never_raise():
    from jarvis.core.registry import Registry
    reg = Registry()
    sp.register(reg, {})
    assert reg.warm_ups
    reg.warm_ups[:] = [lambda: 1 / 0]
    reg.run_warm_ups()          # a failing warm-up is logged, not raised
