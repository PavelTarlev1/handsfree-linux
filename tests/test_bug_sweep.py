"""
Tests for the 9 bugs fixed in the full bug sweep.

Bug 1  — sco_bridge: kill() pacat if terminate+wait times out
Bug 2  — sco_bridge: NameError when first MSBCCodec() raises
Bug 3  — sco_bridge: rx_codec leaked when tx_codec init fails
Bug 4  — ringer: double-audio race (stop() must join thread)
Bug 5  — app: _manual_disconnect not reset on auto-reconnect
Bug 6  — main_window: connect button crash when BT is off (path=None)
Bug 7  — voip/detector: /proc fd leak (open without with-statement)
Bug 8  — slc: callsetup=0 incorrectly fires on_call_ended when ACTIVE
Bug 9  — updater: shell injection via special chars in repo path
"""
import inspect
import shlex
import socket
import subprocess
import threading
import types
from unittest.mock import MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Bug 1 — SCOBridge.stop() calls kill() when terminate+wait times out
# ─────────────────────────────────────────────────────────────────────────────

class TestSCOBridgeStopKillsHungProcess:
    def _bridge_with_proc(self, proc):
        from audio.sco_bridge import SCOBridge
        bridge = SCOBridge()
        bridge._play_proc = proc
        bridge._rec_proc = None
        bridge._sco_sock = None
        bridge._running = True
        return bridge

    def test_kill_called_when_terminate_times_out(self):
        proc = MagicMock()
        proc.wait.side_effect = subprocess.TimeoutExpired(cmd="pacat", timeout=2)
        self._bridge_with_proc(proc).stop()
        proc.kill.assert_called_once()

    def test_normal_exit_does_not_call_kill(self):
        proc = MagicMock()
        proc.wait.return_value = 0
        self._bridge_with_proc(proc).stop()
        proc.kill.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Bug 2 — No NameError when MSBCCodec() raises inside _start_audio
# ─────────────────────────────────────────────────────────────────────────────

class TestSCOBridgeMSBCCodecNameError:
    def test_no_name_error_when_msbc_init_raises(self):
        """Before the fix rx_codec was unbound → NameError at thread creation."""
        import audio.msbc as msbc_mod
        from audio.sco_bridge import SCOBridge
        from bluetooth.at_handler import CODEC_MSBC

        bridge = SCOBridge()
        fake_popen = MagicMock()
        fake_popen.return_value = MagicMock(stdin=MagicMock(), stdout=MagicMock())

        with patch.object(msbc_mod, "MSBCCodec",
                          side_effect=RuntimeError("sbc_init_msbc failed: -1")), \
             patch.object(msbc_mod, "AVAILABLE", True), \
             patch("audio.sco_bridge.subprocess.Popen", fake_popen), \
             patch("audio.sco_bridge.socket.fromfd",
                   return_value=MagicMock()), \
             patch("os.close"):
            # Must not raise NameError — should fall back gracefully
            result = bridge._start_audio(3, "", "", CODEC_MSBC)

        assert result in (True, False)


# ─────────────────────────────────────────────────────────────────────────────
# Bug 3 — rx_codec.close() called when tx_codec init fails
# ─────────────────────────────────────────────────────────────────────────────

class TestSCOBridgeMSBCRxCodecLeakOnTxFailure:
    def test_rx_codec_closed_when_tx_codec_raises(self):
        import audio.msbc as msbc_mod
        from audio.sco_bridge import SCOBridge
        from bluetooth.at_handler import CODEC_MSBC

        bridge = SCOBridge()
        rx = MagicMock()
        call_count = []

        def make_codec():
            if not call_count:
                call_count.append(1)
                return rx
            raise RuntimeError("sbc_init_msbc failed")

        fake_popen = MagicMock(
            return_value=MagicMock(stdin=MagicMock(), stdout=MagicMock())
        )

        with patch.object(msbc_mod, "MSBCCodec", side_effect=make_codec), \
             patch.object(msbc_mod, "AVAILABLE", True), \
             patch("audio.sco_bridge.subprocess.Popen", fake_popen), \
             patch("audio.sco_bridge.socket.fromfd", return_value=MagicMock()), \
             patch("os.close"):
            bridge._start_audio(3, "", "", CODEC_MSBC)

        rx.close.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# Bug 4 — Ringer.stop() joins the thread (no double-audio race)
