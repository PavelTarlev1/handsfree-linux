"""Tests for audio/sco_bridge.py — SCO audio bridge."""
import subprocess
import threading
from unittest.mock import MagicMock, patch

import pytest


class TestSCOBridgeStop:
    def _bridge_with_proc(self, proc):
        from audio.sco_bridge import SCOBridge
        bridge = SCOBridge()
        bridge._play_proc = proc
        bridge._rec_proc = None
        bridge._sco_sock = None
        bridge._running = True
        return bridge

    def test_kill_called_when_terminate_times_out(self):
        """Hung pacat process must be killed, not just terminated."""
        proc = MagicMock()
        proc.wait.side_effect = subprocess.TimeoutExpired(cmd="pacat", timeout=2)
        self._bridge_with_proc(proc).stop()
        proc.kill.assert_called_once()

    def test_normal_exit_does_not_call_kill(self):
        proc = MagicMock()
        proc.wait.return_value = 0
        self._bridge_with_proc(proc).stop()
        proc.kill.assert_not_called()


class TestSCOBridgeMSBCInit:
    """Patch threading.Thread so no real loops spin during tests."""

    def _patched_start_audio(self, bridge, codec, msbc_mod, msbc_side_effect):
        fake_popen = MagicMock(
            return_value=MagicMock(stdin=MagicMock(), stdout=MagicMock())
        )
        fake_thread = MagicMock(spec=threading.Thread)

        with patch.object(msbc_mod, "MSBCCodec", side_effect=msbc_side_effect), \
             patch.object(msbc_mod, "AVAILABLE", True), \
             patch("audio.sco_bridge.subprocess.Popen", fake_popen), \
             patch("audio.sco_bridge.socket.fromfd", return_value=MagicMock()), \
             patch("audio.sco_bridge.threading.Thread", return_value=fake_thread), \
             patch("os.close"):
            return bridge._start_audio(3, "", "", codec)

    def test_no_name_error_when_msbc_init_raises(self):
        """Before fix: rx_codec unbound → NameError. Now falls back to CVSD."""
        import audio.msbc as msbc_mod
        from audio.sco_bridge import SCOBridge
        from bluetooth.at_handler import CODEC_MSBC

        bridge = SCOBridge()
        result = self._patched_start_audio(
            bridge, CODEC_MSBC, msbc_mod,
            RuntimeError("sbc_init_msbc failed: -1"),
        )
        assert result in (True, False)

    def test_rx_codec_closed_when_tx_codec_raises(self):
        """If tx MSBCCodec() raises, the already-created rx codec must be closed."""
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

        self._patched_start_audio(bridge, CODEC_MSBC, msbc_mod, make_codec)
        rx.close.assert_called_once()
