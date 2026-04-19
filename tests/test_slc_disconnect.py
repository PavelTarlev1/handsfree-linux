"""
Tests for SLCConnection disconnect-during-call behaviour.
Verifies that on_call_ended fires before on_disconnected when the
connection drops while a call is active (the bug fixed in slc.py).
"""
import socket
import threading
import pytest

from bluetooth.slc import SLCConnection, CallState


def _make_slc(on_call_ended=None, on_disconnected=None, call_state=CallState.IDLE):
    """Return an SLCConnection with a real loopback socket pair, pre-seeded call state."""
    a, b = socket.socketpair()
    slc = SLCConnection(
        sock=a,
        device_path="/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF",
        on_call_ended=on_call_ended,
        on_disconnected=on_disconnected,
    )
    slc._call_state = call_state
    b.close()   # close the other end so reads on `a` return EOF immediately
    return slc, a


class TestDisconnectDuringCall:
    def test_on_call_ended_fires_when_active(self):
        events = []
        slc, _ = _make_slc(
            on_call_ended=lambda: events.append("call_ended"),
            on_disconnected=lambda p: events.append("disconnected"),
            call_state=CallState.ACTIVE,
        )
        # Simulate the finally block directly
        slc._call_state_cleanup_on_disconnect()
        assert events == ["call_ended", "disconnected"]

    def test_on_call_ended_fires_when_incoming(self):
        events = []
        slc, _ = _make_slc(
            on_call_ended=lambda: events.append("call_ended"),
            on_disconnected=lambda p: events.append("disconnected"),
            call_state=CallState.INCOMING,
        )
        slc._call_state_cleanup_on_disconnect()
        assert events == ["call_ended", "disconnected"]

    def test_no_call_ended_when_idle(self):
        events = []
        slc, _ = _make_slc(
            on_call_ended=lambda: events.append("call_ended"),
            on_disconnected=lambda p: events.append("disconnected"),
            call_state=CallState.IDLE,
        )
        slc._call_state_cleanup_on_disconnect()
        assert events == ["disconnected"]
        assert "call_ended" not in events

    def test_call_state_reset_to_idle(self):
        slc, _ = _make_slc(call_state=CallState.ACTIVE)
        slc._call_state_cleanup_on_disconnect()
        assert slc._call_state == CallState.IDLE

    def test_no_crash_without_callbacks(self):
        slc, _ = _make_slc(call_state=CallState.ACTIVE)
        slc._call_state_cleanup_on_disconnect()   # should not raise

    def test_call_ended_fires_before_disconnected(self):
        """Order must be: call_ended first, then disconnected."""
        order = []
        slc, _ = _make_slc(
            on_call_ended=lambda: order.append(1),
            on_disconnected=lambda p: order.append(2),
            call_state=CallState.ACTIVE,
        )
        slc._call_state_cleanup_on_disconnect()
        assert order == [1, 2]
