"""
SCO audio bridge for HFP calls.

Audio path (simple, direct):

  Phone ─SCO─► recv bytes ──► pacat --playback ──► Jabra speaker
  Jabra mic ──► pacat --record ──► send bytes ──SCO─► Phone

No pipe modules, no loopbacks, no intermediate FIFOs.
PipeWire handles resampling internally via pacat.

The raw BTPROTO_SCO socket uses the kernel's CVSD codec conversion:
  write s16le PCM → kernel encodes CVSD → over-the-air
  over-the-air CVSD → kernel decodes → read s16le PCM
So user-space always works with 8 kHz signed 16-bit mono PCM.

Python 3.10's socket module is broken for BTPROTO_SCO — we use libc
ctypes for socket(), bind(), connect().
"""
from __future__ import annotations

import ctypes as _ct
import logging
import os
import socket
import stat
import struct as _struct
import subprocess
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# CVSD SCO: 8 kHz, s16le, mono
_RATE     = 8000
_FORMAT   = "s16le"
_CHANNELS = 1
# Chunk = 30 ms of audio → smoother audio with fewer write() calls
_CHUNK = int(_RATE * 2 * 0.030)   # 480 bytes = 240 samples = 30 ms


class SCOBridge:
    """
    Bridges a raw SCO socket to/from the user's headset via pacat.
    Call start(), then stop() when the call ends.
    """

    # Public names kept for compatibility with any code that checks them
    sink_name   = "hfp_spk"    # not used in this implementation
    source_name = "hfp_mic"

    def __init__(self):
        self._sco_sock: Optional[socket.socket] = None
        self._play_proc: Optional[subprocess.Popen] = None
        self._rec_proc:  Optional[subprocess.Popen] = None
        self._running = False
        self._threads: list[threading.Thread] = []

    # ── Entry point ───────────────────────────────────────────────────────────

    def start(
        self,
        device_path:   str,
        codec:         int,
        output_sink:   str = "",
        input_source:  str = "",
        bus            = None,    # unused, kept for call-site compatibility
        adapter_addr:  str = "",
    ) -> bool:
        """
        Open SCO socket and bridge audio via pacat.
        Tries MediaTransport1 first, then direct SCO connect (with retries).
        Returns True if audio is running.
        """
        self.stop()

        remote_addr = _addr_from_device_path(device_path)
        if not remote_addr:
            logger.error("Cannot derive BT address from %s", device_path)
            return False

        # Attempt 1 — BlueZ MediaTransport1
        if bus is not None:
            fd = self._acquire_media_transport(device_path, bus)
            if fd >= 0:
                return self._start_audio(fd, output_sink, input_source)

        # Attempt 2 — direct SCO socket (retry until phone opens SCO)
        fd = self._connect_sco(remote_addr)
        if fd >= 0:
            return self._start_audio(fd, output_sink, input_source)

        logger.error("SCO bridge: could not establish link to %s", remote_addr)
        return False

    def stop(self):
        self._running = False

        for proc in (self._play_proc, self._rec_proc):
            if proc:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    pass
        self._play_proc = None
        self._rec_proc  = None

        if self._sco_sock:
            try:
                self._sco_sock.close()
            except OSError:
                pass
            self._sco_sock = None

        for t in self._threads:
            t.join(timeout=2)
        self._threads.clear()

    # ── SCO acquisition ───────────────────────────────────────────────────────

    def _acquire_media_transport(self, device_path: str, bus) -> int:
        """Return an fd from org.bluez.MediaTransport1, or -1."""
        HFP_HF_UUID = "0000111e-0000-1000-8000-00805f9b34fb"
        try:
            import dbus
            manager = dbus.Interface(
                bus.get_object("org.bluez", "/"),
                "org.freedesktop.DBus.ObjectManager",
            )
            for path, ifaces in manager.GetManagedObjects().items():
                if "org.bluez.MediaTransport1" not in ifaces:
                    continue
                props = ifaces["org.bluez.MediaTransport1"]
                if (str(props.get("Device", "")) == device_path and
                        HFP_HF_UUID in str(props.get("UUID", "")).lower()):
                    transport = dbus.Interface(
                        bus.get_object("org.bluez", path),
                        "org.bluez.MediaTransport1",
                    )
                    fd_obj, _, _ = transport.Acquire()
                    raw_fd = fd_obj.take()
                    logger.info("Acquired MediaTransport1 fd=%d", raw_fd)
                    return raw_fd
        except Exception as e:
            logger.debug("MediaTransport1 not available: %s", e)
        return -1

    def _connect_sco(self, remote_addr: str, attempts: int = 8) -> int:
        """
        Try to connect a BTPROTO_SCO socket to remote_addr.
        Retries up to `attempts` times (phone may take a moment to open SCO).
        Returns the fd, or -1.
        """
        for i in range(attempts):
            fd = _sco_connect(remote_addr, timeout_sec=1.0)
            if fd >= 0:
                logger.info("SCO connected to %s (attempt %d)", remote_addr, i + 1)
                return fd
            if i < attempts - 1:
                threading.Event().wait(0.4)   # brief pause before retry
        logger.warning("SCO connect failed after %d attempts to %s", attempts, remote_addr)
        return -1

    # ── Audio forwarding ──────────────────────────────────────────────────────

    def _start_audio(self, raw_fd: int, output_sink: str, input_source: str) -> bool:
        try:
            self._sco_sock = socket.fromfd(
                raw_fd, socket.AF_BLUETOOTH,
                socket.SOCK_SEQPACKET, socket.BTPROTO_SCO,
            )
            os.close(raw_fd)
        except Exception as e:
            logger.error("SCO socket wrap failed: %s", e)
            try:
                os.close(raw_fd)
            except OSError:
                pass
            return False

        # pacat --playback: receives bytes on stdin, plays through speaker
        play_cmd = [
            "pacat", "--playback", "--raw",
            f"--format={_FORMAT}", f"--rate={_RATE}", f"--channels={_CHANNELS}",
            "--latency-msec=20",
        ]
        if output_sink:
            play_cmd.append(f"--device={output_sink}")

        # pacat --record: captures mic to stdout
        rec_cmd = [
            "pacat", "--record", "--raw",
            f"--format={_FORMAT}", f"--rate={_RATE}", f"--channels={_CHANNELS}",
            "--latency-msec=20",
        ]
        if input_source:
            rec_cmd.append(f"--device={input_source}")

        try:
            self._play_proc = subprocess.Popen(
                play_cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
            self._rec_proc = subprocess.Popen(
                rec_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            logger.error("Failed to start pacat: %s", e)
            return False

        logger.info("SCO audio: pacat started  out=%s  in=%s",
                    output_sink or "(default)", input_source or "(default)")

        self._running = True
        t_rx = threading.Thread(target=self._rx_loop, name="SCO-RX", daemon=True)
        t_tx = threading.Thread(target=self._tx_loop, name="SCO-TX", daemon=True)
        t_rx.start()
        t_tx.start()
        self._threads = [t_rx, t_tx]
        return True

    def _rx_loop(self):
        """SCO receive → pacat playback stdin (phone → speaker)."""
        try:
            while self._running and self._sco_sock and self._play_proc:
                data = self._sco_sock.recv(_CHUNK)
                if not data:
                    break
                try:
                    self._play_proc.stdin.write(data)
                    self._play_proc.stdin.flush()
                except BrokenPipeError:
                    break
        except OSError:
            pass
        logger.debug("SCO-RX loop ended")

    def _tx_loop(self):
        """pacat record stdout → SCO send (mic → phone)."""
        try:
            while self._running and self._sco_sock and self._rec_proc:
                data = self._rec_proc.stdout.read(_CHUNK)
                if not data:
                    break
                try:
                    self._sco_sock.sendall(data)
                except OSError:
                    break
        except OSError:
            pass
        logger.debug("SCO-TX loop ended")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _addr_from_device_path(device_path: str) -> str:
    try:
        dev_part = device_path.split("/")[-1]
        if dev_part.startswith("dev_"):
            return dev_part[4:].replace("_", ":")
    except Exception:
        pass
    return ""


# ── ctypes SCO socket (Python 3.10 socket module is broken for BTPROTO_SCO) ──

_AF_BLUETOOTH   = 31
_SOCK_SEQPACKET =  5
_BTPROTO_SCO    =  2
_SOL_SOCKET     =  1
_SO_SNDTIMEO    = 13


class _sockaddr_sco(_ct.Structure):
    _fields_ = [
        ("sco_family", _ct.c_uint16),
        ("sco_bdaddr", _ct.c_uint8 * 6),
    ]


def _mac_to_bdaddr(mac: str):
    octets = [int(x, 16) for x in mac.split(":")]
    octets.reverse()   # bdaddr is little-endian
    return (_ct.c_uint8 * 6)(*octets)


def _sco_connect(remote_mac: str, timeout_sec: float = 2.0) -> int:
    """
    Open and connect a BTPROTO_SCO socket via libc.
    Returns raw fd on success, negative errno on failure.
    """
    try:
        libc = _ct.CDLL("libc.so.6", use_errno=True)

        fd = libc.socket(_AF_BLUETOOTH, _SOCK_SEQPACKET, _BTPROTO_SCO)
        if fd < 0:
            return -_ct.get_errno()

        # Bind to any local adapter
        local = _sockaddr_sco(_AF_BLUETOOTH, _mac_to_bdaddr("00:00:00:00:00:00"))
        if libc.bind(fd, _ct.byref(local), _ct.sizeof(local)) < 0:
            err = _ct.get_errno()
            libc.close(fd)
            return -err

        # Set connect timeout
        tv = _struct.pack("ll", int(timeout_sec), 0)
        tv_buf = (_ct.c_char * len(tv))(*tv)
        libc.setsockopt(fd, _SOL_SOCKET, _SO_SNDTIMEO, tv_buf, len(tv))

        remote = _sockaddr_sco(_AF_BLUETOOTH, _mac_to_bdaddr(remote_mac))
        if libc.connect(fd, _ct.byref(remote), _ct.sizeof(remote)) < 0:
            err = _ct.get_errno()
            libc.close(fd)
            return -err

        return fd
    except Exception as e:
        logger.debug("_sco_connect exception: %s", e)
        return -1
