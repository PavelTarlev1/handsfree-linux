"""
Tests for SLCConnection._handle_ciev and _dispatch — the post-SLC call state machine.

Covers:
  - call indicator: 0/1 transitions
  - callsetup indicator: incoming, outgoing-dial, outgoing-alerting, cancelled
  - RING + CLIP ordering
  - BCS codec confirmation
  - NO CARRIER
  - dial error path
  - outgoing call not ending (regression: callsetup=0 from OUTGOING state)
"""
import socket
import pytest

from bluetooth.slc import SLCConnection, CallState
from bluetooth import at_handler as AT


def _make_slc(**kwargs) -> SLCConnection:
    """Return an SLCConnection wired to a dead socket (not used in these unit tests)."""
    a, b = socket.socketpair()
    b.close()
    defaults = dict(
        sock=a,
        device_path="/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF",
    )
    defaults.update(kwargs)
    slc = SLCConnection(**defaults)
    # Pre-seed a basic indicator map so CIEV indices resolve to names
    slc._indicator_map = {1: "call", 2: "callsetup", 3: "callheld", 4: "signal", 5: "battchg"}
    return slc


# ── call indicator ────────────────────────────────────────────────────────────

class TestCievCall:
    def test_call_1_transitions_to_active(self):
        fired = []
        slc = _make_slc(on_call_active=lambda: fired.append("active"))
        slc._handle_ciev({"index": 1, "value": 1})
        assert slc._call_state == CallState.ACTIVE
        assert fired == ["active"]

    def test_call_1_does_not_double_fire_if_already_active(self):
        fired = []
        slc = _make_slc(on_call_active=lambda: fired.append("active"))
        slc._call_state = CallState.ACTIVE
        slc._handle_ciev({"index": 1, "value": 1})
        assert fired == []

    def test_call_0_from_active_fires_ended(self):
        fired = []
        slc = _make_slc(on_call_ended=lambda: fired.append("ended"))
        slc._call_state = CallState.ACTIVE
        slc._handle_ciev({"index": 1, "value": 0})
        assert slc._call_state == CallState.IDLE
        assert fired == ["ended"]

    def test_call_0_from_incoming_fires_ended(self):
        fired = []
        slc = _make_slc(on_call_ended=lambda: fired.append("ended"))
        slc._call_state = CallState.INCOMING
        slc._handle_ciev({"index": 1, "value": 0})
        assert fired == ["ended"]

    def test_call_0_from_outgoing_fires_ended(self):
        """Regression: outgoing call dropped without answer must fire on_call_ended."""
        fired = []
        slc = _make_slc(on_call_ended=lambda: fired.append("ended"))
        slc._call_state = CallState.OUTGOING
        slc._handle_ciev({"index": 1, "value": 0})
        assert fired == ["ended"]

    def test_call_0_from_idle_does_not_fire(self):
        fired = []
        slc = _make_slc(on_call_ended=lambda: fired.append("ended"))
        slc._call_state = CallState.IDLE
        slc._handle_ciev({"index": 1, "value": 0})
        assert fired == []

    def test_call_0_clears_caller_number(self):
        slc = _make_slc()
        slc._call_state = CallState.ACTIVE
        slc._caller_number = "+1234567890"
        slc._handle_ciev({"index": 1, "value": 0})
        assert slc._caller_number == ""


# ── callsetup indicator ───────────────────────────────────────────────────────

class TestCievCallsetup:
    def test_callsetup_1_incoming(self):
        """callsetup=1 is incoming-ringing; we do not emit anything from CIEV alone."""
        slc = _make_slc()
        slc._handle_ciev({"index": 2, "value": 1})
        # No state change expected from callsetup=1 in our implementation
        assert slc._call_state == CallState.IDLE

    def test_callsetup_2_sets_outgoing(self):
        slc = _make_slc()
        slc._handle_ciev({"index": 2, "value": 2})
        assert slc._call_state == CallState.OUTGOING

    def test_callsetup_3_sets_outgoing(self):
        slc = _make_slc()
        slc._handle_ciev({"index": 2, "value": 3})
        assert slc._call_state == CallState.OUTGOING

    def test_callsetup_0_from_outgoing_fires_ended(self):
        """Regression: the remote hangs up before answering — must fire on_call_ended."""
        fired = []
        slc = _make_slc(on_call_ended=lambda: fired.append("ended"))
        slc._call_state = CallState.OUTGOING
        slc._handle_ciev({"index": 2, "value": 0})
        assert fired == ["ended"]
        assert slc._call_state == CallState.IDLE

    def test_callsetup_0_from_incoming_fires_ended(self):
        """Missed/rejected incoming call must fire on_call_ended."""
        fired = []
        slc = _make_slc(on_call_ended=lambda: fired.append("ended"))
        slc._call_state = CallState.INCOMING
        slc._handle_ciev({"index": 2, "value": 0})
        assert fired == ["ended"]
        assert slc._call_state == CallState.IDLE

    def test_callsetup_0_from_active_does_not_fire_ended(self):
        """Active call: callsetup goes to 0 normally (not a hang-up signal)."""
        fired = []
        slc = _make_slc(on_call_ended=lambda: fired.append("ended"))
        slc._call_state = CallState.ACTIVE
        slc._handle_ciev({"index": 2, "value": 0})
        assert fired == []

    def test_callsetup_0_from_outgoing_clears_caller_number(self):
        slc = _make_slc()
        slc._call_state = CallState.OUTGOING
        slc._caller_number = "+1234567890"
        slc._handle_ciev({"index": 2, "value": 0})
        assert slc._caller_number == ""


