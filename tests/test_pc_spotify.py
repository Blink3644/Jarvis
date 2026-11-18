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
