"""
SCO audio bridge for HFP calls.

Audio path (direct, no pipe modules or loopbacks):

  Phone ─SCO─► recv bytes ──► pacat --playback ──► speaker
  mic ──► pacat --record ──► send bytes ──SCO─► Phone

CVSD (codec=1): kernel handles encode/decode transparently.
  User-space sees raw s16le PCM at 8 kHz, 48-byte SCO packets.

mSBC (codec=2): user-space encode/decode via libsbc.
  s16le PCM at 16 kHz, 60-byte packets (H2 header + 57-byte SBC frame).
  Much clearer audio than CVSD. Requires libsbc1 to be installed.

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

# CVSD: 8 kHz, s16le, mono, 48-byte packets
_CVSD_RATE    = 8000
_CVSD_MTU     = 48
_CVSD_CHUNK   = int(_CVSD_RATE * 2 * 0.030)  # 480 bytes = 30 ms

_FORMAT   = "s16le"
_CHANNELS = 1


def _pacat_supports_latency_flag() -> bool:
    """Return True if this pacat supports --latency-msec (not all Pi builds do)."""
    try:
        result = subprocess.run(
            ["pacat", "--help"], capture_output=True, text=True, timeout=3,
        )
        return "--latency-msec" in (result.stdout + result.stderr)
    except Exception:
        return False


_PACAT_LATENCY_FLAG: Optional[str] = None  # lazy-initialised


def _pacat_latency_arg() -> Optional[str]:
    global _PACAT_LATENCY_FLAG
    if _PACAT_LATENCY_FLAG is None:
        _PACAT_LATENCY_FLAG = "--latency-msec=40" if _pacat_supports_latency_flag() else ""
    return _PACAT_LATENCY_FLAG or None


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

        from bluetooth.at_handler import CODEC_MSBC
        want_msbc = (codec == CODEC_MSBC)

        # Attempt 1 — BlueZ MediaTransport1 (codec handled by BlueZ)
        if bus is not None:
            fd = self._acquire_media_transport(device_path, bus)
            if fd >= 0:
                return self._start_audio(fd, output_sink, input_source, codec)

        # Attempt 2 — direct SCO socket.
        # For mSBC: request transparent mode so the kernel doesn't touch the data.
        # If the adapter doesn't support transparent mode, fall back to CVSD.
        if want_msbc:
            fd = self._connect_sco(
                remote_addr, voice_setting=_BT_VOICE_TRANSPARENT,
            )
            if fd >= 0:
                return self._start_audio(fd, output_sink, input_source, codec)
            logger.warning(
                "Adapter does not support transparent SCO — falling back to CVSD. "
                "mSBC wideband audio is not available on this device."
            )
            codec = 1  # fall back to CVSD

        fd = self._connect_sco(remote_addr, voice_setting=_BT_VOICE_CVSD_16BIT)
        if fd >= 0:
            return self._start_audio(fd, output_sink, input_source, codec)

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
                    try:
                        proc.kill()
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

    def _connect_sco(
        self,
        remote_addr: str,
        attempts: int = 8,
        voice_setting: Optional[int] = None,
    ) -> int:
        """
        Try to connect a BTPROTO_SCO socket to remote_addr.
        Retries up to `attempts` times (phone may take a moment to open SCO).
        Returns the fd, or -1.
        """
        if voice_setting is None:
            voice_setting = _BT_VOICE_CVSD_16BIT
        for i in range(attempts):
            fd = _sco_connect(remote_addr, timeout_sec=1.0, voice_setting=voice_setting)
            if fd >= 0:
                logger.info("SCO connected to %s (attempt %d)", remote_addr, i + 1)
                return fd
            if i < attempts - 1:
                threading.Event().wait(0.4)
        logger.warning("SCO connect failed after %d attempts to %s", attempts, remote_addr)
        return -1

    # ── Audio forwarding ──────────────────────────────────────────────────────

    def _start_audio(
        self, raw_fd: int, output_sink: str, input_source: str, codec: int,
    ) -> bool:
        from audio import msbc as _msbc
        from bluetooth.at_handler import CODEC_MSBC
        use_msbc = (codec == CODEC_MSBC)

        if use_msbc and not _msbc.AVAILABLE:
            logger.warning(
                "mSBC negotiated but libsbc not installed — falling back to CVSD audio. "
                "Install with: sudo apt-get install libsbc1"
            )
            use_msbc = False

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

        rate = _msbc.RATE if use_msbc else _CVSD_RATE
        latency_arg = _pacat_latency_arg()

        play_cmd = [
            "pacat", "--playback", "--raw",
            f"--format={_FORMAT}", f"--rate={rate}", f"--channels={_CHANNELS}",
        ]
        if latency_arg:
            play_cmd.append(latency_arg)
        if output_sink:
            play_cmd.append(f"--device={output_sink}")

        rec_cmd = [
            "pacat", "--record", "--raw",
            f"--format={_FORMAT}", f"--rate={rate}", f"--channels={_CHANNELS}",
        ]
        if latency_arg:
            rec_cmd.append(latency_arg)
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

        codec_name = "mSBC 16kHz" if use_msbc else "CVSD 8kHz"
        logger.info("SCO audio: %s  out=%s  in=%s",
                    codec_name, output_sink or "(default)", input_source or "(default)")

        self._running = True
        rx_codec = tx_codec = None
        if use_msbc:
            try:
                rx_codec = _msbc.MSBCCodec()
                tx_codec = _msbc.MSBCCodec()
            except Exception as e:
                logger.error("mSBC codec init failed (%s) — falling back to CVSD", e)
                if rx_codec:
                    rx_codec.close()
                rx_codec = tx_codec = None
                use_msbc = False

        if use_msbc:
            t_rx = threading.Thread(
                target=self._rx_loop_msbc, args=(rx_codec,), name="SCO-RX", daemon=True,
            )
            t_tx = threading.Thread(
                target=self._tx_loop_msbc, args=(tx_codec,), name="SCO-TX", daemon=True,
            )
        else:
            t_rx = threading.Thread(target=self._rx_loop_cvsd, name="SCO-RX", daemon=True)
            t_tx = threading.Thread(target=self._tx_loop_cvsd, name="SCO-TX", daemon=True)

        t_rx.start()
        t_tx.start()
        self._threads = [t_rx, t_tx]
        return True

    # ── CVSD loops (8 kHz, kernel handles codec) ──────────────────────────────

    def _rx_loop_cvsd(self):
        """SCO receive → pacat playback stdin (phone → speaker, CVSD)."""
        sock = self._sco_sock
        proc = self._play_proc
        buf = bytearray()
        try:
            while self._running and sock and proc:
                data = sock.recv(_CVSD_MTU)
                if not data:
                    break
                buf += data
                if len(buf) >= _CVSD_CHUNK:
                    try:
                        proc.stdin.write(bytes(buf))
                        proc.stdin.flush()
                    except BrokenPipeError:
                        break
                    buf.clear()
        except OSError:
            pass
        logger.debug("SCO-RX-CVSD loop ended")

    def _tx_loop_cvsd(self):
        """pacat record stdout → SCO send (mic → phone, CVSD)."""
        sock = self._sco_sock
        proc = self._rec_proc
        try:
            while self._running and sock and proc:
                data = proc.stdout.read(_CVSD_CHUNK)
                if not data:
                    break
                try:
                    for i in range(0, len(data), _CVSD_MTU):
                        sock.send(data[i : i + _CVSD_MTU])
                except OSError:
                    break
        except OSError:
            pass
        logger.debug("SCO-TX-CVSD loop ended")

    # ── mSBC loops (16 kHz, user-space SBC encode/decode) ────────────────────

    def _rx_loop_msbc(self, codec):
        """SCO receive → decode SBC → pacat playback stdin (phone → speaker, mSBC)."""
        from audio.msbc import MTU as MSBC_MTU  # already cached in sys.modules
        sock = self._sco_sock
        proc = self._play_proc
        try:
            while self._running and sock and proc:
                packet = sock.recv(MSBC_MTU)
                if not packet:
                    break
                pcm = codec.decode(packet)
                if pcm:
                    try:
                        proc.stdin.write(pcm)
                        proc.stdin.flush()
                    except BrokenPipeError:
                        break
        except OSError:
            pass
        finally:
            codec.close()
        logger.debug("SCO-RX-mSBC loop ended")

    def _tx_loop_msbc(self, codec):
        """pacat record stdout → encode SBC → SCO send (mic → phone, mSBC)."""
        from audio.msbc import PCM_BYTES  # already cached in sys.modules
        sock = self._sco_sock
        proc = self._rec_proc
        try:
            while self._running and sock and proc:
                # Read exactly one frame worth of PCM (240 bytes = 120 samples)
                pcm = b""
                while len(pcm) < PCM_BYTES:
                    chunk = proc.stdout.read(PCM_BYTES - len(pcm))
                    if not chunk:
                        return
                    pcm += chunk
                try:
                    sock.send(codec.encode(pcm))
                except OSError:
                    break
        except OSError:
            pass
        finally:
            codec.close()
        logger.debug("SCO-TX-mSBC loop ended")


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
_SOL_BLUETOOTH  = 274
_BT_VOICE       = 11
# Voice settings: kernel passes raw bytes (mSBC) vs. converts CVSD↔PCM
_BT_VOICE_TRANSPARENT = 0x0003
_BT_VOICE_CVSD_16BIT  = 0x0060


class _sockaddr_sco(_ct.Structure):
    _fields_ = [
        ("sco_family", _ct.c_uint16),
        ("sco_bdaddr", _ct.c_uint8 * 6),
    ]


def _mac_to_bdaddr(mac: str):
    octets = [int(x, 16) for x in mac.split(":")]
    octets.reverse()   # bdaddr is little-endian
    return (_ct.c_uint8 * 6)(*octets)


def _load_libc():
    """Load libc via ctypes, works on x86-64, ARM (Pi), aarch64, etc."""
    from ctypes.util import find_library
    # find_library("c") returns "libc.so.6" on glibc, "libc.musl-*.so.1" on musl (Alpine)
    name = find_library("c") or "libc.so.6"
    return _ct.CDLL(name, use_errno=True)


def _sco_connect(
    remote_mac: str,
    timeout_sec: float = 2.0,
    voice_setting: int = _BT_VOICE_CVSD_16BIT,
) -> int:
    """
    Open and connect a BTPROTO_SCO socket via libc.
    Returns raw fd on success, -1 on failure.

    voice_setting controls what the kernel does with the SCO data:
      _BT_VOICE_CVSD_16BIT  (0x0060) — kernel converts CVSD↔16-bit PCM (CVSD calls)
      _BT_VOICE_TRANSPARENT (0x0003) — kernel passes raw bytes through (mSBC calls)

    Not all adapters support _BT_VOICE_TRANSPARENT. If setsockopt fails, the
    caller should retry with _BT_VOICE_CVSD_16BIT and use CVSD audio instead.
    """
    try:
        libc = _load_libc()

        fd = libc.socket(_AF_BLUETOOTH, _SOCK_SEQPACKET, _BTPROTO_SCO)
        if fd < 0:
            return -1

        # Set voice setting before bind/connect — determines kernel codec behaviour.
        vs = _struct.pack("@H", voice_setting)  # uint16_t, native byte order
        vs_buf = (_ct.c_char * len(vs))(*vs)
        if libc.setsockopt(fd, _SOL_BLUETOOTH, _BT_VOICE, vs_buf, len(vs)) < 0:
            err = _ct.get_errno()
            libc.close(fd)
            logger.debug(
                "BT_VOICE setsockopt failed (voice=0x%04x, errno=%d) — "
                "adapter may not support this mode", voice_setting, err,
            )
            return -1

        # Bind to any local adapter
        local = _sockaddr_sco(_AF_BLUETOOTH, _mac_to_bdaddr("00:00:00:00:00:00"))
        if libc.bind(fd, _ct.byref(local), _ct.sizeof(local)) < 0:
            libc.close(fd)
            return -1

        # Set connect timeout.
        # Use "@" (native byte order, native size) so timeval is the right
        # size on both 64-bit (2×8 bytes) and 32-bit ARM/Pi (2×4 bytes).
        tv = _struct.pack("@ll", int(timeout_sec), 0)
        tv_buf = (_ct.c_char * len(tv))(*tv)
        libc.setsockopt(fd, _SOL_SOCKET, _SO_SNDTIMEO, tv_buf, len(tv))

        remote = _sockaddr_sco(_AF_BLUETOOTH, _mac_to_bdaddr(remote_mac))
        if libc.connect(fd, _ct.byref(remote), _ct.sizeof(remote)) < 0:
            libc.close(fd)
            return -1

        return fd
    except Exception as e:
        logger.debug("_sco_connect exception: %s", e)
        return -1
