"""Spotify control (optional). Spotify Web API, Authorization Code + PKCE (no client secret).
Design: docs/superpowers/specs/2026-11-18-spotify-control-design.md

Checked 2026-11-18 against developer.spotify.com (Feb 2026 Development Mode rules):
  - app owner needs Premium; max 5 users per Client ID (added by email in the dashboard)
  - redirect: 'localhost' not allowed; http only for loopback IP literals (127.0.0.1)
  - GET /search ?q&type&limit (limit max 10)       GET /me/playlists ?limit=50&offset
  - GET /me/player/devices                          PUT /me/player/play ?device_id {uris | context_uri}
  - GET /me/player/currently-playing                POST /me/player/queue ?uri
  - PUT /me/player/shuffle ?state                   PUT /me/player/repeat ?state=off|track|context
  - PUT /me/library ?uris=<comma list>   (replaces PUT /me/tracks)
The refresh token lives in keyring (Jarvis / spotify_refresh_token) and is never logged.
"""
import base64
import hashlib
import logging
import secrets as pysecrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

try:
    import httpx
except ImportError:
    import httpx2 as httpx

from jarvis.core.registry import Registry, Tool

log = logging.getLogger(__name__)

# The project owner's Spotify app. Friends the owner adds in the Spotify dashboard can use it as-is;
# anyone else pastes their own Client ID in settings. A Client ID is not a secret.
BUILTIN_CLIENT_ID = "5f4f7c415c634c1b87127120ecabac74"
PORT = 8765
REDIRECT_URI = f"http://127.0.0.1:{PORT}/callback"
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
SCOPES = ("user-read-playback-state user-modify-playback-state user-read-currently-playing "
          "playlist-read-private playlist-read-collaborative user-library-modify")
KEYRING_USER = "spotify_refresh_token"
TIMEOUT = 8.0
KINDS = ("song", "my_playlist", "playlist", "album", "artist")
SEARCH_TYPE = {"song": "track", "playlist": "playlist", "album": "album", "artist": "artist"}


class SpotifyError(Exception):
    """Carries a plain message for the user."""


# ---------- settings / keyring ----------
def client_id(cfg) -> str:
    return (cfg.get("spotify_client_id") or "").strip() or BUILTIN_CLIENT_ID


def _keyring():
    import keyring
    return keyring


def get_refresh_token():
    try:
        return _keyring().get_password("Jarvis", KEYRING_USER)
    except Exception:
        return None


def set_refresh_token(tok: str) -> None:
    _keyring().set_password("Jarvis", KEYRING_USER, tok)


def disconnect() -> None:
    try:
        _keyring().delete_password("Jarvis", KEYRING_USER)
    except Exception:
        pass


def available(cfg) -> bool:
    return bool(client_id(cfg)) and bool(get_refresh_token())


# ---------- PKCE login ----------
def make_pkce() -> tuple[str, str]:
    verifier = pysecrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def auth_url(cid: str, challenge: str, state: str) -> str:
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": cid, "response_type": "code", "redirect_uri": REDIRECT_URI, "scope": SCOPES,
        "code_challenge_method": "S256", "code_challenge": challenge, "state": state})


def _token_request(http, data: dict) -> dict:
    try:
        r = http.post(TOKEN_URL, data=data, timeout=TIMEOUT)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
        raise SpotifyError("I can't reach Spotify. The internet may be down.")
    if r.status_code >= 400:
        log.warning("Spotify token request -> %s", r.status_code)
        raise SpotifyError("Spotify didn't accept the login. Reconnect Spotify in settings.")
    return r.json()


def wait_for_code(state: str, timeout: float = 180.0) -> str:
    """Serve one request on 127.0.0.1:PORT/callback and return the ?code=. Plain SpotifyError on failure."""
    got: dict = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if urllib.parse.urlparse(self.path).path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            got.update({k: v[0] for k, v in q.items()})
            ok = "code" in got and got.get("state") == state
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "Spotify is connected. You can close this tab." if ok else \
                "Spotify login didn't finish. Go back to Jarvis settings and try again."
            self.wfile.write(f"<html><body style='font-family:sans-serif'><h2>{msg}</h2></body></html>".encode())

        def log_message(self, *a):          # keep the code out of the log
            pass

    try:
        srv = HTTPServer(("127.0.0.1", PORT), H)
    except OSError:
        raise SpotifyError(f"Port {PORT} is busy, so I can't finish the Spotify login. Close other apps and retry.")
    srv.timeout = 1.0
    end = time.monotonic() + timeout
    try:
        while not got and time.monotonic() < end:
            srv.handle_request()
    finally:
        srv.server_close()
    if not got:
        raise SpotifyError("The Spotify login timed out. Click Connect again.")
    if got.get("error"):
        raise SpotifyError("Spotify login was cancelled.")
    if got.get("state") != state or "code" not in got:
        raise SpotifyError("Spotify login didn't match. Click Connect again.")
    return got["code"]


