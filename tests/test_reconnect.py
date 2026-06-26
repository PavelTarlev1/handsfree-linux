"""Tests for manual-disconnect reconnect suppression.

HandsFreeApp is a QObject subclass so we cannot instantiate it directly.
Instead we call the unbound methods with a MagicMock that provides only
the attributes each method actually touches.
"""
import types
import threading
from unittest.mock import MagicMock, patch

import pytest

from core.app import HandsFreeApp


def _app(**attrs):
    """Return a SimpleNamespace with the given attribute values.

    SimpleNamespace accepts any attribute (including underscore names),
    making it a clean stand-in for HandsFreeApp without needing Qt.
    """
    return types.SimpleNamespace(**attrs)


class TestManualDisconnectFlag:
    def _disconnected_app(self, **extra):
        return _app(
            _tray=MagicMock(),
            _window=MagicMock(),
            _reconnect_timer=None,
            **extra,
        )

    def test_reconnect_scheduled_on_unexpected_disconnect(self):
        """Disconnect not triggered by user → reconnect should fire."""
        scheduled = []
        app = self._disconnected_app(
            _current_device_path="/dev/AA",
            _current_device_name="MyPhone",
            _manual_disconnect=False,
            _schedule_reconnect=lambda p: scheduled.append(p),
        )

        HandsFreeApp._on_disconnected(app, "/dev/AA")

        assert "/dev/AA" in scheduled

    def test_reconnect_suppressed_after_manual_disconnect(self):
        """Disconnect triggered by user → reconnect must NOT fire."""
        scheduled = []
        app = self._disconnected_app(
            _current_device_path="/dev/AA",
            _current_device_name="MyPhone",
            _manual_disconnect=True,
            _schedule_reconnect=lambda p: scheduled.append(p),
        )

        HandsFreeApp._on_disconnected(app, "/dev/AA")

        assert scheduled == []

    def test_ignores_disconnect_for_other_device(self):
        """Disconnect for a device we're not connected to is silently ignored."""
        scheduled = []
        app = self._disconnected_app(
            _current_device_path="/dev/BB",
            _current_device_name="OtherPhone",
            _manual_disconnect=False,
            _schedule_reconnect=lambda p: scheduled.append(p),
        )

        HandsFreeApp._on_disconnected(app, "/dev/AA")

        assert scheduled == []
        assert app._current_device_path == "/dev/BB"

    def test_manual_flag_cleared_on_connect_request(self):
        """User clicking Connect resets the flag so future drops auto-reconnect."""
        app = _app(
            _manual_disconnect=True,
            _connect_device_thread=MagicMock(),
            _hfp=MagicMock(get_paired_devices=lambda: [
                {"path": "/dev/AA", "name": "MyPhone"}
            ]),
            _bridge=MagicMock(),
        )

        with patch("threading.Thread"):
            HandsFreeApp._on_connect_device_requested(app, "/dev/AA")

        assert app._manual_disconnect is False

    def test_reconnect_timer_cancelled_when_bt_turns_off(self):
        """Pending reconnect timer must be cancelled if adapter powers off."""
        timer = MagicMock()
        app = _app(_bridge=MagicMock(), _reconnect_timer=timer)

        HandsFreeApp._cb_bt_powered(app, False)

        timer.cancel.assert_called_once()
        assert app._reconnect_timer is None

    def test_no_crash_when_no_reconnect_timer_on_bt_off(self):
        app = _app(_bridge=MagicMock(), _reconnect_timer=None)
        HandsFreeApp._cb_bt_powered(app, False)  # must not raise

    def test_bt_powered_signal_emitted(self):
        bridge = MagicMock()
        app = _app(_bridge=bridge, _reconnect_timer=None)

        HandsFreeApp._cb_bt_powered(app, True)
        bridge.sig_bt_powered.emit.assert_called_once_with(True)

        HandsFreeApp._cb_bt_powered(app, False)
        bridge.sig_bt_powered.emit.assert_called_with(False)


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

    def test_manual_disconnect_cleared_on_auto_reconnect(self):
        """Phone reconnecting autonomously must clear _manual_disconnect."""
        from core.app import HandsFreeApp

        app = self._connected_app()
        assert app._manual_disconnect is True

        HandsFreeApp._on_connected(app, "/dev/AA", "MyPhone")

        assert app._manual_disconnect is False

    def test_auto_reconnect_works_after_phone_reconnects(self):
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
