"""Tests for audio/ringer.py — call ringtone player."""
import threading
from unittest.mock import MagicMock


class TestRingerStop:
    def test_stop_joins_thread_before_clearing(self):
        """stop() must join the thread so the old ring loop is fully dead."""
        from audio.ringer import Ringer

        ringer = Ringer()
        mock_thread = MagicMock(spec=threading.Thread)
        ringer._thread = mock_thread

        ringer.stop()

        mock_thread.join.assert_called_once_with(timeout=2)
        assert ringer._thread is None

    def test_stop_sets_event_before_join(self):
        """stop() must set the stop event first so the thread can actually exit."""
        from audio.ringer import Ringer

        order = []
        ringer = Ringer()
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.join.side_effect = lambda timeout: order.append("join")
        ringer._thread = mock_thread
        ringer._stop_event = MagicMock()
        ringer._stop_event.set.side_effect = lambda: order.append("set")

        ringer.stop()

        assert order == ["set", "join"]
