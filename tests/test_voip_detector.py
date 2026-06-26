"""Tests for voip/detector.py — VoIP call detector."""
import inspect


class TestProcessScan:
    def test_proc_comm_uses_context_manager(self):
        """open() must be used as a with-statement to avoid fd exhaustion."""
        from voip.detector import VoIPDetector

        src = inspect.getsource(VoIPDetector._check_processes_linux)
        assert "with open(" in src, (
            "_check_processes_linux must use 'with open()' to avoid fd leaks"
        )
