#!/usr/bin/env bash
# HandsFree — installation helper for Linux
set -e

echo "=== HandsFree installer ==="
echo

# System packages
echo "[1/3] Installing system dependencies..."
sudo apt-get update -qq
sudo apt-get install -y \
    python3-pip \
    python3-dbus \
    python3-gi \
    bluez \
    bluez-obexd \
    libdbus-1-dev \
    libglib2.0-dev

# Python packages
echo "[2/3] Installing Python packages..."
pip3 install --user PyQt6 vobject psutil

# Desktop entry
echo "[3/3] Creating desktop entry..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

mkdir -p ~/.config/autostart
cat > ~/.config/autostart/handsfree.desktop << EOF
[Desktop Entry]
Type=Application
Name=HandsFree
Comment=Bluetooth Hands-Free for your computer
Exec=python3 ${SCRIPT_DIR}/main.py
Hidden=false
X-GNOME-Autostart-enabled=true
EOF

cat > ~/.local/share/applications/handsfree.desktop << EOF
[Desktop Entry]
Type=Application
Name=HandsFree
Comment=Bluetooth Hands-Free for your computer
Exec=python3 ${SCRIPT_DIR}/main.py
Icon=phone
Categories=Utility;
StartupNotify=false
EOF

echo
echo "=== Done! ==="
echo
echo "To start HandsFree now:"
echo "  python3 ${SCRIPT_DIR}/main.py"
echo
echo "HandsFree will auto-start on next login."
echo
echo "IMPORTANT: If you get a WirePlumber conflict, see:"
echo "  docs/wireplumber-setup.md"
