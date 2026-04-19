"""Tests for core/config.py — known_devices persistence."""
import json
import pytest
from core.config import Config, CONFIG_DIR


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """Config instance with its sidecar file redirected to tmp_path."""
    import core.config as _cfg_mod
    monkeypatch.setattr(_cfg_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(_cfg_mod, "CONFIG_FILE", tmp_path / "config.toml")
    c = Config()
    return c, tmp_path


class TestRememberDevice:
    def test_adds_new_device(self, cfg):
        c, _ = cfg
        c.remember_device("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", "My Phone", "AA:BB:CC:DD:EE:FF")
        assert len(c.bluetooth.known_devices) == 1
        assert c.bluetooth.known_devices[0]["name"] == "My Phone"

    def test_updates_existing_device(self, cfg):
        c, _ = cfg
        path = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
        c.remember_device(path, "Old Name", "AA:BB:CC:DD:EE:FF")
        c.remember_device(path, "New Name", "AA:BB:CC:DD:EE:FF")
        assert len(c.bluetooth.known_devices) == 1
        assert c.bluetooth.known_devices[0]["name"] == "New Name"

    def test_multiple_devices(self, cfg):
        c, _ = cfg
        c.remember_device("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", "Phone A", "AA:BB:CC:DD:EE:FF")
        c.remember_device("/org/bluez/hci0/dev_11_22_33_44_55_66", "Phone B", "11:22:33:44:55:66")
        assert len(c.bluetooth.known_devices) == 2

    def test_persists_to_sidecar(self, cfg):
        c, tmp_path = cfg
        c.remember_device("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", "My Phone", "AA:BB:CC:DD:EE:FF")
        sidecar = tmp_path / "known_devices.json"
        assert sidecar.exists()
        data = json.loads(sidecar.read_text())
        assert data[0]["name"] == "My Phone"


class TestLoadKnownDevices:
    def test_loads_from_sidecar(self, cfg):
        c, tmp_path = cfg
        sidecar = tmp_path / "known_devices.json"
        sidecar.write_text(json.dumps([
            {"path": "/org/bluez/hci0/dev_AA", "name": "Saved Phone", "address": "AA:BB:CC:DD:EE:FF"}
        ]))
        c._load_known_devices()
        assert len(c.bluetooth.known_devices) == 1
        assert c.bluetooth.known_devices[0]["name"] == "Saved Phone"

    def test_missing_sidecar_is_fine(self, cfg):
        c, _ = cfg
        c._load_known_devices()   # should not raise
        assert c.bluetooth.known_devices == []

    def test_corrupt_sidecar_is_ignored(self, cfg):
        c, tmp_path = cfg
        (tmp_path / "known_devices.json").write_text("not json {{{")
        c._load_known_devices()   # should not raise
        assert c.bluetooth.known_devices == []
