"""
Windows HFP backend stub.

A full Windows implementation would use winrt (Windows Runtime Python bindings)
with Windows.Devices.Bluetooth.Rfcomm — this is a future milestone.

On Windows, the UI, SQLite contacts, and VoIP detection all work.
Only the Bluetooth HFP profile is not available.
"""
import logging

logger = logging.getLogger(__name__)


class WindowsHfpBackend:
    def start(self):
        logger.warning(
            "HFP Bluetooth hands-free is not available on Windows in this version.\n"
            "The contacts manager and VoIP detection will still work.\n"
            "For Windows HFP support, a WinRT-based backend is required."
        )

    def stop(self):
        pass

    def answer(self, *_):
        raise NotImplementedError("HFP not supported on Windows")

    def reject(self, *_):
        raise NotImplementedError("HFP not supported on Windows")

    def dial(self, *_):
        raise NotImplementedError("HFP not supported on Windows")

    def get_paired_devices(self):
        return []

    def get_device_alias(self, *_):
        return "Unknown"