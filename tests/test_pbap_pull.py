"""Tests for PBAP pull logic — fast bail on session death, error classification."""
from unittest.mock import MagicMock, patch, call

import pytest

from bluetooth.pbap_client import PBAPClient, PBAPPullError, PBAPUnauthorizedError


def _dbus_error(name: str, msg: str = "error") -> Exception:
    """Fake a dbus exception with a given error name."""
    exc = Exception(msg)
    exc.get_dbus_name = lambda: name
    return exc


class TestPullAllFastBail:
    """_pull_all_with_fallback must stop immediately on UnknownObject."""

    def _client_with_iface(self, side_effect):
        client = PBAPClient()
        client._session_path = "/org/bluez/obex/client/session0"
        iface = MagicMock()
        iface.PullAll.side_effect = side_effect
        client._pbap_iface = iface
        return client, iface

    def test_unknown_object_raises_immediately(self):
        exc = _dbus_error("org.freedesktop.DBus.Error.UnknownObject")
        client, iface = self._client_with_iface(exc)
        with pytest.raises(PBAPPullError, match="Session closed"):
            client._pull_all_with_fallback("/tmp/test.vcf", None)
        # Must stop after the first call, not try all 4 signatures
        assert iface.PullAll.call_count == 1

    def test_other_errors_try_all_signatures(self):
        exc = _dbus_error("org.bluez.obex.Error.Failed", "signature mismatch")
        client, iface = self._client_with_iface(exc)
        with pytest.raises(PBAPPullError):
            client._pull_all_with_fallback("/tmp/test.vcf", None)
        # Should try all 3 signatures (+ optional bus proxy = 4 if bus is None → 3)
        assert iface.PullAll.call_count >= 3

    def test_returns_on_first_success(self):
        transfer_path = "/org/bluez/obex/transfer0"
        iface = MagicMock()
        iface.PullAll.return_value = (transfer_path, {})
        client = PBAPClient()
        client._session_path = "/session"
        client._pbap_iface = iface

        result_path, _ = client._pull_all_with_fallback("/tmp/test.vcf", None)
        assert str(result_path) == transfer_path
        assert iface.PullAll.call_count == 1


class TestConnectAuthError:
    """connect() must raise PBAPUnauthorizedError on OBEX 0x41."""

    def _client(self):
        return PBAPClient()

    def test_0x41_in_message_raises_unauthorized(self):
        client = self._client()
        exc = Exception("OBEX error 0x41")

        with patch.object(client, "_ensure_obexd", return_value=True), \
             patch.object(client, "_find_pbap_channel", return_value=None), \
             patch.object(client, "_get_session_bus") as mock_bus:
            mock_bus.return_value.get_object.return_value = MagicMock()
            obex_client = MagicMock()
            obex_client.CreateSession.side_effect = exc
            import dbus
            with patch("dbus.Interface", return_value=obex_client):
                with pytest.raises(PBAPUnauthorizedError):
                    client.connect("AA:BB:CC:DD:EE:FF")

    def test_unauthorized_in_message_raises_unauthorized(self):
        client = self._client()
        exc = Exception("Unauthorized access denied")

        with patch.object(client, "_ensure_obexd", return_value=True), \
             patch.object(client, "_find_pbap_channel", return_value=None), \
             patch.object(client, "_get_session_bus") as mock_bus:
            mock_bus.return_value.get_object.return_value = MagicMock()
            obex_client = MagicMock()
            obex_client.CreateSession.side_effect = exc
            import dbus
            with patch("dbus.Interface", return_value=obex_client):
                with pytest.raises(PBAPUnauthorizedError):
                    client.connect("AA:BB:CC:DD:EE:FF")

    def test_generic_error_returns_false(self):
        client = self._client()
        exc = Exception("Connection refused")

        with patch.object(client, "_ensure_obexd", return_value=True), \
             patch.object(client, "_find_pbap_channel", return_value=None), \
             patch.object(client, "_get_session_bus") as mock_bus:
            mock_bus.return_value.get_object.return_value = MagicMock()
            obex_client = MagicMock()
            obex_client.CreateSession.side_effect = exc
            import dbus
            with patch("dbus.Interface", return_value=obex_client):
                result = client.connect("AA:BB:CC:DD:EE:FF")
        assert result is False

    def test_connect_returns_false_when_obexd_unavailable(self):
        client = self._client()
        with patch.object(client, "_ensure_obexd", return_value=False):
            assert client.connect("AA:BB:CC:DD:EE:FF") is False


class TestConnectChannelFallback:
    """connect() tries explicit channel first, then auto-discovery."""

    def test_uses_explicit_channel_when_sdp_succeeds(self):
        client = PBAPClient()
        created_with = []

        with patch.object(client, "_ensure_obexd", return_value=True):
            with patch.object(client, "_find_pbap_channel", return_value=13):
                with patch.object(client, "_get_session_bus") as mock_bus:
                    mock_bus.return_value.get_object.return_value = MagicMock()
                    obex_iface = MagicMock()
                    obex_iface.CreateSession.side_effect = lambda addr, params: (
                        created_with.append(params) or "/session/0"
                    )
                    import dbus
                    with patch("dbus.Interface", return_value=obex_iface):
                        client.connect("AA:BB:CC:DD:EE:FF")

        assert len(created_with) >= 1
        assert "Channel" in created_with[0]

    def test_falls_back_to_auto_when_sdp_returns_none(self):
        client = PBAPClient()
        created_with = []

        with patch.object(client, "_ensure_obexd", return_value=True):
            with patch.object(client, "_find_pbap_channel", return_value=None):
                with patch.object(client, "_get_session_bus") as mock_bus:
                    mock_bus.return_value.get_object.return_value = MagicMock()
                    obex_iface = MagicMock()
                    obex_iface.CreateSession.side_effect = lambda addr, params: (
                        created_with.append(params) or "/session/0"
                    )
                    import dbus
                    with patch("dbus.Interface", return_value=obex_iface):
                        client.connect("AA:BB:CC:DD:EE:FF")

        assert len(created_with) >= 1
        assert "Channel" not in created_with[0]

    def test_falls_back_to_auto_when_channel_connect_fails(self):
        """If explicit-channel CreateSession fails, try without channel."""
        client = PBAPClient()
        attempt_params = []

        def fake_create_session(addr, params):
            attempt_params.append(dict(params))
            if "Channel" in params:
                raise Exception("RFCOMM connection failed")
            return "/session/0"

        with patch.object(client, "_ensure_obexd", return_value=True):
            with patch.object(client, "_find_pbap_channel", return_value=13):
                with patch.object(client, "_get_session_bus") as mock_bus:
                    mock_bus.return_value.get_object.return_value = MagicMock()
                    obex_iface = MagicMock()
                    obex_iface.CreateSession.side_effect = fake_create_session
                    import dbus
                    with patch("dbus.Interface", return_value=obex_iface):
                        result = client.connect("AA:BB:CC:DD:EE:FF")

        assert result is True
        assert len(attempt_params) == 2
        assert "Channel" in attempt_params[0]
        assert "Channel" not in attempt_params[1]