def connect(cid: str, open_browser=None, http=None, timeout: float = 180.0) -> str:
    """Full login: open the browser, catch the redirect, swap the code for tokens, save the refresh token."""
    if not cid:
        raise SpotifyError("Paste a Spotify Client ID first.")
    import webbrowser
    open_browser = open_browser or webbrowser.open
    verifier, challenge = make_pkce()
    state = pysecrets.token_urlsafe(16)
    result: dict = {}

    def serve():
        try:
            result["code"] = wait_for_code(state, timeout)
        except SpotifyError as e:
            result["err"] = e
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    time.sleep(0.2)
    open_browser(auth_url(cid, challenge, state))
    t.join(timeout + 5)
    if "err" in result:
        raise result["err"]
    if "code" not in result:
        raise SpotifyError("The Spotify login timed out. Click Connect again.")
    tok = _token_request(http or httpx, {"grant_type": "authorization_code", "code": result["code"],
                                         "redirect_uri": REDIRECT_URI, "client_id": cid,
                                         "code_verifier": verifier})
    if not tok.get("refresh_token"):
        raise SpotifyError("Spotify didn't give a login token. Try again.")
    set_refresh_token(tok["refresh_token"])
    return "Spotify connected."


# ---------- API client ----------
class Client:
    def __init__(self, cid, http=None, refresh_fn=None, save_fn=None):
        self.cid = cid
        self.http = http or httpx
        self.refresh_fn = refresh_fn or get_refresh_token
        self.save_fn = save_fn or set_refresh_token
        self.access, self.expires = None, 0.0

    def _token(self, force=False) -> str:
        if self.access and not force and time.time() < self.expires - 30:
            return self.access
        rt = self.refresh_fn()
        if not rt:
            raise SpotifyError("Spotify isn't connected. Connect it in settings, on the Integrations page.")
        tok = _token_request(self.http, {"grant_type": "refresh_token", "refresh_token": rt,
                                         "client_id": self.cid})
        self.access = tok["access_token"]
        self.expires = time.time() + float(tok.get("expires_in", 3600))
        if tok.get("refresh_token") and tok["refresh_token"] != rt:
            self.save_fn(tok["refresh_token"])
        return self.access

    def req(self, method, path, params=None, json=None, _retry=True):
        try:
            r = self.http.request(method, API + path, params=params, json=json, timeout=TIMEOUT,
                                  headers={"Authorization": f"Bearer {self._token()}"})
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
            raise SpotifyError("I can't reach Spotify. The internet may be down.")
        if r.status_code == 401 and _retry:
            self._token(force=True)
            return self.req(method, path, params, json, _retry=False)
        if r.status_code == 404 and path.startswith("/me/player"):
            raise NoDevice()
        if r.status_code == 403:
            log.warning("Spotify %s %s -> 403", method, path)
            raise SpotifyError("Spotify refused that. Playback control needs Spotify Premium, and your "
                               "account must be added to the Jarvis Spotify app.")
        if r.status_code == 429:
            raise SpotifyError("Spotify says too many requests. Try again in a minute.")
        if r.status_code >= 400:
            log.warning("Spotify %s %s -> %s", method, path, r.status_code)
            raise SpotifyError("Spotify gave an error. Try again in a moment.")
        if r.status_code == 204 or not r.content:
            return {}
        return r.json()


class NoDevice(SpotifyError):
    def __init__(self):
        super().__init__("Spotify isn't playing on any device. Open Spotify and try again.")


# ---------- pure helpers ----------
def first_item(result: dict, stype: str):
    for it in (result.get(stype + "s") or {}).get("items") or []:
        if it and it.get("uri"):           # playlist search can return null entries
            return it
    return None


def describe(item: dict) -> str:
    name = item.get("name", "")
    artists = ", ".join(a.get("name", "") for a in item.get("artists") or [])
    owner = (item.get("owner") or {}).get("display_name")
    if artists:
        return f"{name} by {artists}"
    return f"{name} ({owner})" if owner else name


