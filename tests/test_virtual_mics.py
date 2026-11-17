"""Virtual inputs (VB-Cable etc.) are hidden unless the user unlocks one by name."""
from jarvis.core.ui.settings_window import build_cfg
from jarvis.core.voice import devices

HOSTAPIS = [{"name": "MME"}, {"name": "Windows WASAPI", "default_input_device": 1, "default_output_device": 9}]
DEVS = [
    {"index": 1, "name": "Headset Microphone", "hostapi": 1, "max_input_channels": 1, "max_output_channels": 0},
    {"index": 2, "name": "CABLE Output (VB-Audio Virtual Cable)", "hostapi": 1,
     "max_input_channels": 2, "max_output_channels": 0},
    {"index": 3, "name": "Stereo Mix (Realtek)", "hostapi": 1, "max_input_channels": 2, "max_output_channels": 0},
]
CABLE = DEVS[1]["name"]


def fake(monkeypatch):
    monkeypatch.setattr(devices.sd, "query_hostapis", lambda: HOSTAPIS)
    monkeypatch.setattr(devices.sd, "query_devices", lambda: DEVS)


def test_cable_hidden_by_default(monkeypatch):
    fake(monkeypatch)
    assert [d["name"] for d in devices.list_inputs()] == ["Headset Microphone"]
    assert devices.resolve("input", CABLE) is None


def test_virtual_list(monkeypatch):
    fake(monkeypatch)
    assert [d["name"] for d in devices.list_virtual_inputs()] == [CABLE, "Stereo Mix (Realtek)"]


def test_unlocked_cable_usable(monkeypatch):
    fake(monkeypatch)
    names = [d["name"] for d in devices.list_inputs([CABLE])]
    assert names == ["Headset Microphone", CABLE]
    assert devices.resolve("input", CABLE, [CABLE]) == 2
    assert devices.resolve("input", "Stereo Mix (Realtek)", [CABLE]) is None


def test_settings_unlocks_picked_virtual_mic():
    base = {"mic_device": "", "allowed_virtual_mics": []}
    cfg, errors = build_cfg(base, {"mic_device": CABLE, "virtual_inputs": [CABLE]})
    assert not errors and cfg["allowed_virtual_mics"] == [CABLE]
    cfg, _ = build_cfg(base, {"mic_device": "Headset Microphone", "virtual_inputs": [CABLE]})
    assert cfg["allowed_virtual_mics"] == []
