#!/usr/bin/env python3
"""
HandsFree — Bluetooth Hands-Free application for Linux/Windows.

Makes your computer act like a car hands-free kit:
  - Phone connects via Bluetooth HFP
  - Incoming/outgoing call handling with pop-up notifications
  - Contact sync from phone (PBAP)
  - VoIP app detection (Teams, Zoom, Meet, Slack, Discord…)python3 main.py --debug
  - Local contact storage with rename support

Usage:
    python main.py [--debug]

Requirements (Linux):
    sudo apt install bluez bluez-obexd python3-dbus python3-gi
    pip install PyQt6 vobject psutil 

For WirePlumber HFP conflict, see: docs/wireplumber-setup.md
"""
import argparse
import logging
import platform
import sys
import os

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def parse_args():
    parser = argparse.ArgumentParser(description="HandsFree — Bluetooth Hands-Free")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--log-file",
        default=None,
        help="Log to file in addition to stderr",
    )
    return parser.parse_args()


def setup_logging(debug: bool, log_file: str | None):
    level = logging.DEBUG if debug else logging.INFO
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]

    if log_file is None:
        from core.config import LOG_FILE
        from core.config import CONFIG_DIR
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        log_file = str(LOG_FILE)

    try:
        fh = logging.FileHandler(log_file)
        fh.setLevel(level)
        handlers.append(fh)
    except OSError as e:
        print(f"Warning: cannot open log file {log_file}: {e}", file=sys.stderr)

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )
    # Quiet noisy libraries
    logging.getLogger("dbus").setLevel(logging.WARNING)
    logging.getLogger("gi").setLevel(logging.WARNING)


def check_dependencies():
    """Warn about missing optional/required packages."""
    missing = []

    try:
        import PyQt6  # noqa: F401
    except ImportError:
        missing.append("PyQt6  (pip install PyQt6)")

    if platform.system() == "Linux":
        try:
            import dbus  # noqa: F401
        except ImportError:
            missing.append("dbus-python  (sudo apt install python3-dbus)")
        try:
            from gi.repository import GLib  # noqa: F401
        except ImportError:
            missing.append("PyGObject  (sudo apt install python3-gi)")

    try:
        import vobject  # noqa: F401
    except ImportError:
        missing.append("vobject  (pip install vobject)  — needed for PBAP contact sync")

    try:
        import psutil  # noqa: F401
    except ImportError:
        missing.append("psutil  (pip install psutil)  — used for VoIP detection on Windows")

    if missing:
        print("\nHandsFree — Missing dependencies:", file=sys.stderr)
        for pkg in missing:
            print(f"  • {pkg}", file=sys.stderr)
        print(file=sys.stderr)

    # Fatal if PyQt6 is missing
    if any("PyQt6" in m for m in missing):
        print("ERROR: PyQt6 is required. Run: pip install PyQt6", file=sys.stderr)
        sys.exit(1)


def main():
    args = parse_args()
    setup_logging(args.debug, args.log_file)
    check_dependencies()

    log = logging.getLogger("main")
    log.info("Starting HandsFree on %s Python %s", platform.system(), sys.version.split()[0])

    # Qt requires QApplication before any QWidget
    from PyQt6.QtWidgets import QApplication
    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("HandsFree")
    qt_app.setApplicationDisplayName("HandsFree")
    qt_app.setQuitOnLastWindowClosed(False)  # Stay alive in tray

    from core.app import HandsFreeApp
    app = HandsFreeApp(qt_app)
    app.start()

    sys.exit(qt_app.exec())


if __name__ == "__main__":
    main()
