"""Tests for bluetooth/at_handler.py"""
import pytest
from bluetooth.at_handler import (
    parse_line,
    cmd_brsf, cmd_bac, cmd_cind_test, cmd_cind_read, cmd_cmer,
    cmd_chld_test, cmd_clip_enable, cmd_ccwa_enable,
    cmd_answer, cmd_reject, cmd_hangup, cmd_dial,
    cmd_bcs_confirm, cmd_volume_speaker, cmd_volume_mic,
    cmd_clcc, cmd_dtmf, cmd_redial, cmd_bcc,
    HF_FEATURES,
)


class TestParseLine:
    def test_empty_line(self):
        ev = parse_line("")
        assert ev.kind == "empty"

    def test_whitespace_only(self):
        ev = parse_line("   ")
        assert ev.kind == "empty"

    def test_ring(self):
        ev = parse_line("RING")
        assert ev.kind == "ring"
        assert ev.params == {}

    def test_ok(self):
        ev = parse_line("OK")
        assert ev.kind == "ok"

    def test_error(self):
        ev = parse_line("ERROR")
        assert ev.kind == "error"

    def test_no_carrier(self):
        ev = parse_line("NO CARRIER")
        assert ev.kind == "no_carrier"

    def test_brsf(self):
        ev = parse_line("+BRSF: 1039")
        assert ev.kind == "brsf"
        assert ev.params["features"] == 1039

    def test_cind_format(self):
        ev = parse_line('+CIND: ("call",(0,1)),("callsetup",(0-3))')
        assert ev.kind == "cind_format"

    def test_cind_values(self):
        ev = parse_line("+CIND: 0,0,1,4,1,0,0")
        assert ev.kind == "cind_values"
        assert ev.params["values"] == [0, 0, 1, 4, 1, 0, 0]

    def test_ciev(self):
        ev = parse_line("+CIEV: 3,1")
        assert ev.kind == "ciev"
        assert ev.params["index"] == 3
        assert ev.params["value"] == 1

    def test_clip(self):
        ev = parse_line('+CLIP: "+972501234567",145')
        assert ev.kind == "clip"
        assert ev.params["number"] == "+972501234567"
        assert ev.params["type"] == 145

    def test_clip_empty_number(self):
        ev = parse_line('+CLIP: "",128')
        assert ev.kind == "clip"
        assert ev.params["number"] == ""

    def test_bcs(self):
        ev = parse_line("+BCS: 1")
        assert ev.kind == "bcs"
        assert ev.params["codec"] == 1

    def test_bcs_msbc(self):
        ev = parse_line("+BCS: 2")
        assert ev.params["codec"] == 2

    def test_vgm(self):
        ev = parse_line("+VGM: 8")
        assert ev.kind == "vgm"
        assert ev.params["gain"] == 8

    def test_vgs(self):
        ev = parse_line("+VGS: 15")
        assert ev.kind == "vgs"
        assert ev.params["gain"] == 15

    def test_chld(self):
        ev = parse_line("+CHLD: (0,1,2)")
        assert ev.kind == "chld"
        assert "0" in ev.params["modes"]
        assert "1" in ev.params["modes"]

    def test_ccwa(self):
        ev = parse_line('+CCWA: "0501234567",129')
        assert ev.kind == "ccwa"
        assert ev.params["number"] == "0501234567"
        assert ev.params["type"] == 129

    def test_bvra_active(self):
        ev = parse_line("+BVRA: 1")
        assert ev.kind == "bvra"
        assert ev.params["active"] is True

    def test_bvra_inactive(self):
        ev = parse_line("+BVRA: 0")
        assert ev.params["active"] is False

    def test_cme_error(self):
        ev = parse_line("+CME ERROR: 10")
        assert ev.kind == "cme_error"
        assert ev.params["code"] == 10

    def test_unknown_line(self):
        ev = parse_line("AT+GARBAGE=1")
        assert ev.kind == "unknown"
        assert ev.raw == "AT+GARBAGE=1"

    def test_strips_trailing_whitespace(self):
        ev = parse_line("OK   ")
        assert ev.kind == "ok"

    def test_clcc_raw(self):
        ev = parse_line("+CLCC: 1,0,0,0,0")
        assert ev.kind == "clcc"


class TestCommandBuilders:
    def test_cmd_brsf_cvsd_matches_base_features(self):
        from bluetooth.at_handler import _HF_FEATURES_BASE
        cmd = cmd_brsf("cvsd")
        assert cmd == f"AT+BRSF={_HF_FEATURES_BASE}"

    def test_cmd_brsf_msbc_adds_codec_bit(self):
        msbc_val = int(cmd_brsf("msbc").split("=")[1])
        assert msbc_val & (1 << 7)

    def test_cmd_bac_msbc_advertises_both(self):
        assert cmd_bac("msbc") == "AT+BAC=1,2"

    def test_cmd_bac_cvsd_advertises_cvsd_only(self):
        assert cmd_bac("cvsd") == "AT+BAC=1"

    def test_cmd_cind_test(self):
        assert cmd_cind_test() == "AT+CIND=?"

    def test_cmd_cind_read(self):
        assert cmd_cind_read() == "AT+CIND?"

    def test_cmd_cmer(self):
        assert cmd_cmer() == "AT+CMER=3,0,0,1"

    def test_cmd_chld_test(self):
        assert cmd_chld_test() == "AT+CHLD=?"

    def test_cmd_clip_enable(self):
        assert cmd_clip_enable() == "AT+CLIP=1"

    def test_cmd_ccwa_enable(self):
        assert cmd_ccwa_enable() == "AT+CCWA=1"

    def test_cmd_answer(self):
        assert cmd_answer() == "ATA"

    def test_cmd_reject(self):
        assert cmd_reject() == "AT+CHUP"

    def test_cmd_hangup(self):
        assert cmd_hangup() == "AT+CHUP"

    def test_cmd_dial_plain(self):
        assert cmd_dial("0501234567") == "ATD0501234567;"

    def test_cmd_dial_strips_spaces(self):
        assert cmd_dial("+1 800 555 1234") == "ATD+18005551234;"

    def test_cmd_dial_strips_dashes(self):
        assert cmd_dial("050-123-4567") == "ATD0501234567;"

    def test_cmd_dial_keeps_hash_star(self):
        result = cmd_dial("*123#")
        assert result == "ATD*123#;"

    def test_cmd_bcs_confirm(self):
        assert cmd_bcs_confirm(1) == "AT+BCS=1"
        assert cmd_bcs_confirm(2) == "AT+BCS=2"

    def test_cmd_volume_speaker_clamps_high(self):
        assert cmd_volume_speaker(20) == "AT+VGS=15"

    def test_cmd_volume_speaker_clamps_low(self):
        assert cmd_volume_speaker(-5) == "AT+VGS=0"

    def test_cmd_volume_speaker_normal(self):
        assert cmd_volume_speaker(8) == "AT+VGS=8"

    def test_cmd_volume_mic_clamps(self):
        assert cmd_volume_mic(100) == "AT+VGM=15"
        assert cmd_volume_mic(-1) == "AT+VGM=0"

    def test_cmd_clcc(self):
        assert cmd_clcc() == "AT+CLCC"

    def test_cmd_dtmf(self):
        assert cmd_dtmf("5") == "AT+VTS=5"
        assert cmd_dtmf("#") == "AT+VTS=#"

    def test_cmd_redial(self):
        assert cmd_redial() == "AT+BLDN"

    def test_cmd_bcc(self):
        assert cmd_bcc() == "AT+BCC"
