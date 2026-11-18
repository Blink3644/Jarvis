"""'Clip that' against a small fake obs-websocket v5 server on localhost (real websockets library)."""
import json
import threading

import pytest
from websockets.sync.server import serve

from jarvis.core.registry import Registry
from jarvis.pc import obs_clip, screen

SALT, CHALLENGE = "c2FsdA==", "Y2hhbGxlbmdl"


class FakeObs:
    def __init__(self, password=None, buffer_on=True, send_event=True):
        self.password, self.buffer_on, self.send_event = password, buffer_on, send_event
        self.requests = []
        self.server = serve(self.handle, "127.0.0.1", 0)
        self.port = self.server.socket.getsockname()[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def handle(self, ws):
        hello = {"obsWebSocketVersion": "5.5.0", "rpcVersion": 1}
        if self.password:
            hello["authentication"] = {"salt": SALT, "challenge": CHALLENGE}
        ws.send(json.dumps({"op": 0, "d": hello}))
        ident = json.loads(ws.recv())
        assert ident["op"] == 1 and ident["d"]["eventSubscriptions"] == obs_clip.SUB_OUTPUTS
        if self.password and ident["d"].get("authentication") != obs_clip.auth_string(self.password, SALT, CHALLENGE):
            ws.close(4009, "Authentication failed.")
            return
        ws.send(json.dumps({"op": 2, "d": {"negotiatedRpcVersion": 1}}))
        for raw in ws:
            d = json.loads(raw)["d"]
            rtype = d["requestType"]
            self.requests.append(rtype)
            data = {}
            if rtype == "GetReplayBufferStatus":
                data = {"outputActive": self.buffer_on}
            elif rtype == "StartReplayBuffer":
                self.buffer_on = True
            ws.send(json.dumps({"op": 7, "d": {"requestType": rtype, "requestId": d["requestId"],
                                               "requestStatus": {"result": True, "code": 100},
                                               "responseData": data}}))
            if rtype == "SaveReplayBuffer" and self.send_event:
                ws.send(json.dumps({"op": 5, "d": {"eventType": "ReplayBufferSaved", "eventIntent": 64,
                                                   "eventData": {"savedReplayPath": "C:/Videos/Replay 1.mkv"}}}))

    def close(self):
        self.server.shutdown()


@pytest.fixture
def obs():
    servers = []

    def make(**kw):
        s = FakeObs(**kw)
        servers.append(s)
        return s
    yield make
    for s in servers:
        s.close()


def tool(cfg, password=None, monkeypatch=None):
    reg = Registry()
    obs_clip.register(reg, cfg)
    if password is not None:
        obs_clip.set_password(password)
    return reg.tools["clip_that"].handler


def test_auth_string_follows_protocol_steps():
    # protocol.md's example inputs (the doc gives no final value, so follow its 4 steps literally)
    import base64, hashlib
    pw, salt = "supersecretpassword", "lM1GncleQOaCu9lT1yeUZhFYnqhsLLP1G5lAGo3ixaI="
    challenge = "+IxH4CnCiqpX1rM9scsNynZzbOe4KhDeYcTNS3PDaeY="
    secret = base64.b64encode(hashlib.sha256(f"{pw}{salt}".encode()).digest())
    expected = base64.b64encode(hashlib.sha256(secret + challenge.encode()).digest()).decode()
    assert obs_clip.auth_string(pw, salt, challenge) == expected


def test_clip_saves_and_returns_file_name(obs):
    s = obs()
    out = tool({"obs_port": s.port})()
    assert out == {"saved": True, "note": out["note"], "file": "Replay 1.mkv"}
    assert s.requests == ["GetReplayBufferStatus", "SaveReplayBuffer"]


def test_clip_with_password(obs):
    s = obs(password="hunter2")
    out = tool({"obs_port": s.port}, password="hunter2")()
    assert out["saved"] is True


def test_wrong_password_is_a_plain_message(obs):
    s = obs(password="hunter2")
    out = tool({"obs_port": s.port}, password="nope")()
    assert "rejected the password" in out["error"]


def test_missing_password_is_a_plain_message(obs):
    s = obs(password="hunter2")
    out = tool({"obs_port": s.port})()
    assert "wants a WebSocket password" in out["error"]


def test_buffer_off_gets_started_and_says_so(obs):
    s = obs(buffer_on=False)
    out = tool({"obs_port": s.port})()
    assert "turned it on" in out["error"]
    assert s.requests == ["GetReplayBufferStatus", "StartReplayBuffer"]


def test_saved_even_if_event_never_arrives(obs, monkeypatch):
    s = obs(send_event=False)
    monkeypatch.setattr(obs_clip.ObsClient, "wait_event",
                        lambda self, etype, timeout=5.0: (_ for _ in ()).throw(obs_clip.ObsError("late")))
    out = tool({"obs_port": s.port})()
    assert out["saved"] is True and "file" not in out


def test_obs_not_running():
    s = FakeObs()
    port = s.port
    s.close()
    out = tool({"obs_port": port})()
    assert "Can't reach OBS" in out["error"]


def test_tool_is_not_visual_and_phrase_skips_auto_look():
    reg = Registry()
    obs_clip.register(reg, {})
    assert reg.tools["clip_that"].visual is False
    assert screen.is_non_visual("clip that") and screen.is_non_visual("Jarvis, save that clip")
