"""
VoIP call detector — detects active Teams, Zoom, Meet, Slack, Discord, etc. calls.

Uses three complementary strategies:
  A) Process name scan (/proc/*/comm on Linux, psutil on Windows)
  B) PulseAudio source-output monitoring (pactl list source-outputs)
  C) PipeWire media.role=Communication check (pw-dump JSON)

Runs in a background thread, emits Qt signals when state changes.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# VoIP process names to look for (lowercase)
DEFAULT_VOIP_PROCESSES = {
    "zoom", "zoom.exe",
    "ms-teams", "teams", "teams.exe",
    "slack", "slack.exe",
    "discord", "discord.exe",
    "webex", "webex.exe",
    "skype", "skype.exe",
    "signal",
    "telegram-desktop",
}

# Browser processes that may host WebRTC calls (Meet, Teams web, etc.)
BROWSER_PROCESSES = {"chrome", "chromium", "firefox", "brave", "opera", "edge"}


class VoIPDetector:
    """
    Polls every `poll_interval` seconds.
    Calls `on_started()` when a VoIP call is detected, `on_ended()` when it ends.
    Uses a debounce of 3 consecutive positive/negative polls to avoid flapping.
    """

    DEBOUNCE_COUNT = 3  # Consecutive polls before state change is confirmed

    def __init__(
        self,
        poll_interval: float = 2.0,
        process_names: Optional[set[str]] = None,
        on_started: Optional[Callable] = None,
        on_ended: Optional[Callable] = None,
    ):
        self._interval = poll_interval
        self._process_names = (
            {p.lower() for p in process_names} if process_names
            else DEFAULT_VOIP_PROCESSES
        )
        self._on_started = on_started
        self._on_ended = on_ended

        self._active = False
        self._positive_streak = 0
        self._negative_streak = 0

        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="VoIPDetector", daemon=True
        )

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def is_active(self) -> bool:
        return self._active

    # ── Poll loop ─────────────────────────────────────────────────────────────

    def _run(self):
        while not self._stop.is_set():
            detected = self._check_all()

            if detected:
                self._negative_streak = 0
                self._positive_streak += 1
                if not self._active and self._positive_streak >= self.DEBOUNCE_COUNT:
                    self._active = True
                    logger.info("VoIP call detected")
                    if self._on_started:
                        try:
                            self._on_started()
                        except Exception:
                            pass
            else:
                self._positive_streak = 0
                self._negative_streak += 1
                if self._active and self._negative_streak >= self.DEBOUNCE_COUNT:
                    self._active = False
                    logger.info("VoIP call ended")
                    if self._on_ended:
                        try:
                            self._on_ended()
                        except Exception:
                            pass

            self._stop.wait(self._interval)

    def _check_all(self) -> bool:
        """Return True if any strategy detects an active VoIP call."""
        return (
            self._check_processes()
            or self._check_pa_source_outputs()
            or self._check_pipewire_communication()
        )

    # ── Strategy A: Process names ─────────────────────────────────────────────

    def _check_processes(self) -> bool:
        if platform.system() == "Linux":
            return self._check_processes_linux()
        else:
            return self._check_processes_psutil()

    def _check_processes_linux(self) -> bool:
        try:
            for entry in os.scandir("/proc"):
                if not entry.name.isdigit():
                    continue
                try:
                    with open(f"/proc/{entry.name}/comm") as fh:
                        comm = fh.read().strip().lower()
                    if comm in self._process_names:
                        return True
                except (PermissionError, FileNotFoundError, OSError):
                    pass
        except Exception as e:
            logger.debug("Process scan error: %s", e)
        return False

    def _check_processes_psutil(self) -> bool:
        try:
            import psutil
            for proc in psutil.process_iter(["name"]):
                try:
                    name = (proc.info.get("name") or "").lower()
                    if name in self._process_names:
                        return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception as e:
            logger.debug("psutil scan error: %s", e)
        return False

    # ── Strategy B: PulseAudio source-output monitoring ───────────────────────

    def _check_pa_source_outputs(self) -> bool:
        """
        Check if any VoIP app is recording from a microphone (not a monitor).
        Uses pactl list source-outputs short format.
        """
        try:
            out = subprocess.check_output(
                ["pactl", "list", "source-outputs"],
                text=True, timeout=3,
                stderr=subprocess.DEVNULL,
            )
            # Parse application.name and source name from the output
            app_name = ""
            source_name = ""
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("application.name"):
                    app_name = line.split("=", 1)[-1].strip().strip('"').lower()
                elif line.startswith("Source:") or line.startswith("source.name"):
                    source_name = line.split(":", 1)[-1].strip().lower()
                elif line == "" and app_name:
                    # End of a source-output block
                    if (
                        any(vp in app_name for vp in self._process_names | BROWSER_PROCESSES)
                        and "monitor" not in source_name
                    ):
                        return True
                    app_name = ""
                    source_name = ""
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        except Exception as e:
            logger.debug("pactl source-output check error: %s", e)
        return False

    # ── Strategy C: PipeWire media.role=Communication ─────────────────────────

    def _check_pipewire_communication(self) -> bool:
        """
        pw-dump returns a JSON array of all PipeWire objects.
        Look for Node objects with media.role == "Communication" and state "running".
        This catches Chrome/Firefox WebRTC calls (Meet, Teams web, etc.).
        """
        try:
            out = subprocess.check_output(
                ["pw-dump"],
                text=True, timeout=5,
                stderr=subprocess.DEVNULL,
            )
            nodes = json.loads(out)
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                if node.get("type") != "PipeWire:Interface:Node":
                    continue
                props = node.get("info", {}).get("props", {})
                role = props.get("media.role", "")
                state = node.get("info", {}).get("state", "")
                if role == "Communication" and state == "running":
                    return True
        except (FileNotFoundError, json.JSONDecodeError, subprocess.TimeoutExpired):
            pass
        except Exception as e:
            logger.debug("pw-dump check error: %s", e)
        return False