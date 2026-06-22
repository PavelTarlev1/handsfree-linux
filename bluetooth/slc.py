"""
HFP Service Level Connection (SLC) state machine.

Runs in a dedicated thread per connected phone.
Drives the mandatory AT handshake, then handles ongoing call events.
Emits callbacks (thread-safe via Qt signals) for UI updates.
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from enum import Enum, auto
from typing import Callable, Optional

from bluetooth import at_handler as AT

logger = logging.getLogger(__name__)


class SLCState(Enum):
    IDLE = auto()
    BRSF_SENT = auto()
    BAC_SENT = auto()
    CIND_TEST_SENT = auto()
    CIND_READ_SENT = auto()
    CMER_SENT = auto()
    CHLD_TEST_SENT = auto()
    CLIP_SENT = auto()
    ESTABLISHED = auto()
    CALL_RINGING = auto()
    CALL_ACTIVE = auto()
    CALL_ON_HOLD = auto()
    DISCONNECTED = auto()


class CallState(Enum):
    IDLE = auto()
    INCOMING = auto()
    OUTGOING = auto()
    ACTIVE = auto()
    ON_HOLD = auto()


class SLCConnection:
    """
    Manages the RFCOMM socket and AT command exchange for one paired phone.

    Callbacks (called from the SLC thread — UI must use Qt queued connections):
        on_established(device_path)
        on_disconnected(device_path)
        on_ring(number: str)          # incoming call ringing
        on_call_answered()
        on_call_ended()
        on_call_active()
        on_codec_negotiated(codec: int)
        on_battery(level: int)   # 0-5, maps to 0-100%
    """

    READ_TIMEOUT = 5.0   # seconds; allows clean shutdown via self._stop

    def __init__(
        self,
        sock: socket.socket,
        device_path: str,
        preferred_codec: str = "msbc",
        on_established: Optional[Callable] = None,
        on_disconnected: Optional[Callable] = None,
        on_ring: Optional[Callable] = None,
        on_call_answered: Optional[Callable] = None,
        on_call_ended: Optional[Callable] = None,
        on_call_active: Optional[Callable] = None,
        on_codec_negotiated: Optional[Callable] = None,
        on_dial_error: Optional[Callable] = None,
    ):
        self._sock = sock
        self._sock.settimeout(self.READ_TIMEOUT)
        self._device_path = device_path
        self._preferred_codec = preferred_codec
        self._state = SLCState.IDLE
        self._call_state = CallState.IDLE
        self._stop = threading.Event()
        self._send_lock = threading.Lock()

        # Callbacks
        self._on_established = on_established
        self._on_disconnected = on_disconnected
        self._on_ring = on_ring
        self._on_call_answered = on_call_answered
        self._on_call_ended = on_call_ended
        self._on_call_active = on_call_active
        self._on_codec_negotiated = on_codec_negotiated
        self._on_dial_error = on_dial_error
        self._dialing = False   # True while waiting for ATD response

        # Indicator mapping: index → name (populated from AT+CIND=? response)
        self._indicator_map: dict[int, str] = {}
        self._caller_number: str = ""
        self._active_call_id: Optional[int] = None
        self._call_log_start: Optional[float] = None
        self._last_activity: float = time.monotonic()

        # Keepalive: iPhone drops HFP after ~50s of silence.
        # Send AT+CIND? every 10 seconds when idle to keep the link alive.
        self._KEEPALIVE_INTERVAL = 10.0
        self._keepalive_timer: Optional[threading.Timer] = None

        self._thread = threading.Thread(
            target=self._run, name=f"SLC-{device_path}", daemon=True
        )

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._cancel_keepalive()

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    # ── Public command API (safe to call from any thread) ─────────────────────

    def answer(self):
        self._send(AT.cmd_answer())

    def reject(self):
        self._send(AT.cmd_reject())

    def hangup(self):
        self._send(AT.cmd_hangup())

    def dial(self, number: str):
        self._dialing = True
        self._send(AT.cmd_dial(number))

    def dtmf(self, digit: str):
        self._send(AT.cmd_dtmf(digit))

    def set_speaker_volume(self, gain: int):
        self._send(AT.cmd_volume_speaker(gain))

    def set_mic_volume(self, gain: int):
        self._send(AT.cmd_volume_mic(gain))

    def redial(self):
        self._send(AT.cmd_redial())

    # ── Internal ──────────────────────────────────────────────────────────────

    def _send(self, cmd: str):
        data = (cmd + "\r").encode("ascii")
        with self._send_lock:
            try:
                self._sock.sendall(data)
                logger.debug("→ %s", cmd)
                self._last_activity = time.monotonic()
            except OSError as e:
                logger.error("Send error: %s", e)
                self._state = SLCState.DISCONNECTED
                self._stop.set()

    def _schedule_keepalive(self):
        self._cancel_keepalive()
        if not self._stop.is_set():
            self._keepalive_timer = threading.Timer(
                self._KEEPALIVE_INTERVAL, self._send_keepalive
            )
            self._keepalive_timer.daemon = True
            self._keepalive_timer.start()

    def _cancel_keepalive(self):
        if self._keepalive_timer:
            self._keepalive_timer.cancel()
            self._keepalive_timer = None

    def _send_keepalive(self):
        if self._stop.is_set() or self._state == SLCState.DISCONNECTED:
            return
        idle_for = time.monotonic() - self._last_activity
        if idle_for >= self._KEEPALIVE_INTERVAL - 1:
            # AT+CIND? is universally supported (iPhone included) and lightweight.
            # AT+CLCC is only valid during a call on many phones.
            logger.debug("Keepalive → AT+CIND? (idle %.0fs)", idle_for)
            self._send(AT.cmd_cind_read())
        self._schedule_keepalive()

    def _run(self):
        """Main loop: send SLC handshake, then read AT events."""
        logger.info("SLC thread starting for %s", self._device_path)
        try:
            self._do_slc_handshake()
            self._schedule_keepalive()
            while not self._stop.is_set() and self._state != SLCState.DISCONNECTED:
                line = self._read_line()
                if line is None:
                    continue
                self._last_activity = time.monotonic()
                self._dispatch(line)
        except Exception as e:
            logger.exception("SLC thread error: %s", e)
        finally:
            self._cancel_keepalive()
            self._state = SLCState.DISCONNECTED
            try:
                self._sock.close()
            except OSError:
                pass
            logger.info("SLC disconnected from %s", self._device_path)
            self._call_state_cleanup_on_disconnect()

    def _call_state_cleanup_on_disconnect(self):
        if self._call_state != CallState.IDLE:
            self._call_state = CallState.IDLE
            if self._on_call_ended:
                self._on_call_ended()
        if self._on_disconnected:
            self._on_disconnected(self._device_path)

    def _do_slc_handshake(self):
        """
        Full SLC establishment sequence per HFP 1.8 spec §4.2.
        We (HF) initiate by sending AT+BRSF immediately after connection.
        """
        # Step 1: Exchange supported features
        self._send(AT.cmd_brsf())
        self._state = SLCState.BRSF_SENT

        while not self._stop.is_set() and self._state != SLCState.ESTABLISHED:
            line = self._read_line()
            if line is None:
                continue
            event = AT.parse_line(line)

            if self._state == SLCState.BRSF_SENT:
                if event.kind == "brsf":
                    logger.debug("AG features: 0x%x", event.params.get("features", 0))
                elif event.kind == "ok":
                    # After BRSF OK, send BAC (codec negotiation)
                    self._send(AT.cmd_bac(self._preferred_codec))
                    self._state = SLCState.BAC_SENT

            elif self._state == SLCState.BAC_SENT:
                if event.kind == "ok":
                    self._send(AT.cmd_cind_test())
                    self._state = SLCState.CIND_TEST_SENT
                elif event.kind == "error":
                    # Some phones don't support BAC — skip codec negotiation
                    self._send(AT.cmd_cind_test())
                    self._state = SLCState.CIND_TEST_SENT

            elif self._state == SLCState.CIND_TEST_SENT:
                if event.kind == "cind_format":
                    self._parse_cind_format(line)
                elif event.kind == "ok":
                    self._send(AT.cmd_cind_read())
                    self._state = SLCState.CIND_READ_SENT

            elif self._state == SLCState.CIND_READ_SENT:
                if event.kind == "cind_values":
                    self._apply_cind_values(event.params.get("values", []))
                elif event.kind == "ok":
                    self._send(AT.cmd_cmer())
                    self._state = SLCState.CMER_SENT

            elif self._state == SLCState.CMER_SENT:
                if event.kind == "ok":
                    # Query call hold modes — required by HFP spec if AG supports 3-way calling.
                    # iPhone needs this before it will accept ATD commands.
                    self._send(AT.cmd_chld_test())
                    self._state = SLCState.CHLD_TEST_SENT

            elif self._state == SLCState.CHLD_TEST_SENT:
                if event.kind in ("ok", "error", "chld"):
                    # chld response arrives before OK — collect both then move on
                    if event.kind in ("ok", "error"):
                        self._send(AT.cmd_clip_enable())
                        self._state = SLCState.CLIP_SENT

            elif self._state == SLCState.CLIP_SENT:
                if event.kind in ("ok", "error"):
                    if event.kind == "error":
                        logger.debug("AT+CLIP=1 returned ERROR (normal on iPhone)")
                    self._send(AT.cmd_ccwa_enable())
                    self._state = SLCState.ESTABLISHED
                    logger.info("SLC established with %s", self._device_path)
                    if self._on_established:
                        self._on_established(self._device_path)
                    # If the phone was already in a call when we connected,
                    # fire the call-active callback so the UI shows the in-call screen.
                    if self._call_state == CallState.ACTIVE and self._on_call_active:
                        self._call_log_start = time.monotonic()
                        logger.info("Call already active at SLC establishment — transferring to computer")
                        self._on_call_active()
                    elif self._call_state == CallState.INCOMING and self._on_ring:
                        logger.info("Incoming call already ringing at SLC establishment")
                        self._on_ring(self._caller_number)

    def _dispatch(self, line: str):
        """Handle events after SLC is established."""
        event = AT.parse_line(line)

        if event.kind == "ring":
            self._call_state = CallState.INCOMING
            self._call_log_start = time.monotonic()
            logger.info("RING from %s", self._caller_number or "unknown")
            if self._on_ring:
                self._on_ring(self._caller_number)

        elif event.kind == "clip":
            self._caller_number = event.params.get("number", "")
            logger.info("CLIP: %s", self._caller_number)
            # If ring already received, re-emit with the number now known
            if self._call_state == CallState.INCOMING and self._on_ring:
                self._on_ring(self._caller_number)

        elif event.kind == "ciev":
            self._handle_ciev(event.params)

        elif event.kind == "bcs":
            codec = event.params.get("codec", AT.CODEC_CVSD)
            logger.info("Codec selected: %s", "mSBC" if codec == AT.CODEC_MSBC else "CVSD")
            # Confirm codec selection
            self._send(AT.cmd_bcs_confirm(codec))
            if self._on_codec_negotiated:
                self._on_codec_negotiated(codec)

        elif event.kind == "ok" and self._dialing:
            self._dialing = False  # ATD accepted

        elif event.kind == "error" and self._dialing:
            self._dialing = False
            logger.warning("Dial rejected by phone (ERROR) — unlock iPhone screen if locked")
            if self._on_dial_error:
                self._on_dial_error()

        elif event.kind == "no_carrier":
            self._call_state = CallState.IDLE
            self._caller_number = ""
            if self._on_call_ended:
                self._on_call_ended()

        elif event.kind in ("vgs", "vgm"):
            logger.debug("Volume update: %s", event)

        elif event.kind == "ccwa":
            logger.info("Call waiting: %s", event.params.get("number", ""))

        elif event.kind == "unknown":
            logger.debug("Unhandled: %r", event.raw)

    def _handle_ciev(self, params: dict):
        """
        +CIEV: index,value — indicator change.
        Key indicators:
          call: 0=no call, 1=call active
          callsetup: 0=idle, 1=incoming, 2=outgoing-dial, 3=outgoing-alerting
          callheld: 0=none, 1=held+active, 2=held-only
        """
        index = params.get("index", 0)
        value = params.get("value", 0)
        name = self._indicator_map.get(index, f"ind{index}")
        logger.debug("CIEV: %s=%d", name, value)

        if name == "call":
            if value == 1 and self._call_state != CallState.ACTIVE:
                self._call_state = CallState.ACTIVE
                self._call_log_start = time.monotonic()
                if self._on_call_active:
                    self._on_call_active()
            elif value == 0 and self._call_state in (CallState.ACTIVE, CallState.INCOMING, CallState.OUTGOING):
                self._call_state = CallState.IDLE
                self._caller_number = ""
                if self._on_call_ended:
                    self._on_call_ended()

        elif name == "callsetup":
            if value in (2, 3):
                # outgoing-dial or outgoing-alerting (ringing on remote side)
                self._call_state = CallState.OUTGOING
            elif value == 0 and self._call_state in (CallState.INCOMING, CallState.OUTGOING):
                # callsetup went to idle without call becoming active:
                # incoming → missed/rejected; outgoing → no answer / cancelled
                self._call_state = CallState.IDLE
                self._caller_number = ""
                if self._on_call_ended:
                    self._on_call_ended()

    def _parse_cind_format(self, line: str):
        """
        Parse +CIND: ("call",(0,1)),("callsetup",(0-3)),...
        to build self._indicator_map: {1: "call", 2: "callsetup", ...}
        """
        import re
        names = re.findall(r'"([^"]+)"', line)
        self._indicator_map = {i + 1: name for i, name in enumerate(names)}
        logger.debug("Indicator map: %s", self._indicator_map)

    def _apply_cind_values(self, values: list[int]):
        """Apply initial indicator values from AT+CIND? response."""
        for i, val in enumerate(values):
            name = self._indicator_map.get(i + 1)
            if name == "call" and val == 1:
                self._call_state = CallState.ACTIVE
            elif name == "callsetup" and val == 1:
                self._call_state = CallState.INCOMING

    def _read_line(self) -> Optional[str]:
        """
        Read one CR/LF-terminated line from the RFCOMM socket.
        Returns None on timeout (allows checking self._stop).
        """
        buf = b""
        try:
            while not self._stop.is_set():
                chunk = self._sock.recv(1)
                if not chunk:
                    # Connection closed by phone
                    self._state = SLCState.DISCONNECTED
                    self._stop.set()
                    return None
                buf += chunk
                if buf.endswith(b"\r") or buf.endswith(b"\n"):
                    line = buf.decode("ascii", errors="replace").strip()
                    if line:
                        logger.debug("← %s", line)
                    return line if line else None
        except socket.timeout:
            return None  # Normal — just check _stop and retry
        except OSError as e:
            logger.error("Socket read error: %s", e)
            self._state = SLCState.DISCONNECTED
            self._stop.set()
            return None
        return None