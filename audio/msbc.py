"""
mSBC codec for HFP wideband audio (16 kHz).

Each SCO packet over the air is 60 bytes:
  [0x01] [H2-SN] [57-byte SBC frame] [0x00 padding]

libsbc ships with BlueZ — available as `libsbc1` on Debian/Ubuntu/Pi OS.
If libsbc is not installed AVAILABLE is False and the app falls back to CVSD.
"""
from __future__ import annotations

import ctypes as _ct
import logging
from ctypes.util import find_library
from typing import Optional

logger = logging.getLogger(__name__)

MTU       = 60    # bytes per mSBC SCO packet
FRAME_LEN = 57    # SBC payload bytes inside the packet
SAMPLES   = 120   # PCM samples per decoded frame
PCM_BYTES = SAMPLES * 2  # 240 bytes, s16le mono
RATE      = 16000

_H2_SYNC = 0x01
_H2_SN   = (0x08, 0x38, 0xC8, 0xF8)  # H2 sequence-number nibble cycles

_SBC_BUFSIZE = 512    # safe upper bound for sizeof(sbc_t)


def _load_libsbc() -> Optional[_ct.CDLL]:
    for name in (find_library("sbc"), "libsbc.so.2", "libsbc.so.1", "libsbc.so"):
        if not name:
            continue
        try:
            lib = _ct.CDLL(name, use_errno=True)
            _ = lib.sbc_init_msbc, lib.sbc_decode, lib.sbc_encode, lib.sbc_finish
            logger.debug("Loaded libsbc: %s", name)
            return lib
        except (OSError, AttributeError):
            continue
    return None


_LIB: Optional[_ct.CDLL] = _load_libsbc()
AVAILABLE: bool = _LIB is not None


class MSBCCodec:
    """
    One mSBC encoder/decoder instance. Not thread-safe — use separate instances
    for the RX (decode) and TX (encode) paths.
    """

    def __init__(self):
        if _LIB is None:
            raise RuntimeError(
                "libsbc not found. Install it with: sudo apt-get install libsbc1"
            )
        self._lib = _LIB
        self._buf = _ct.create_string_buffer(_SBC_BUFSIZE)
        ret = self._lib.sbc_init_msbc(self._buf, 0)
        if ret < 0:
            raise RuntimeError(f"sbc_init_msbc failed: {ret}")
        self._sn = 0

    def decode(self, packet: bytes) -> Optional[bytes]:
        """60-byte mSBC SCO packet → 240 bytes s16le PCM at 16 kHz.

        Some adapters deliver H2-framed packets [0x01][SN][57-byte SBC][pad],
        others deliver a raw SBC frame with no H2 header.  We try both.
        """
        if len(packet) >= 2 + FRAME_LEN and packet[0] == _H2_SYNC:
            sbc_frame = packet[2 : 2 + FRAME_LEN]
        elif len(packet) >= FRAME_LEN:
            # No H2 header — adapter delivers raw SBC frame directly
            sbc_frame = packet[:FRAME_LEN]
        else:
            logger.debug("mSBC: short packet (%d bytes) dropped", len(packet))
            return None
        out = _ct.create_string_buffer(PCM_BYTES * 2)
        written = _ct.c_size_t(0)
        r = self._lib.sbc_decode(
            self._buf, sbc_frame, len(sbc_frame),
            out, len(out), _ct.byref(written),
        )
        if r < 0:
            logger.warning("sbc_decode error %d (frame bytes 0-3: %s)", r, sbc_frame[:4].hex())
            return None
        if written.value == 0:
            logger.warning("sbc_decode wrote 0 bytes")
            return None
        return bytes(out[: written.value])

    def encode(self, pcm: bytes) -> bytes:
        """240 bytes s16le PCM at 16 kHz → one 60-byte mSBC SCO packet."""
        if len(pcm) < PCM_BYTES:
            pcm = pcm + b"\x00" * (PCM_BYTES - len(pcm))
        out = _ct.create_string_buffer(FRAME_LEN + 8)
        written = _ct.c_ssize_t(0)
        r = self._lib.sbc_encode(
            self._buf, pcm, PCM_BYTES,
            out, len(out), _ct.byref(written),
        )
        if r < 0:
            raise RuntimeError(f"sbc_encode error {r}")
        frame = bytes(out[: written.value])
        if len(frame) < FRAME_LEN:
            frame += b"\x00" * (FRAME_LEN - len(frame))
        else:
            frame = frame[:FRAME_LEN]
        pkt = bytes([_H2_SYNC, _H2_SN[self._sn]]) + frame + b"\x00"
        self._sn = (self._sn + 1) % 4
        return pkt  # 2 + 57 + 1 = 60 bytes

    def close(self):
        try:
            self._lib.sbc_finish(self._buf)
        except Exception:
            pass
