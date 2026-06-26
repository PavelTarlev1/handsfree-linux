"""Tests for mSBC codec — encode/decode, H2 header, packet format."""
import ctypes
from unittest.mock import MagicMock, patch
import pytest


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_lib(decode_output: bytes = b"\x00" * 240, encode_output: bytes = b"\x00" * 57):
    """Return a fake libsbc that returns fixed decode/encode output."""
    lib = MagicMock()
    lib.sbc_init.return_value = 0
    lib.sbc_finish.return_value = None

    def fake_decode(buf, src, src_len, dst, dst_len, written_ptr):
        pcm = decode_output[:dst_len]
        ctypes.memmove(dst, pcm, len(pcm))
        written_ptr._obj.value = len(pcm)  # type: ignore[attr-defined]
        return src_len

    def fake_encode(buf, src, src_len, dst, dst_len, written_ptr):
        frame = encode_output[:dst_len]
        ctypes.memmove(dst, frame, len(frame))
        written_ptr._obj.value = len(frame)  # type: ignore[attr-defined]
        return src_len

    lib.sbc_decode.side_effect = fake_decode
    lib.sbc_encode.side_effect = fake_encode
    return lib


def _codec_with(lib):
    """Instantiate MSBCCodec with a fake lib injected."""
    import audio.msbc as m
    with patch.object(m, "_LIB", lib), patch.object(m, "AVAILABLE", True):
        from audio.msbc import MSBCCodec
        codec = MSBCCodec.__new__(MSBCCodec)
        codec._lib = lib
        codec._buf = ctypes.create_string_buffer(512)
        codec._sn = 0
        lib.sbc_init(codec._buf, 0x04)
        return codec


# ── decode tests ──────────────────────────────────────────────────────────────

class TestMSBCDecode:
    def test_valid_packet_returns_pcm(self):
        from audio import msbc
        lib = _make_lib(decode_output=b"\x01\x02" * 120)
        codec = _codec_with(lib)
        packet = bytes([0x01, 0x08]) + b"\xAB" * 57 + b"\x00"
        result = codec.decode(packet)
        assert result is not None
        assert len(result) == 240

    def test_non_h2_packet_falls_back_to_raw_sbc(self):
        """Packets without H2 sync byte are tried as raw SBC frames (adapter compat)."""
        from audio import msbc
        lib = _make_lib(decode_output=b"\x01\x02" * 120)
        codec = _codec_with(lib)
        # 60-byte packet without H2 header (byte0 != 0x01) — should still decode
        packet = bytes([0xFF, 0x08]) + b"\x00" * 57 + b"\x00"
        result = codec.decode(packet)
        assert result is not None  # fallback to raw SBC succeeded

    def test_short_packet_returns_none(self):
        from audio import msbc
        lib = _make_lib()
        codec = _codec_with(lib)
        assert codec.decode(b"\x01\x08\x00") is None  # too short

    def test_sbc_decode_error_returns_none(self):
        lib = MagicMock()
        lib.sbc_init.return_value = 0

        def bad_decode(*a, **kw):
            return -1

        lib.sbc_decode.side_effect = bad_decode
        codec = _codec_with(lib)
        packet = bytes([0x01, 0x08]) + b"\x00" * 57 + b"\x00"
        assert codec.decode(packet) is None


# ── encode tests ──────────────────────────────────────────────────────────────

class TestMSBCEncode:
    def test_packet_is_60_bytes(self):
        lib = _make_lib(encode_output=b"\xAB" * 57)
        codec = _codec_with(lib)
        pcm = b"\x01\x02" * 120  # 240 bytes
        pkt = codec.encode(pcm)
        assert len(pkt) == 60

    def test_packet_starts_with_h2_sync(self):
        lib = _make_lib(encode_output=b"\x00" * 57)
        codec = _codec_with(lib)
        pkt = codec.encode(b"\x00" * 240)
        assert pkt[0] == 0x01

    def test_h2_sequence_cycles(self):
        from audio.msbc import _H2_SN
        lib = _make_lib(encode_output=b"\x00" * 57)
        codec = _codec_with(lib)
        sns = [codec.encode(b"\x00" * 240)[1] for _ in range(8)]
        # Should cycle through 4 values twice
        assert sns[:4] == list(_H2_SN)
        assert sns[4:8] == list(_H2_SN)

    def test_short_pcm_is_zero_padded(self):
        lib = _make_lib(encode_output=b"\x00" * 57)
        codec = _codec_with(lib)
        # Should not raise even with short input
        pkt = codec.encode(b"\x01" * 100)
        assert len(pkt) == 60

    def test_encode_error_raises(self):
        lib = MagicMock()
        lib.sbc_init.return_value = 0

        def bad_encode(*a, **kw):
            return -1

        lib.sbc_encode.side_effect = bad_encode
        codec = _codec_with(lib)
        with pytest.raises(RuntimeError, match="sbc_encode error"):
            codec.encode(b"\x00" * 240)


# ── availability tests ────────────────────────────────────────────────────────

class TestMSBCAvailability:
    def test_msbc_codec_raises_when_libsbc_missing(self):
        import audio.msbc as m
        with patch.object(m, "_LIB", None), patch.object(m, "AVAILABLE", False):
            from audio.msbc import MSBCCodec
            with pytest.raises(RuntimeError, match="libsbc not found"):
                MSBCCodec()

    def test_at_handler_bac_advertises_both_with_msbc(self):
        from bluetooth.at_handler import cmd_bac, CODEC_CVSD, CODEC_MSBC
        from unittest.mock import patch
        with patch("core.config.load_pref", return_value=False):
            result = cmd_bac("msbc")
        assert str(CODEC_CVSD) in result
        assert str(CODEC_MSBC) in result

    def test_at_handler_bac_cvsd_only(self):
        from bluetooth.at_handler import cmd_bac, CODEC_MSBC
        result = cmd_bac("cvsd")
        assert str(CODEC_MSBC) not in result

    def test_at_handler_brsf_has_codec_bit_for_msbc(self):
        from bluetooth.at_handler import cmd_brsf
        msbc_features = int(cmd_brsf("msbc").split("=")[1])
        cvsd_features = int(cmd_brsf("cvsd").split("=")[1])
        assert msbc_features & (1 << 7), "codec negotiation bit not set for msbc"
        assert not (cvsd_features & (1 << 7)), "codec negotiation bit set for cvsd"
