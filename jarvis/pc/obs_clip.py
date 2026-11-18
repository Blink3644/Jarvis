"""'Jarvis, clip that': save OBS's Replay Buffer (the last N seconds of video) through obs-websocket.

Uses OBS's own WebSocket server (built into OBS 28+, protocol v5, checked against
obsproject/obs-websocket docs/generated/protocol.md on 2026-11-18). No key presses are sent to any app or game.
The optional OBS WebSocket password lives in keyring (Windows Credential Manager), never in config or logs.
"""
import base64
import hashlib
import json
import logging
import os
import uuid

from jarvis.core.registry import Registry, Tool

log = logging.getLogger(__name__)

KEYRING_USER = "obs_password"
OP_HELLO, OP_IDENTIFY, OP_IDENTIFIED, OP_EVENT, OP_REQUEST, OP_RESPONSE = 0, 1, 2, 5, 6, 7
SUB_OUTPUTS = 1 << 6            # EventSubscription::Outputs (ReplayBufferSaved)
AUTH_FAILED = 4009              # WebSocketCloseCode::AuthenticationFailed

NOT_RUNNING = ("Can't reach OBS. OBS must be open, with Tools > WebSocket Server Settings > "
               "'Enable WebSocket server' ticked, and the port must match Jarvis settings > Integrations.")


class ObsError(Exception):
    """A plain-language problem to tell the user."""


def _keyring():
    import keyring
    return keyring


def get_password():
    try:
        return _keyring().get_password("Jarvis", KEYRING_USER)
    except Exception:
        return None


def set_password(pw: str) -> None:
    _keyring().set_password("Jarvis", KEYRING_USER, pw)


def clear_password() -> None:
    try:
        _keyring().delete_password("Jarvis", KEYRING_USER)
    except Exception:
        pass


def auth_string(password: str, salt: str, challenge: str) -> str:
    """Base64(SHA256(Base64(SHA256(password + salt)) + challenge)), per the obs-websocket v5 protocol."""
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


class ObsClient:
    """Tiny synchronous obs-websocket v5 client: identify, send requests, wait for one event."""

    def __init__(self, port=4455, password=None, timeout=4.0, connect=None):
        self.port, self.password, self.timeout = int(port), password or "", timeout
        self._connect = connect          # tests inject a fake socket factory
        self.ws = self._cm = None

    def __enter__(self):
        if self._connect is None:
            from websockets.sync.client import connect
            self._connect = connect
        try:
            self._cm = self._connect(f"ws://127.0.0.1:{self.port}", open_timeout=self.timeout)
            self.ws = self._cm.__enter__()
        except (OSError, TimeoutError) as e:
            log.info("OBS not reachable: %s", type(e).__name__)
            raise ObsError(NOT_RUNNING) from None
        hello = self._recv()
        if hello.get("op") != OP_HELLO:
            raise ObsError("OBS answered in a way I don't understand. Is it OBS 28 or newer?")
        ident = {"rpcVersion": 1, "eventSubscriptions": SUB_OUTPUTS}
        auth = hello.get("d", {}).get("authentication")
        if auth:
            if not self.password:
                raise ObsError("OBS wants a WebSocket password. Paste it in Jarvis settings > Integrations > "
                               "OBS (it's in OBS under Tools > WebSocket Server Settings > Show Connect Info).")
            ident["authentication"] = auth_string(self.password, auth["salt"], auth["challenge"])
        self._send(OP_IDENTIFY, ident)
        msg = self._recv(auth_check=True)
        if msg.get("op") != OP_IDENTIFIED:
            raise ObsError("OBS didn't accept the connection.")
        return self

    def __exit__(self, *exc):
        try:
            self._cm.__exit__(None, None, None)
        except Exception:
            pass

    def _send(self, op, d):
        self.ws.send(json.dumps({"op": op, "d": d}))

    def _recv(self, auth_check=False, timeout=None):
        try:
            raw = self.ws.recv(timeout=timeout or self.timeout)
        except TimeoutError:
            raise ObsError("OBS didn't answer in time.") from None
        except Exception as e:
            code = getattr(getattr(e, "rcvd", None), "code", None)
            if auth_check and code == AUTH_FAILED:
                raise ObsError("OBS rejected the password. Check the OBS password in Jarvis settings > "
                               "Integrations.") from None
            log.info("OBS connection dropped: %s %s", type(e).__name__, code)
            raise ObsError("The connection to OBS dropped.") from None
        return json.loads(raw)

    def request(self, rtype, data=None) -> dict:
        rid = uuid.uuid4().hex
        d = {"requestType": rtype, "requestId": rid}
        if data:
            d["requestData"] = data
        self._send(OP_REQUEST, d)
        while True:
            msg = self._recv()
            if msg.get("op") == OP_RESPONSE and msg["d"].get("requestId") == rid:
                st = msg["d"].get("requestStatus", {})
                if not st.get("result"):
                    raise ObsError(f"OBS said no to {rtype} (code {st.get('code')}: {st.get('comment', '')}).")
                return msg["d"].get("responseData") or {}

    def wait_event(self, etype, timeout=5.0):
        while True:
            msg = self._recv(timeout=timeout)
            if msg.get("op") == OP_EVENT and msg["d"].get("eventType") == etype:
                return msg["d"].get("eventData") or {}


def save_replay(port, password, connect=None) -> dict:
    with ObsClient(port, password, connect=connect) as obs:
        if not obs.request("GetReplayBufferStatus").get("outputActive"):
            obs.request("StartReplayBuffer")
            return {"error": "OBS's Replay Buffer was off, so there was nothing to clip yet. I've turned it on "
                             "now: tell the user that from now on 'clip that' saves the last stretch of play."}
        obs.request("SaveReplayBuffer")
        try:
            path = obs.wait_event("ReplayBufferSaved").get("savedReplayPath", "")
        except ObsError:
            path = ""            # saved, but the event didn't arrive in time
    log.info("replay buffer saved")
    out = {"saved": True, "note": "Confirm in a few words, e.g. 'Clipped.'"}
    if path:
        out["file"] = os.path.basename(path)
    return out


def register(reg: Registry, cfg: dict, connect=None) -> None:
    def clip_that() -> dict:
        try:
            return save_replay(cfg.get("obs_port", 4455), get_password(), connect=connect)
        except ObsError as e:
            return {"error": str(e)}
        except Exception:
            log.exception("OBS clip failed")
            return {"error": "Saving the clip failed. Details are in the log."}

    reg.register(Tool(
        "clip_that",
        "Save a video clip of the last stretch of gameplay/screen using OBS's Replay Buffer. Use when the "
        "user says 'clip that', 'clip it', 'save that clip', 'save a replay' or similar. No arguments. Act "
        "immediately (speed matters; the moment is already in the buffer) and keep the reply very short.",
        clip_that, module="pc", visual=False))
