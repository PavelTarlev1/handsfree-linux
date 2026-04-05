"""
Audio routing manager — routes HFP call audio to/from the user's chosen headset.

Audio flow during a call (direct pacat path, no pipe modules or loopbacks):
  Phone (SCO) → SCOBridge._rx_loop → pacat --playback → output_sink (speaker)
  input_source (mic) → pacat --record → SCOBridge._tx_loop → Phone (SCO)

If no specific call device is configured, pacat uses the system default.
"""
from __future__ import annotations

import logging
import subprocess
from typing import Optional

from audio.sco_bridge import SCOBridge

logger = logging.getLogger(__name__)


class AudioManager:

    CODEC_CVSD = 1
    CODEC_MSBC = 2

    def __init__(self):
        self._active = False
        self._bridge = SCOBridge()

        # Device names (pactl short names); "" = system default
        self._call_output_sink:   str = ""
        self._call_input_source:  str = ""
        self._call_volume:        int = 80   # 0-100

        # Supplied by HandsFreeApp when a phone connects
        self._device_path:  str = ""
        self._adapter_addr: str = ""
        self._dbus_bus = None

    # ── Configuration (called by HandsFreeApp) ────────────────────────────────

    def set_call_context(self, device_path: str, adapter_addr: str, bus):
        self._device_path  = device_path
        self._adapter_addr = adapter_addr
        self._dbus_bus     = bus

    def set_call_devices(self, output_sink: str, input_source: str):
        """Set which audio devices to use during calls."""
        self._call_output_sink  = output_sink
        self._call_input_source = input_source

    def set_call_volume(self, pct: int):
        """Set call volume (0-100). Applied immediately if a call is active."""
        pct = max(0, min(100, pct))
        self._call_volume = pct
        if self._active:
            self._apply_volume()

    def adjust_call_volume(self, delta: int):
        self.set_call_volume(self._call_volume + delta)

    @property
    def call_volume(self) -> int:
        return self._call_volume

    # ── Call lifecycle ────────────────────────────────────────────────────────

    def on_call_started(self, codec: int = CODEC_CVSD):
        logger.info("SCO audio: starting (codec=%s)",
                    "mSBC" if codec == self.CODEC_MSBC else "CVSD")
        if not self._device_path:
            logger.error("No device path set — cannot start SCO bridge")
            return

        ok = self._bridge.start(
            device_path=self._device_path,
            codec=codec,
            output_sink=self._call_output_sink,
            input_source=self._call_input_source,
            bus=self._dbus_bus,
            adapter_addr=self._adapter_addr,
        )

        if ok:
            self._active = True
            self._apply_volume()
            logger.info("SCO audio running  out=%s  in=%s",
                        self._call_output_sink or "(default)",
                        self._call_input_source or "(default)")
        else:
            logger.error("SCO bridge failed to start")

    def on_call_ended(self):
        if not self._active:
            return
        logger.info("SCO audio: stopping")
        self._bridge.stop()
        self._active = False

    def mute_microphone(self, muted: bool = True):
        src = self._call_input_source or "@DEFAULT_SOURCE@"
        self._run(["pactl", "set-source-mute", src, "1" if muted else "0"])

    # ── Internal ──────────────────────────────────────────────────────────────

    def _apply_volume(self):
        sink = self._call_output_sink or "@DEFAULT_SINK@"
        self._run(["pactl", "set-sink-volume", sink, f"{self._call_volume}%"])

    # ── pactl helpers ─────────────────────────────────────────────────────────

    def _find_pa_node(self, node_type: str, keyword: str) -> Optional[str]:
        try:
            out = subprocess.check_output(
                ["pactl", "list", node_type, "short"], text=True, timeout=3,
            )
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 2 and keyword.lower() in parts[1].lower():
                    return parts[1]
        except Exception as e:
            logger.debug("pactl %s search error: %s", node_type, e)
        return None

    @staticmethod
    def _run(cmd: list[str]):
        try:
            subprocess.run(cmd, check=False, timeout=5, capture_output=True)
        except Exception as e:
            logger.debug("Audio command error %s: %s", cmd, e)
