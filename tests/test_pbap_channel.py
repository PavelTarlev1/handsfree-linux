"""Tests for _find_pbap_channel — SDP channel lookup via sdptool."""
from unittest.mock import MagicMock, patch

import pytest

from bluetooth.pbap_client import PBAPClient


def _make_result(stdout: str):
    r = MagicMock()
    r.stdout = stdout
    r.returncode = 0
    return r


IPHONE_SDPTOOL = """\
Browsing A4:C3:37:CC:5C:22 ...
Service Name: Phonebook
Service Class: "Phonebook Access - PSE" (0x112f)
Protocol Descriptor List:
  "L2CAP" (0x0100)
  "RFCOMM" (0x0003)
    Channel: 13
Profile Descriptor List:
  "Phone Book Access" (0x1130) Version: 0x0102
"""

ANDROID_SDPTOOL = """\
Browsing 00:11:22:33:44:55 ...
Service Name: PBAP PSE
Service Class: "Phonebook Access - PSE" (0x112f)
Protocol Descriptor List:
  "L2CAP" (0x0100)
  "RFCOMM" (0x0003)
    Channel: 19
"""

MULTI_SERVICE_SDPTOOL = """\
Browsing AA:BB:CC:DD:EE:FF ...
Service Name: Headset
Service Class: "Headset" (0x1108)
  Channel: 1

Service Name: Phonebook
Service Class: "Phonebook Access - PSE" (0x112f)
  Channel: 13

Service Name: Audio
Service Class: "Audio Source" (0x110a)
  Channel: 5
"""

NO_PBAP_SDPTOOL = """\
Browsing AA:BB:CC:DD:EE:FF ...
Service Name: Headset
Service Class: "Headset" (0x1108)
  Channel: 1
"""


class TestFindPbapChannel:
    def _client(self):
        return PBAPClient()

    def test_iphone_channel_13(self):
        with patch("subprocess.run", return_value=_make_result(IPHONE_SDPTOOL)):
            ch = self._client()._find_pbap_channel("A4:C3:37:CC:5C:22")
        assert ch == 13

    def test_android_channel_19(self):
        with patch("subprocess.run", return_value=_make_result(ANDROID_SDPTOOL)):
            ch = self._client()._find_pbap_channel("00:11:22:33:44:55")
        assert ch == 19

    def test_picks_pbap_channel_from_multi_service_output(self):
        with patch("subprocess.run", return_value=_make_result(MULTI_SERVICE_SDPTOOL)):
            ch = self._client()._find_pbap_channel("AA:BB:CC:DD:EE:FF")
        assert ch == 13

    def test_returns_none_when_no_pbap_service(self):
        with patch("subprocess.run", return_value=_make_result(NO_PBAP_SDPTOOL)):
            ch = self._client()._find_pbap_channel("AA:BB:CC:DD:EE:FF")
        assert ch is None

    def test_returns_none_on_sdptool_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            ch = self._client()._find_pbap_channel("AA:BB:CC:DD:EE:FF")
        assert ch is None

    def test_returns_none_on_timeout(self):
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("sdptool", 10)):
            ch = self._client()._find_pbap_channel("AA:BB:CC:DD:EE:FF")
        assert ch is None

    def test_returns_none_on_empty_output(self):
        with patch("subprocess.run", return_value=_make_result("")):
            ch = self._client()._find_pbap_channel("AA:BB:CC:DD:EE:FF")
        assert ch is None

    def test_case_insensitive_service_class_match(self):
        output = IPHONE_SDPTOOL.replace("0x112f", "0x112F")
        with patch("subprocess.run", return_value=_make_result(output)):
            ch = self._client()._find_pbap_channel("A4:C3:37:CC:5C:22")
        assert ch == 13