# ── RING + CLIP ───────────────────────────────────────────────────────────────

class TestRingClip:
    def test_ring_sets_incoming(self):
        slc = _make_slc()
        slc._dispatch("RING")
        assert slc._call_state == CallState.INCOMING

    def test_ring_fires_on_ring_with_empty_number(self):
        fired = []
        slc = _make_slc(on_ring=lambda n: fired.append(n))
        slc._dispatch("RING")
        assert fired == [""]

    def test_clip_updates_caller_number(self):
        slc = _make_slc()
        slc._dispatch('+CLIP: "+972501234567",145')
        assert slc._caller_number == "+972501234567"

    def test_clip_re_emits_ring_if_already_ringing(self):
        fired = []
        slc = _make_slc(on_ring=lambda n: fired.append(n))
        slc._call_state = CallState.INCOMING
        slc._dispatch('+CLIP: "+972501234567",145')
        assert "+972501234567" in fired

    def test_ring_then_clip_emits_number(self):
        fired = []
        slc = _make_slc(on_ring=lambda n: fired.append(n))
        slc._dispatch("RING")
        slc._dispatch('+CLIP: "+972501234567",145')
        assert "+972501234567" in fired


# ── BCS codec negotiation ─────────────────────────────────────────────────────

class TestBCS:
    def test_bcs_fires_codec_negotiated(self):
        fired = []
        slc = _make_slc(on_codec_negotiated=lambda c: fired.append(c))
        slc._dispatch("+BCS: 2")
        assert fired == [AT.CODEC_MSBC]

    def test_bcs_cvsd(self):
        fired = []
        slc = _make_slc(on_codec_negotiated=lambda c: fired.append(c))
        slc._dispatch("+BCS: 1")
        assert fired == [AT.CODEC_CVSD]


# ── NO CARRIER ───────────────────────────────────────────────────────────────

class TestNoCarrier:
    def test_no_carrier_fires_ended(self):
        fired = []
        slc = _make_slc(on_call_ended=lambda: fired.append("ended"))
        slc._call_state = CallState.ACTIVE
        slc._dispatch("NO CARRIER")
        assert fired == ["ended"]
        assert slc._call_state == CallState.IDLE

    def test_no_carrier_clears_caller_number(self):
        slc = _make_slc()
        slc._caller_number = "+1234567890"
        slc._dispatch("NO CARRIER")
        assert slc._caller_number == ""


# ── Dial error path ───────────────────────────────────────────────────────────

class TestDialError:
    def test_error_while_dialing_fires_dial_error(self):
        fired = []
        slc = _make_slc(on_dial_error=lambda: fired.append("error"))
        slc._dialing = True
        slc._dispatch("ERROR")
        assert fired == ["error"]
        assert slc._dialing is False

    def test_ok_while_dialing_clears_flag(self):
        slc = _make_slc()
        slc._dialing = True
        slc._dispatch("OK")
        assert slc._dialing is False

    def test_error_not_dialing_does_not_fire(self):
        fired = []
        slc = _make_slc(on_dial_error=lambda: fired.append("error"))
        slc._dialing = False
        slc._dispatch("ERROR")
        assert fired == []


# ── _parse_cind_format ────────────────────────────────────────────────────────

class TestParseCindFormat:
    def test_parses_standard_indicators(self):
        slc = _make_slc()
        slc._parse_cind_format('+CIND: ("call",(0,1)),("callsetup",(0-3)),("callheld",(0-2))')
        assert slc._indicator_map == {1: "call", 2: "callsetup", 3: "callheld"}

    def test_parses_iphone_indicators(self):
        slc = _make_slc()
        line = (
            '+CIND: ("service",(0,1)),("call",(0,1)),("callsetup",(0-3)),'
            '("callheld",(0-2)),("signal",(0-5)),("roam",(0,1)),("battchg",(0-5))'
        )
        slc._parse_cind_format(line)
        assert slc._indicator_map[2] == "call"
        assert slc._indicator_map[3] == "callsetup"


# ── _apply_cind_values ────────────────────────────────────────────────────────

class TestApplyCindValues:
    def test_call_active_on_reconnect(self):
        slc = _make_slc()
        slc._indicator_map = {1: "call", 2: "callsetup"}
        slc._apply_cind_values([1, 0])
        assert slc._call_state == CallState.ACTIVE

    def test_incoming_on_reconnect(self):
        slc = _make_slc()
        slc._indicator_map = {1: "call", 2: "callsetup"}
        slc._apply_cind_values([0, 1])
        assert slc._call_state == CallState.INCOMING

    def test_idle_state_unchanged(self):
        slc = _make_slc()
        slc._indicator_map = {1: "call", 2: "callsetup"}
        slc._apply_cind_values([0, 0])
        assert slc._call_state == CallState.IDLE
