"""Remote update checker and in-place updater for HandsFree (Linux only).

Update flow:
  1. Check GitHub API for latest release tag
  2. Compare with local VERSION
  3. If newer: show "Update" button
  4. On click: git pull origin main in a background shell, then restart
"""

import os
import re
import sys
import json
import socket
import tempfile
import subprocess
import urllib.request
import urllib.error

from PyQt6.QtCore import QThread, pyqtSignal

from core.version import __version__

_OWNER = "PavelTarlev1"
_REPO  = "handsfree-linux"
_API   = f"https://api.github.com/repos/{_OWNER}/{_REPO}/releases/latest"
_API_TIMEOUT_S = 10


def _parse_version(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", v.lstrip("v"))[:3])


def _api_headers() -> dict:
    return {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": f"HandsFree/{__version__}",
    }


def _repo_root() -> str:
    """Absolute path to the directory that contains main.py."""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


# ── Version check ─────────────────────────────────────────────────────────────

def check_for_update() -> dict | None:
    """Return release info dict if a newer version exists on GitHub, else None.
    Raises on network failure.
    """
    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(_API_TIMEOUT_S)
    try:
        req = urllib.request.Request(_API, headers=_api_headers())
        with urllib.request.urlopen(req, timeout=_API_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode())
    finally:
        socket.setdefaulttimeout(old_timeout)

    tag    = data.get("tag_name", "")
    remote = _parse_version(tag)
    local  = _parse_version(__version__)

    if remote <= local:
        return None

    return {
        "tag":      tag,
        "notes":    data.get("body", "").strip(),
        "html_url": data.get("html_url", ""),
    }


# ── Apply update via git pull ─────────────────────────────────────────────────

def apply_update_linux():
    """
    Pull latest code via git and restart the app.

    A small shell script does the git pull after this process exits so
    we're not pulling while files we depend on are loaded.
    """
    root = _repo_root()

    fd, script = tempfile.mkstemp(suffix=".sh", prefix="handsfree_upd_")
    with os.fdopen(fd, "w") as f:
        f.write("#!/usr/bin/env bash\n")
        f.write("sleep 1\n")
        f.write(f'git -C "{root}" pull origin main\n')
        f.write(f'rm -- "$0"\n')
        f.write(f'python3 "{root}/main.py" &\n')

    os.chmod(script, 0o755)
    subprocess.Popen(
        ["bash", script],
        close_fds=True,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    sys.exit(0)


# ── QThread workers ───────────────────────────────────────────────────────────

class UpdateChecker(QThread):
    """Non-blocking update check — create, connect signals, call start()."""
    update_available = pyqtSignal(dict)
    up_to_date       = pyqtSignal()
    check_failed     = pyqtSignal(str)

    def run(self):
        try:
            info = check_for_update()
            if info:
                self.update_available.emit(info)
            else:
                self.up_to_date.emit()
        except Exception as e:
            self.check_failed.emit(str(e))


class UpdateApplier(QThread):
    """Runs git pull in a thread so the UI can show progress."""
    finished = pyqtSignal()
    failed   = pyqtSignal(str)

    def run(self):
        try:
            root = _repo_root()
            result = subprocess.run(
                ["git", "-C", root, "pull", "origin", "main"],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                self.failed.emit(result.stderr.strip() or "git pull failed")
                return
            self.finished.emit()
        except Exception as e:
            self.failed.emit(str(e))
