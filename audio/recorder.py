"""
Call recorder — mixes SCO RX (phone→speaker) and TX (mic→phone) into a stereo WAV.

Left channel  = incoming (phone voice)
Right channel = outgoing (your mic)

Written in real time as the call progresses; finalized on stop().
"""
from __future__ import annotations

import logging
import struct
import threading
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# SCO CVSD audio parameters
_RATE     = 8000
_SAMPWIDTH = 2   # s16le
_CHANNELS  = 2   # stereo: L=rx, R=tx


class CallRecorder:
    """
    Usage:
        rec = CallRecorder(directory)
        rec.start(contact_name, number)
        # Feed audio as it arrives:
        rec.write_rx(bytes)   # incoming (phone → speaker)
        rec.write_tx(bytes)   # outgoing (mic → phone)
        rec.stop()
    """

    def __init__(self, directory: str = ""):
        self._dir = Path(directory).expanduser() if directory else \
                    Path.home() / "recordings" / "handsfree"
        self._wav: Optional[wave.Wave_write] = None
        self._lock = threading.Lock()
        self._rx_buf = bytearray()
        self._tx_buf = bytearray()
        self._running = False

    def start(self, contact_name: str = "", number: str = ""):
        self.stop()
        self._dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = _safe(contact_name or number or "unknown")
        filename = self._dir / f"{ts}_{safe_name}.wav"

        try:
            self._wav = wave.open(str(filename), "wb")
            self._wav.setnchannels(_CHANNELS)
            self._wav.setsampwidth(_SAMPWIDTH)
            self._wav.setframerate(_RATE)
            self._running = True
            logger.info("Recording started: %s", filename)
        except Exception as e:
            logger.error("Failed to open recording file %s: %s", filename, e)
            self._wav = None

    def write_rx(self, data: bytes):
        """Incoming audio (phone voice → left channel)."""
        if not self._running:
            return
        with self._lock:
            self._rx_buf += data
            self._flush()

    def write_tx(self, data: bytes):
        """Outgoing audio (your mic → right channel)."""
        if not self._running:
            return
        with self._lock:
            self._tx_buf += data
            self._flush()

    def stop(self):
        if not self._running:
            return
        self._running = False
        with self._lock:
            # Flush remaining bytes (pad shorter buffer with silence)
            self._flush(final=True)
            if self._wav:
                try:
                    self._wav.close()
                except Exception:
                    pass
                self._wav = None
        logger.info("Recording stopped")

    def _flush(self, final: bool = False):
        """Interleave rx/tx mono s16le frames into stereo and write to WAV."""
        if not self._wav:
            return

        # Work in 2-byte (one sample) chunks
        frame_size = _SAMPWIDTH   # bytes per mono sample

        if final:
            # Pad the shorter buffer to match
            max_len = max(len(self._rx_buf), len(self._tx_buf))
            # Round up to even
            max_len = (max_len + 1) & ~1
            while len(self._rx_buf) < max_len:
                self._rx_buf += b"\x00\x00"
            while len(self._tx_buf) < max_len:
                self._tx_buf += b"\x00\x00"

        frames = min(len(self._rx_buf), len(self._tx_buf)) // frame_size
        if frames == 0:
            return

        rx_chunk = bytes(self._rx_buf[:frames * frame_size])
        tx_chunk = bytes(self._tx_buf[:frames * frame_size])
        self._rx_buf = self._rx_buf[frames * frame_size:]
        self._tx_buf = self._tx_buf[frames * frame_size:]

        # Interleave: L(rx), R(tx), L(rx), R(tx), ...
        stereo = bytearray(frames * 4)
        for i in range(frames):
            stereo[i * 4 + 0] = rx_chunk[i * 2 + 0]
            stereo[i * 4 + 1] = rx_chunk[i * 2 + 1]
            stereo[i * 4 + 2] = tx_chunk[i * 2 + 0]
            stereo[i * 4 + 3] = tx_chunk[i * 2 + 1]

        try:
            self._wav.writeframes(bytes(stereo))
        except Exception as e:
            logger.error("WAV write error: %s", e)


def _safe(name: str) -> str:
    """Strip characters unsafe for filenames."""
    return "".join(c if c.isalnum() or c in " _-+" else "_" for c in name)[:40].strip()
