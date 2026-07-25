#!/usr/bin/env bash
# HandsFree — installation helper for Linux
set -e

echo "=== HandsFree installer ==="
echo

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"

# Detect distro family
if command -v pacman &>/dev/null; then
    DISTRO="arch"
elif command -v apt-get &>/dev/null; then
    DISTRO="debian"
else
    echo "Unsupported distro — please install dependencies manually."
    echo "Required: bluez, bluez-obexd, python3-dbus, python3-gi, PyQt6, vobject, psutil"
    exit 1
fi

# System packages
echo "[1/3] Installing system dependencies..."
if [ "$DISTRO" = "arch" ]; then
    sudo pacman -Sy --needed --noconfirm \
        python-pip \
        python-dbus \
        python-gobject \
        bluez \
        bluez-obex \
        dbus \
        glib2
else
    sudo apt-get update -qq
    sudo apt-get install -y \
        python3-pip \
        python3-venv \
        python3-full \
        python3-dbus \
        python3-gi \
        bluez \
        bluez-obexd \
        libdbus-1-dev \
        libglib2.0-dev
fi

# Python packages
echo "[2/3] Installing Python packages..."
if [ "$DISTRO" = "arch" ]; then
    # Arch ships dbus/gi as system packages — install only pure-Python packages
    pip3 install --user vobject psutil PyQt6
else
    # Ubuntu 24.04+ uses an externally-managed Python — use a virtualenv
    if python3 -c "import sys; exit(0 if sys.version_info >= (3,11) else 1)" 2>/dev/null || \
       python3 -m pip install --dry-run pip 2>&1 | grep -q "externally-managed"; then
        echo "  Detected externally-managed Python — using virtualenv at ${VENV_DIR}"
        python3 -m venv --system-site-packages "${VENV_DIR}"
        # system-site-packages gives us dbus + gi from apt; pip installs the rest
        "${VENV_DIR}/bin/pip" install --quiet PyQt6 vobject psutil
        PYTHON="${VENV_DIR}/bin/python3"
    else
        pip3 install --user PyQt6 vobject psutil
        PYTHON="python3"
    fi
fi

PYTHON="${PYTHON:-python3}"

# Desktop entry + icon
echo "[3/3] Creating desktop entry and installing icon..."

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
Exec=${PYTHON} ${SCRIPT_DIR}/main.py
Icon=handsfree
Hidden=false
X-GNOME-Autostart-enabled=true
EOF

cat > ~/.local/share/applications/handsfree.desktop << EOF
[Desktop Entry]
Type=Application
Name=HandsFree
Comment=Bluetooth Hands-Free for your computer
Exec=${PYTHON} ${SCRIPT_DIR}/main.py
Icon=handsfree
Categories=Utility;Network;
StartupNotify=false
EOF

echo
echo "=== Done! ==="
echo
echo "To start HandsFree now:"
echo "  ${PYTHON} ${SCRIPT_DIR}/main.py"
echo
echo "HandsFree will auto-start on next login."
echo
echo "IMPORTANT: If you get a WirePlumber conflict, see:"
echo "  docs/wireplumber-setup.md"
