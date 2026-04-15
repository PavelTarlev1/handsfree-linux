"""Tests for audio/recorder.py"""
import struct
import wave
from pathlib import Path

import pytest

from audio.recorder import CallRecorder, _safe


class TestSafeFilename:
    def test_alphanumeric_unchanged(self):
        assert _safe("John123") == "John123"

    def test_spaces_preserved(self):
        assert " " in _safe("John Doe")

    def test_special_chars_replaced(self):
        result = _safe("John/Doe:Test")
        assert "/" not in result
        assert ":" not in result

    def test_plus_preserved(self):
        assert "+" in _safe("+972501234567")

    def test_truncated_to_40(self):
        long_name = "A" * 100
        assert len(_safe(long_name)) <= 40

    def test_empty_string(self):
        assert _safe("") == ""


class TestCallRecorder:
    def test_start_creates_wav(self, tmp_path):
        rec = CallRecorder(str(tmp_path))
        rec.start("Alice", "+1234567890")
        assert rec._running is True
        assert rec._wav is not None
        rec.stop()
        # At least one .wav file should exist
        files = list(tmp_path.glob("*.wav"))
        assert len(files) == 1

    def test_stop_closes_wav(self, tmp_path):
        rec = CallRecorder(str(tmp_path))
        rec.start("Bob", "050")
        rec.stop()
        assert rec._running is False
        assert rec._wav is None

    def test_write_rx_and_tx_interleaved(self, tmp_path):
        rec = CallRecorder(str(tmp_path))
        rec.start("Test", "0")
        # Two s16le samples each
        rx = struct.pack("<hh", 100, 200)   # 4 bytes = 2 mono samples
        tx = struct.pack("<hh", 300, 400)
        rec.write_rx(rx)
        rec.write_tx(tx)
        rec.stop()

        wav_file = list(tmp_path.glob("*.wav"))[0]
        with wave.open(str(wav_file), "rb") as w:
            assert w.getnchannels() == 2
            assert w.getsampwidth() == 2
            assert w.getframerate() == 8000
            frames = w.readframes(w.getnframes())

        # Expected interleaving: L(rx[0]), R(tx[0]), L(rx[1]), R(tx[1])
        samples = struct.unpack("<" + "h" * (len(frames) // 2), frames)
        # First pair: rx=100, tx=300
        assert samples[0] == 100
        assert samples[1] == 300
        # Second pair: rx=200, tx=400
        assert samples[2] == 200
        assert samples[3] == 400

    def test_write_before_start_ignored(self, tmp_path):
        rec = CallRecorder(str(tmp_path))
        # Should not raise even when not started
        rec.write_rx(b"\x00\x00")
        rec.write_tx(b"\x00\x00")
        assert list(tmp_path.glob("*.wav")) == []

    def test_mismatched_buffer_padded_on_stop(self, tmp_path):
        rec = CallRecorder(str(tmp_path))
        rec.start("Pad", "0")
        # Only write rx — tx is silent
        rx = struct.pack("<hhhh", 1, 2, 3, 4)
        rec.write_rx(rx)
        rec.stop()

        wav_file = list(tmp_path.glob("*.wav"))[0]
        with wave.open(str(wav_file), "rb") as w:
            n = w.getnframes()
        assert n == 4  # 4 stereo frames

    def test_stop_idempotent(self, tmp_path):
        rec = CallRecorder(str(tmp_path))
        rec.start("Idempotent", "0")
        rec.stop()
        rec.stop()  # Should not raise
        assert rec._running is False

    def test_directory_created_automatically(self, tmp_path):
        subdir = tmp_path / "deep" / "nested"
        rec = CallRecorder(str(subdir))
        rec.start("Dir", "0")
        rec.stop()
        assert subdir.exists()
