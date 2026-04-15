"""
Ringer — plays a looping sound for incoming or outgoing calls.

Uses paplay (PulseAudio/PipeWire) to play freedesktop standard sounds.
"""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_SOUNDS_DIR = Path("/usr/share/sounds/freedesktop/stereo")
INCOMING = _SOUNDS_DIR / "phone-incoming-call.oga"
OUTGOING = _SOUNDS_DIR / "phone-outgoing-calling.oga"

_INTERVAL = 3.0   # seconds between repeats


class Ringer:
    """
    Start/stop a looping ringtone.

    Usage:
        ringer = Ringer()
        ringer.start(Ringer.INCOMING)   # on incoming call
        ringer.start(Ringer.OUTGOING)   # on outgoing call (ringback)
        ringer.stop()                   # on answer, reject, or call end
    """

    INCOMING = INCOMING
    OUTGOING = OUTGOING

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._sound: Path = INCOMING
        self._volume: int = 80   # 0-100 %

    def set_volume(self, pct: int):
        """Set ring volume (0-100). Takes effect on the next ring loop iteration."""
        self._volume = max(0, min(100, pct))

    def start(self, sound: Path | None = None):
        self.stop()   # ensure clean state
        self._sound = sound if sound is not None else INCOMING
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="ringer")
        self._thread.start()
        logger.debug("Ringer started: %s", self._sound.name)

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
            if self._sound.exists():
                # paplay volume: 0-65536, where 65536 = 100 %
                pa_vol = int(self._volume * 655.36)
                proc = subprocess.Popen(
                    ["paplay", f"--volume={pa_vol}", str(self._sound)],
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
                logger.debug("Ringtone file not found: %s", self._sound)
        except Exception as e:
            logger.debug("Ringer play error: %s", e)
