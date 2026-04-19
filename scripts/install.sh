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

# Desktop entry + icon
echo "[3/3] Creating desktop entry and installing icon..."
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Install icon at standard XDG sizes
for SIZE in 16 32 48 64 128 256 512; do
    ICON_SRC="${SCRIPT_DIR}/resources/icon_${SIZE}.png"
    if [ -f "$ICON_SRC" ]; then
        ICON_DIR="${HOME}/.local/share/icons/hicolor/${SIZE}x${SIZE}/apps"
        mkdir -p "$ICON_DIR"
        cp "$ICON_SRC" "$ICON_DIR/handsfree.png"
    fi
done
# Refresh icon cache if possible
gtk-update-icon-cache -f -t "${HOME}/.local/share/icons/hicolor" 2>/dev/null || true

mkdir -p ~/.config/autostart ~/.local/share/applications

cat > ~/.config/autostart/handsfree.desktop << EOF
[Desktop Entry]
Type=Application
Name=HandsFree
Comment=Bluetooth Hands-Free for your computer
Exec=python3 ${SCRIPT_DIR}/main.py
Icon=handsfree
Hidden=false
X-GNOME-Autostart-enabled=true
EOF

cat > ~/.local/share/applications/handsfree.desktop << EOF
[Desktop Entry]
Type=Application
Name=HandsFree
Comment=Bluetooth Hands-Free for your computer
Exec=python3 ${SCRIPT_DIR}/main.py
Icon=handsfree
Categories=Utility;Network;
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
