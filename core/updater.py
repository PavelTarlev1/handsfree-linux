"""Remote update checker and in-place updater for HandsFree (Linux only)."""

import os
import re
import sys
import json
import shutil
import tarfile
import tempfile
import subprocess
import urllib.request
import urllib.error

from PyQt6.QtCore import QThread, pyqtSignal

from core.version import __version__

_OWNER      = "PavelTarlev1"
_REPO       = "HandsFree-Linux"
_API        = f"https://api.github.com/repos/{_OWNER}/{_REPO}/releases/latest"
_ASSET_NAME           = "handsfree-linux.tar.gz"   # name of the asset attached to each release
_API_CHECK_TIMEOUT_S  = 10    # seconds before giving up on the version-check request
_DOWNLOAD_TIMEOUT_S   = 120   # seconds before giving up on the asset download


# ── helpers ─────────────────────────────────────────────────────────────────

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


# ── network functions (run inside QThread workers) ───────────────────────────

def check_for_update() -> dict | None:
    """Return release info dict if a newer version exists on GitHub, else None.
    Raises on network failure.
    """
    req = urllib.request.Request(_API, headers=_api_headers())
    with urllib.request.urlopen(req, timeout=_API_CHECK_TIMEOUT_S) as resp:
        data = json.loads(resp.read().decode())

    tag    = data.get("tag_name", "")
    remote = _parse_version(tag)
    local  = _parse_version(__version__)

    if remote <= local:
        return None

    asset_url = None
    for asset in data.get("assets", []):
        if asset["name"] == _ASSET_NAME:
            asset_url = asset.get("url")
            break

    return {
        "tag":       tag,
        "notes":     data.get("body", "").strip(),
        "asset_url": asset_url,
        "html_url":  data.get("html_url", ""),
    }


def download_update(asset_url: str, progress_cb=None) -> str:
    """Download the release tarball to a temp file. Returns the temp path."""
    req = urllib.request.Request(
        asset_url,
        headers={**_api_headers(), "Accept": "application/octet-stream"},
    )
    with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT_S) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        fd, tmp_path = tempfile.mkstemp(suffix=".tar.gz", prefix="handsfree_update_")
        done = 0
        with os.fdopen(fd, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress_cb:
                    progress_cb(done, total)
    return tmp_path


def apply_update_linux(tmp_path: str):
    """
    Extract the tarball over the current install directory and re-launch.

    The tarball is expected to contain a single top-level directory (GitHub's
    default layout for source archives).  Its contents are copied on top of
    the repo root, preserving ~/.config/handsfree user data untouched.
    A small shell script does the actual swap after this process exits so
    we don't overwrite ourselves while running.
    """
    root = _repo_root()

    # Write a swap script that runs after we exit
    fd, script = tempfile.mkstemp(suffix=".sh", prefix="handsfree_upd_")
    with os.fdopen(fd, "w") as f:
        f.write("#!/usr/bin/env bash\n")
        f.write("sleep 1\n")
        # Extract into a temp dir first
        f.write(f'TMP=$(mktemp -d)\n')
        f.write(f'tar -xzf "{tmp_path}" -C "$TMP"\n')
        # Find the single top-level dir GitHub creates (e.g. Owner-Repo-abc1234/)
        f.write(f'SRC=$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -1)\n')
        # Copy new files over the install dir
        f.write(f'cp -r "$SRC"/. "{root}/"\n')
        # Clean up
        f.write(f'rm -rf "$TMP" "{tmp_path}"\n')
        f.write(f'rm -- "$0"\n')
        # Re-launch
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


# ── QThread workers ──────────────────────────────────────────────────────────

class UpdateChecker(QThread):
    """Non-blocking update check — create, connect signals, call start()."""
    update_available = pyqtSignal(dict)   # release info dict
    up_to_date       = pyqtSignal()
    check_failed     = pyqtSignal(str)    # error message

    def run(self):
        try:
            info = check_for_update()
            if info:
                self.update_available.emit(info)
            else:
                self.up_to_date.emit()
        except Exception as e:
            self.check_failed.emit(str(e))


class UpdateDownloader(QThread):
    """Non-blocking download with progress reporting."""
    progress = pyqtSignal(int, int)   # bytes_done, total_bytes
    finished = pyqtSignal(str)        # tmp_path of downloaded file
    failed   = pyqtSignal(str)        # error message

    def __init__(self, asset_url: str, parent=None):
        super().__init__(parent)
        self._url = asset_url

    def run(self):
        try:
            path = download_update(self._url, self.progress.emit)
            self.finished.emit(path)
        except Exception as e:
            self.failed.emit(str(e))