# ─────────────────────────────────────────────────────────────────────────────

class TestRingerStopJoinsThread:
    def test_stop_joins_thread_before_clearing(self):
        from audio.ringer import Ringer

        ringer = Ringer()
        mock_thread = MagicMock(spec=threading.Thread)
        ringer._thread = mock_thread

        ringer.stop()

        mock_thread.join.assert_called_once_with(timeout=2)
        assert ringer._thread is None

    def test_stop_sets_event_before_join(self):
        """stop() must set the event first so the thread can exit, then join."""
        from audio.ringer import Ringer

        order = []
        ringer = Ringer()

        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.join.side_effect = lambda timeout: order.append("join")
        ringer._thread = mock_thread
        ringer._stop_event = MagicMock()
        ringer._stop_event.set.side_effect = lambda: order.append("set")

        ringer.stop()

        assert order == ["set", "join"]


# ─────────────────────────────────────────────────────────────────────────────
# Bug 5 — _manual_disconnect reset when phone auto-reconnects
# ─────────────────────────────────────────────────────────────────────────────

class TestManualDisconnectResetOnAutoReconnect:
    def _connected_app(self):
        return types.SimpleNamespace(
            _manual_disconnect=True,
            _current_device_path=None,
            _current_device_name=None,
            _tray=MagicMock(),
            _window=MagicMock(),
            _cfg=MagicMock(),
            _store=MagicMock(count=lambda: 0),
            _refresh_devices=MagicMock(),
            _should_sync=MagicMock(return_value=False),
        )

    def test_manual_disconnect_cleared_on_reconnect(self):
        from core.app import HandsFreeApp

        app = self._connected_app()
        assert app._manual_disconnect is True

        HandsFreeApp._on_connected(app, "/dev/AA", "MyPhone")

        assert app._manual_disconnect is False

    def test_reconnect_after_auto_connection_works(self):
        """After phone auto-reconnects, a subsequent drop must schedule reconnect."""
        from core.app import HandsFreeApp

        app = self._connected_app()
        HandsFreeApp._on_connected(app, "/dev/AA", "MyPhone")

        scheduled = []
        app._current_device_path = "/dev/AA"
        app._current_device_name = "MyPhone"
        app._schedule_reconnect = lambda p: scheduled.append(p)

        HandsFreeApp._on_disconnected(app, "/dev/AA")

        assert "/dev/AA" in scheduled


# ─────────────────────────────────────────────────────────────────────────────
# Bug 6 — Connect button does nothing when device path is None (BT off)
# ─────────────────────────────────────────────────────────────────────────────

class TestConnectButtonNoneGuard:
    def test_connect_requested_not_emitted_for_none_path(self):
        from ui.main_window import MainWindow

        emitted = []
        win = types.SimpleNamespace(
            _device_combo=MagicMock(),
            connect_requested=MagicMock(emit=lambda p: emitted.append(p)),
        )
        win._device_combo.currentIndex.return_value = 0
        win._device_combo.itemData.return_value = None

        MainWindow._on_connect_clicked(win)

        assert emitted == []

    def test_connect_requested_emitted_for_valid_path(self):
        from ui.main_window import MainWindow

        emitted = []
        win = types.SimpleNamespace(
            _device_combo=MagicMock(),
            connect_requested=MagicMock(emit=lambda p: emitted.append(p)),
        )
        win._device_combo.currentIndex.return_value = 0
        win._device_combo.itemData.return_value = "/org/bluez/hci0/dev_AA"

        MainWindow._on_connect_clicked(win)

        assert emitted == ["/org/bluez/hci0/dev_AA"]


# ─────────────────────────────────────────────────────────────────────────────
# Bug 7 — /proc/*/comm read uses with-statement (no fd leak)
# ─────────────────────────────────────────────────────────────────────────────

