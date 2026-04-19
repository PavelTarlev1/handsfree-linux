from pathlib import Path

_VERSION_FILE = Path(__file__).parent.parent / "VERSION"

try:
    __version__: str = _VERSION_FILE.read_text().strip()
except OSError:
    __version__ = "0.0.0"