def find_by_name(items: list[dict], name: str):
    name = (name or "").strip().lower()
    for it in items:
        if (it.get("name") or "").lower() == name:
            return it
    try:
        from rapidfuzz import fuzz, process
        best = process.extractOne(name, {i: (it.get("name") or "").lower() for i, it in enumerate(items)},
                                  scorer=fuzz.WRatio, score_cutoff=75)
        return items[best[2]] if best else None
    except ImportError:
        return next((it for it in items if name in (it.get("name") or "").lower()), None)


def pick_device(devices: list[dict]):
    if not devices:
        return None
    for d in devices:
        if d.get("is_active"):
            return d
    for d in devices:
        if (d.get("type") or "").lower() == "computer":
            return d
    return devices[0]


# ---------- tools ----------
def make_tools(cfg, client_fn=None, open_spotify=None, sleep=time.sleep, wait_s: float = 15.0):
    cache: dict = {}

    def client():
        if client_fn:
            return client_fn()
        cid = client_id(cfg)
        if cache.get("cid") != cid:
            cache["cid"], cache["c"] = cid, Client(cid)
        return cache["c"]

    def _open():
        if open_spotify:
            return open_spotify()
        from jarvis.pc import apps
        return apps.open_app(cfg, "spotify")

    def device_id(c) -> str:
        d = pick_device(c.req("GET", "/me/player/devices").get("devices", []))
        if d:
            return d["id"]
        _open()
        waited = 0.0
        while waited < wait_s:
            sleep(1.0)
            waited += 1.0
            d = pick_device(c.req("GET", "/me/player/devices").get("devices", []))
            if d:
                return d["id"]
        raise SpotifyError("I opened Spotify but it didn't show up in time. Try again in a few seconds.")

    def my_playlists(c) -> list[dict]:
        out = []
        for off in range(0, 200, 50):
            page = c.req("GET", "/me/playlists", params={"limit": 50, "offset": off})
            out += [p for p in page.get("items") or [] if p]
            if not page.get("next"):
                break
        return out

    def _run(fn):
        try:
            return fn()
        except SpotifyError as e:
            return {"error": str(e)}
        except Exception:
            log.exception("Spotify tool failed")
            return {"error": "Spotify gave an error. Try again in a moment."}

    def spotify_play(query: str, kind: str = "song") -> dict:
        kind = kind if kind in KINDS else "song"
        query = (query or "").strip()
        if not query:
            return {"error": "Tell me what to play."}

        def go():
            c = client()
            if kind == "my_playlist":
                item = find_by_name(my_playlists(c), query)
                if not item:
                    return {"error": f"I couldn't find a playlist of yours called {query}."}
            else:
                st = SEARCH_TYPE[kind]
                item = first_item(c.req("GET", "/search", params={"q": query, "type": st, "limit": 5}), st)
                if not item:
                    return {"error": f"Spotify found nothing for {query}."}
            body = {"uris": [item["uri"]]} if kind == "song" else {"context_uri": item["uri"]}
            c.req("PUT", "/me/player/play", params={"device_id": device_id(c)}, json=body)
            return {"playing": describe(item), "kind": kind}
        return _run(go)

    def spotify_queue(query: str) -> dict:
        def go():
            c = client()
            item = first_item(c.req("GET", "/search", params={"q": query, "type": "track", "limit": 5}), "track")
            if not item:
                return {"error": f"Spotify found nothing for {query}."}
            try:
                c.req("POST", "/me/player/queue", params={"uri": item["uri"]})
            except NoDevice:
                return {"error": "Start something playing on Spotify first, then I can queue songs."}
            return {"queued": describe(item)}
        return _run(go)

    def now(c):
        cur = c.req("GET", "/me/player/currently-playing")
        return cur.get("item") if cur else None

    def spotify_now_playing() -> dict:
        def go():
            c = client()
            try:
                cur = c.req("GET", "/me/player/currently-playing")
            except NoDevice:
                cur = {}
            item = (cur or {}).get("item")
            if not item:
                return {"playing": None, "message": "Nothing is playing on Spotify."}
            return {"playing": describe(item), "album": (item.get("album") or {}).get("name", ""),
                    "is_playing": bool(cur.get("is_playing"))}
        return _run(go)

    def spotify_like_current() -> dict:
        def go():
            c = client()
            try:
                item = now(c)
            except NoDevice:
                item = None
            if not item:
                return {"error": "Nothing is playing on Spotify, so there's nothing to like."}
            c.req("PUT", "/me/library", params={"uris": item["uri"]})
            return {"liked": describe(item)}
        return _run(go)

    def spotify_shuffle(on: bool) -> dict:
        def go():
            client().req("PUT", "/me/player/shuffle", params={"state": "true" if on else "false"})
            return {"shuffle": bool(on)}
        return _run(go)

    def spotify_repeat(mode: str) -> dict:
        mode = mode if mode in ("off", "track", "context") else "off"

        def go():
            client().req("PUT", "/me/player/repeat", params={"state": mode})
            return {"repeat": mode}
        return _run(go)

    def spotify_volume(percent=None, delta=None) -> dict:
        def go():
            c = client()
            dev = (c.req("GET", "/me/player") or {}).get("device") or {}
            if not dev:
                raise NoDevice()
            if dev.get("supports_volume") is False:
                return {"error": f"Spotify can't change the volume on {dev.get('name') or 'that device'}."}
            cur = dev.get("volume_percent")
            if percent is None and delta is None:
                return {"spotify_volume": cur}
            new = float(percent) if percent is not None else (cur or 0) + float(delta)
            new = max(0, min(100, int(round(new))))
            c.req("PUT", "/me/player/volume", params={"volume_percent": new, "device_id": dev.get("id")})
            return {"spotify_volume": new}
        return _run(go)

    return {"spotify_volume": spotify_volume, "spotify_play": spotify_play, "spotify_queue": spotify_queue,
            "spotify_now_playing": spotify_now_playing, "spotify_like_current": spotify_like_current,
            "spotify_shuffle": spotify_shuffle, "spotify_repeat": spotify_repeat}