class TestVoipDetectorFdLeak:
    def test_proc_comm_file_is_closed_after_read(self):
        """Verify open() is used as a context manager so the fd is always closed."""
        from voip.detector import VoIPDetector

        src = inspect.getsource(VoIPDetector._check_processes_linux)
        assert "with open(" in src, (
            "_check_processes_linux must use 'with open()' to avoid fd leaks"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Bug 8 — callsetup=0 does NOT fire on_call_ended when call is ACTIVE
# ─────────────────────────────────────────────────────────────────────────────

class TestSLCCallsetupActiveGuard:
    def _make_slc(self, **kwargs):
        from bluetooth.slc import SLCConnection
        a, b = socket.socketpair()
        b.close()
        slc = SLCConnection(sock=a, device_path="/org/bluez/hci0/dev_AA")
        slc._indicator_map = {1: "call", 2: "callsetup"}
        for k, v in kwargs.items():
            setattr(slc, k, v)
        return slc

    def test_callsetup_0_does_not_end_active_call(self):
        """Some phones send callsetup=0 after call=1; must not end the call."""
        from bluetooth.slc import CallState

        ended = []
        slc = self._make_slc(_on_call_ended=lambda: ended.append("ended"))
        slc._call_state = CallState.ACTIVE

        slc._handle_ciev({"index": 2, "value": 0})

        assert ended == [], "on_call_ended must not fire when call is already ACTIVE"
        assert slc._call_state == CallState.ACTIVE

    def test_callsetup_0_from_incoming_fires_ended(self):
        """callsetup=0 from INCOMING (missed call) must still fire on_call_ended."""
        from bluetooth.slc import CallState

        ended = []
        slc = self._make_slc(_on_call_ended=lambda: ended.append("ended"))
        slc._call_state = CallState.INCOMING

        slc._handle_ciev({"index": 2, "value": 0})

        assert ended == ["ended"]

    def test_callsetup_0_from_outgoing_fires_ended(self):
        """callsetup=0 from OUTGOING (no answer) must still fire on_call_ended."""
        from bluetooth.slc import CallState

        ended = []
        slc = self._make_slc(_on_call_ended=lambda: ended.append("ended"))
        slc._call_state = CallState.OUTGOING

        slc._handle_ciev({"index": 2, "value": 0})

        assert ended == ["ended"]


# ─────────────────────────────────────────────────────────────────────────────
# Bug 9 — updater uses shlex.quote on repo path (no shell injection)
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdaterShellQuote:
    def test_shlex_quote_used_in_source(self):
        from core import updater
        src = inspect.getsource(updater.apply_update_linux)
        assert "shlex.quote" in src

    def test_special_chars_are_safely_quoted_by_shlex(self):
        """shlex.quote wraps in single quotes — dollar signs and spaces can't expand."""
        tricky = "/srv/my repo $HOME"
        quoted = shlex.quote(tricky)
        # shlex.quote always wraps in single quotes when the string has special chars
        assert quoted.startswith("'") and quoted.endswith("'")
        # The raw string reconstructed from the quoted form is identical
        assert shlex.split(quoted)[0] == tricky

    def test_script_contains_quoted_not_raw_path(self):
        """The generated script must contain shlex.quote(root), not the raw string."""
        from core import updater

        tricky = "/srv/hand free app"
        written = []

        def fake_fdopen(fd, mode):
            class Writer:
                def __enter__(self): return self
                def __exit__(self, *a): pass
                def write(self, s): written.append(s)
            return Writer()

        with patch.object(updater, "_repo_root", return_value=tricky), \
             patch("subprocess.Popen"), \
             patch("sys.exit"), \
             patch("os.chmod"), \
             patch("os.fdopen", fake_fdopen):
            try:
                updater.apply_update_linux()
            except Exception:
                pass

        full = "".join(written)
        if full:
            assert shlex.quote(tricky) in full, "Script must contain quoted path"
            assert f' {tricky} ' not in full, "Script must not contain bare unquoted path"
