"""Tests for PBAPClient._ensure_obexd — multi-path and multi-service discovery."""
import subprocess
from unittest.mock import MagicMock, call, patch

import pytest

from bluetooth.pbap_client import PBAPClient


def _dbus_ok():
    """Simulate obexd already running (D-Bus object reachable)."""
    return MagicMock()


def _dbus_fail():
    """Simulate obexd not running (D-Bus raises)."""
    raise Exception("org.freedesktop.DBus.Error.ServiceUnknown")


class TestEnsureObexd:
    def test_returns_true_when_already_running(self):
        client = PBAPClient()
        with patch.object(client, "_get_session_bus") as mock_bus:
            mock_bus.return_value.get_object.return_value = MagicMock()
            assert client._ensure_obexd() is True

    def test_tries_systemctl_obex_first(self):
        client = PBAPClient()
        calls = []

        def fake_bus():
            if not calls:
                raise Exception("not running")
            return MagicMock()

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            r = MagicMock()
            r.returncode = 0
            return r

        with patch.object(client, "_get_session_bus", side_effect=fake_bus):
            with patch("subprocess.run", side_effect=fake_run):
                with patch("time.sleep"):
                    result = client._ensure_obexd()

        assert result is True
        assert any("obex" in " ".join(c) for c in calls)

    def test_falls_back_to_direct_launch_on_systemctl_failure(self):
        client = PBAPClient()
        bus_calls = [0]

        def fake_bus():
            bus_calls[0] += 1
            if bus_calls[0] <= 3:   # fails for all systemctl attempts
                raise Exception("not running")
            return MagicMock()

        def fake_run(cmd, **kwargs):
            r = MagicMock()
            r.returncode = 1  # systemctl fails
            return r

        launched = []

        def fake_popen(cmd, **kwargs):
            launched.append(cmd[0])
            return MagicMock()

        with patch.object(client, "_get_session_bus", side_effect=fake_bus):
            with patch("subprocess.run", side_effect=fake_run):
                with patch("subprocess.Popen", side_effect=fake_popen):
                    with patch("time.sleep"):
                        client._ensure_obexd()

        assert len(launched) > 0

    def test_obexd_paths_cover_major_distros(self):
        paths = PBAPClient._OBEXD_PATHS
        # Must cover: in-PATH (Ubuntu), /usr/lib/bluetooth (Arch/Fedora),
        # /usr/libexec/bluetooth (RHEL-based)
        assert any(p == "obexd" for p in paths), "missing bare 'obexd' for Ubuntu"
        assert any("usr/lib/bluetooth" in p for p in paths), "missing Arch/Fedora path"
        assert any("libexec" in p for p in paths), "missing RHEL/CentOS path"

    def test_returns_false_when_all_attempts_fail(self):
        client = PBAPClient()

        def fake_bus():
            raise Exception("not running")

        def fail_run(cmd, **kwargs):
            r = MagicMock()
            r.returncode = 1
            return r

        def fail_popen(cmd, **kwargs):
            raise FileNotFoundError

        with patch.object(client, "_get_session_bus", side_effect=fake_bus):
            with patch("subprocess.run", side_effect=fail_run):
                with patch("subprocess.Popen", side_effect=fail_popen):
                    with patch("time.sleep"):
                        result = client._ensure_obexd()

        assert result is False