# Words that point to these tools; a request only describes the tools whose topic matches.
TOPICS = [("music", r"spotify|play|playing|pause|resume|skip|next|previous|song|songs|track|tracks|music|tune|"
                    r"tunes|playlist|playlists|album|artist|band|queue|shuffle|repeat|loop|like\s+(this|that|it)|liked|listen|"
                    r"listening|genre|vibe|vibes|funky|chill|jazz|rock|pop|rap|lofi|beat|beats",
            ["spotify_play", "spotify_queue", "spotify_shuffle", "spotify_repeat", "spotify_like_current",
             "spotify_now_playing", "spotify_volume"]),
          ("music_volume", r"volume|louder|quieter|softer|mute|muted|unmute|loud|quiet|turn\s+(it|that|this|the\s+\w+)?"
                           r"\s*(up|down)|crank|lower|raise", ["spotify_volume"])]


def register(reg: Registry, cfg: dict) -> None:
    t = make_tools(cfg)
    is_on = lambda: available(cfg)   # noqa: E731

    def T(name, description, parameters=None, **kw):
        reg.register(Tool(name=name, description=description, handler=t[name],
                          parameters=parameters or {"type": "object", "properties": {}},
                          module="pc", visual=False, available=is_on, **kw))

    T("spotify_play",
      "Play something on Spotify: a song, one of the user's own playlists (kind=my_playlist, use when "
      "they say 'my ... playlist'), a public playlist, an album or an artist. Opens Spotify if needed.",
      {"type": "object", "properties": {
          "query": {"type": "string", "description": "Song/playlist/album/artist name as said, "
                                                     "e.g. 'Bohemian Rhapsody Queen' or 'Gym'."},
          "kind": {"type": "string", "enum": list(KINDS)}}, "required": ["query"]})
    T("spotify_queue", "Add a song to the Spotify queue so it plays next.",
      {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]})
    T("spotify_now_playing", "Say what song is playing on Spotify right now.",
      side_effect=False, brings_data=True)
    T("spotify_like_current", "Add the song playing on Spotify to the user's Liked Songs.")
    T("spotify_shuffle", "Turn Spotify shuffle on (true) or off (false).",
      {"type": "object", "properties": {"on": {"type": "boolean"}}, "required": ["on"]})
    T("spotify_repeat", "Set Spotify repeat: off, track (repeat this song) or context (repeat the playlist/album).",
      {"type": "object", "properties": {"mode": {"type": "string", "enum": ["off", "track", "context"]}},
       "required": ["mode"]})
    T("spotify_volume",
      "Spotify's own volume (0-100), separate from the PC volume. Use this, not set_volume/change_volume, "
      "whenever the user means the music, song or Spotify ('turn the music down', 'music louder', "
      "'mute the music' = 0). Give percent to set it, delta (e.g. 10 or -10) to change it, or neither "
      "to read it.",
      {"type": "object", "properties": {"percent": {"type": "number"}, "delta": {"type": "number"}}})
