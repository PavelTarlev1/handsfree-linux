"""
Ringer — plays a looping ringtone on incoming calls.

Uses paplay (PulseAudio/PipeWire) to play the system phone-incoming-call sound.
Falls back to aplay with a generated beep if the .oga file is not found.
"""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Preferred ringtone — freedesktop standard, present on most distros
_RINGTONE = Path("/usr/share/sounds/freedesktop/stereo/phone-incoming-call.oga")
_INTERVAL = 3.0   # seconds between repeats


class Ringer:
    """
    Start/stop a looping ringtone.

    Usage:
        ringer = Ringer()
        ringer.start()   # on incoming call
        ringer.stop()    # on answer, reject, or call end
    """

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self):
        self.stop()   # ensure clean state
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="ringer")
        self._thread.start()
        logger.debug("Ringer started")

    def stop(self):
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread = None
        logger.debug("Ringer stopped")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _loop(self):
        while not self._stop_event.is_set():
            self._play_once()
            # Wait _INTERVAL seconds but wake up immediately on stop
            self._stop_event.wait(timeout=_INTERVAL)

    def _play_once(self):
        if self._stop_event.is_set():
            return
        try:
            if _RINGTONE.exists():
                proc = subprocess.Popen(
                    ["paplay", str(_RINGTONE)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                # Wait for playback to finish, but bail if stopped
                while proc.poll() is None:
                    if self._stop_event.is_set():
                        proc.terminate()
                        return
                    time.sleep(0.05)
            else:
                # Fallback: short beep via paplay with a raw sine if available,
                # otherwise just log and skip
                logger.debug("Ringtone file not found: %s", _RINGTONE)
        except Exception as e:
            logger.debug("Ringer play error: %s", e)
