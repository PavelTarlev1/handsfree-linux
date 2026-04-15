"""
Configuration management — loads from ~/.config/handsfree/config.toml
Falls back to defaults if file doesn't exist.
"""
from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".config" / "handsfree"
CONFIG_FILE = CONFIG_DIR / "config.toml"
DB_FILE = CONFIG_DIR / "contacts.db"
LOG_FILE = CONFIG_DIR / "handsfree.log"


@dataclass
class BluetoothConfig:
    adapter: str = "hci0"
    auto_connect: bool = True
    preferred_codec: str = "msbc"  # "msbc" or "cvsd"


@dataclass
class AudioConfig:
    sco_routing: str = "pipewire"  # "pipewire" or "alsa_direct"
    mic_gain: int = 10
    speaker_gain: int = 10
    # pactl short node names for the headset used during calls.
    # Empty string = use system default.
    call_output_device: str = ""   # sink  — what the user hears
    call_input_device:  str = ""   # source — user's microphone
    call_volume: int = 80          # 0-100 %
    ring_volume: int = 80          # 0-100 %
    mic_sensitivity: int = 80      # 0-100 % — pactl source volume during calls


@dataclass
class PBAPConfig:
    sync_on_connect: bool = True
    sync_interval_hours: int = 24


@dataclass
class VoIPConfig:
    enabled: bool = True
    poll_interval_seconds: float = 2.0
    process_names: list[str] = field(default_factory=lambda: [
        "zoom", "ZoomPhone", "teams", "ms-teams", "slack",
        "discord", "webex", "skype", "meet",
    ])


@dataclass
class DialConfig:
    country_code: str = ""        # e.g. "359" for Bulgaria — auto-prepends to local numbers
    strip_leading_zero: bool = True   # 0899… → +359899…


@dataclass
class UIConfig:
    show_main_window_on_start: bool = False
    call_popup_timeout_seconds: int = 30
    theme: str = "VS Code Dark"


@dataclass
class Config:
    bluetooth: BluetoothConfig = field(default_factory=BluetoothConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    pbap: PBAPConfig = field(default_factory=PBAPConfig)
    voip: VoIPConfig = field(default_factory=VoIPConfig)
    dial: DialConfig = field(default_factory=DialConfig)
    ui: UIConfig = field(default_factory=UIConfig)

    @classmethod
    def load(cls) -> "Config":
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        cfg = cls()
        if CONFIG_FILE.exists():
            try:
                cfg._load_toml()
            except Exception as e:
                logger.warning("Failed to load config: %s — using defaults", e)
        else:
            cfg._write_defaults()
        return cfg

    def _load_toml(self):
        try:
            import tomllib  # Python 3.11+
        except ImportError:
            try:
                import tomli as tomllib  # pip install tomli
            except ImportError:
                logger.warning("No TOML library available; using defaults.")
                return

        with open(CONFIG_FILE, "rb") as f:
            data = tomllib.load(f)

        bt = data.get("bluetooth", {})
        self.bluetooth.adapter = bt.get("adapter", self.bluetooth.adapter)
        self.bluetooth.auto_connect = bt.get("auto_connect", self.bluetooth.auto_connect)
        self.bluetooth.preferred_codec = bt.get("preferred_codec", self.bluetooth.preferred_codec)

        au = data.get("audio", {})
        self.audio.sco_routing = au.get("sco_routing", self.audio.sco_routing)
        self.audio.mic_gain = au.get("mic_gain", self.audio.mic_gain)
        self.audio.speaker_gain = au.get("speaker_gain", self.audio.speaker_gain)
        self.audio.call_output_device = au.get("call_output_device", self.audio.call_output_device)
        self.audio.call_input_device  = au.get("call_input_device",  self.audio.call_input_device)
        self.audio.call_volume        = au.get("call_volume",        self.audio.call_volume)
        self.audio.ring_volume        = au.get("ring_volume",        self.audio.ring_volume)
        self.audio.mic_sensitivity    = au.get("mic_sensitivity",    self.audio.mic_sensitivity)

        pb = data.get("pbap", {})
        self.pbap.sync_on_connect = pb.get("sync_on_connect", self.pbap.sync_on_connect)
        self.pbap.sync_interval_hours = pb.get("sync_interval_hours", self.pbap.sync_interval_hours)

        vo = data.get("voip", {})
        self.voip.enabled = vo.get("enabled", self.voip.enabled)
        self.voip.poll_interval_seconds = vo.get("poll_interval_seconds", self.voip.poll_interval_seconds)
        self.voip.process_names = vo.get("process_names", self.voip.process_names)

        di = data.get("dial", {})
        self.dial.country_code = di.get("country_code", self.dial.country_code)
        self.dial.strip_leading_zero = di.get("strip_leading_zero", self.dial.strip_leading_zero)

        ui = data.get("ui", {})
        self.ui.show_main_window_on_start = ui.get("show_main_window_on_start", self.ui.show_main_window_on_start)
        self.ui.call_popup_timeout_seconds = ui.get("call_popup_timeout_seconds", self.ui.call_popup_timeout_seconds)
        self.ui.theme = ui.get("theme", self.ui.theme)

    def _write_defaults(self):
        """Write a commented default config file for the user to edit."""
        CONFIG_FILE.write_text("""\
[bluetooth]
adapter = "hci0"
auto_connect = true
preferred_codec = "msbc"   # "msbc" (wideband) or "cvsd"

[audio]
sco_routing = "pipewire"   # "pipewire" or "alsa_direct"
mic_gain = 10              # 0-15
speaker_gain = 10          # 0-15
# pactl short node name for the headset to use during calls.
# Leave empty to use the system default.
call_output_device = ""    # sink  — what you hear during a call
call_input_device  = ""    # source — your microphone during a call
call_volume = 80           # 0-100 %
ring_volume = 80           # 0-100 %
mic_sensitivity = 80       # 0-100 % (150 = +50 % boost)

[pbap]
sync_on_connect = true
sync_interval_hours = 24

[voip]
enabled = true
poll_interval_seconds = 2
process_names = ["zoom", "teams", "webex", "slack", "discord", "skype"]

[dial]
# Your country code WITHOUT leading +  (e.g. "359" for Bulgaria, "44" for UK, "1" for US)
# Leave empty to dial numbers exactly as typed.
country_code = "359"
# If true, a leading 0 is stripped before adding country code (0899… → +359899…)
strip_leading_zero = true

[ui]
show_main_window_on_start = false
call_popup_timeout_seconds = 30
""")
        logger.info("Default config written to %s", CONFIG_FILE)