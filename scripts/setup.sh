#!/bin/bash
# One-time setup for HandsFree on Linux.
# Run once with: bash scripts/setup.sh
#
# Supports: Ubuntu, Debian, Fedora, Arch, openSUSE, and other systemd distros.

set -e

# ── Bluetooth device class ────────────────────────────────────────────────────
# Class 0x200408 = Telephony service + Audio/Video major + Handsfree minor.
# Required so iPhone shows the "Sync Contacts" toggle.
# Set it permanently in main.conf and also apply it on every boot via a service.

MAIN_CONF=/etc/bluetooth/main.conf

echo "Setting Bluetooth device class in $MAIN_CONF..."
if [ -f "$MAIN_CONF" ]; then
    if grep -q "^Class" "$MAIN_CONF"; then
        sudo sed -i 's/^Class.*/Class = 0x200408/' "$MAIN_CONF"
    elif grep -q "^\[General\]" "$MAIN_CONF"; then
        sudo sed -i '/^\[General\]/a Class = 0x200408' "$MAIN_CONF"
    else
        echo -e "\n[General]\nClass = 0x200408" | sudo tee -a "$MAIN_CONF" > /dev/null
    fi
else
    echo -e "[General]\nClass = 0x200408" | sudo tee "$MAIN_CONF" > /dev/null
fi

# Pick the best available tool for applying the class at boot time
_set_class_cmd=""
if command -v hciconfig >/dev/null 2>&1; then
    _set_class_cmd="/usr/bin/env hciconfig hci0 class 0x200408"
elif command -v btmgmt >/dev/null 2>&1; then
    # btmgmt sets major/minor; service class bits come from main.conf above
    _set_class_cmd="/usr/bin/env btmgmt class 4 8"
fi

# ── Systemd service (if systemd is available) ─────────────────────────────────
if command -v systemctl >/dev/null 2>&1 && systemctl is-system-running --quiet 2>/dev/null || \
   [ -d /run/systemd/system ]; then

    SERVICE_FILE=/etc/systemd/system/bluetooth-class.service
    echo "Installing bluetooth-class systemd service..."

    if [ -n "$_set_class_cmd" ]; then
        sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=Set Bluetooth device class for HandsFree
After=bluetooth.service
Requires=bluetooth.service

[Service]
Type=oneshot
ExecStart=$_set_class_cmd
RemainAfterExit=yes

[Install]
WantedBy=bluetooth.target
EOF
        sudo systemctl daemon-reload
        sudo systemctl enable bluetooth-class.service
        sudo systemctl start bluetooth-class.service
        echo "  bluetooth-class.service installed and started."
    else
        echo "  Warning: neither hciconfig nor btmgmt found."
        echo "  Device class will be applied from main.conf on next Bluetooth restart."
    fi
else
    echo "  systemd not detected — skipping service install."
    echo "  Apply class manually on each boot: sudo hciconfig hci0 class 0x200408"
fi

# ── WirePlumber HFP conflict fix ──────────────────────────────────────────────
# WirePlumber registers hfp_hf which conflicts with our HFP handler.
# Disable it by removing hfp_hf from the roles list.
# Different config format for WirePlumber 0.4.x (Lua) vs 0.5.x (.conf).

if command -v wireplumber >/dev/null 2>&1; then
    WP_VERSION=$(wireplumber --version 2>&1 | grep -oP '\d+\.\d+' | head -1)
    WP_MAJOR=$(echo "$WP_VERSION" | cut -d. -f1)
    WP_MINOR=$(echo "$WP_VERSION" | cut -d. -f2)
    echo "Detected WirePlumber $WP_VERSION"

    if [ "$WP_MAJOR" -ge 1 ] || ( [ "$WP_MAJOR" -eq 0 ] && [ "$WP_MINOR" -ge 5 ] ); then
        # WirePlumber 0.5.x — uses .conf format
        WP_DIR="$HOME/.config/wireplumber/wireplumber.conf.d"
        WP_FILE="$WP_DIR/50-disable-hfp-hf.conf"
        mkdir -p "$WP_DIR"
        cat > "$WP_FILE" << 'EOF'
# Disable WirePlumber's hfp_hf role — HandsFree app handles HFP directly.
monitor.bluez.properties = {
  bluez5.roles = [ a2dp_sink a2dp_source bap_sink bap_source hfp_ag ]
}
EOF
        echo "  WirePlumber 0.5.x config written to $WP_FILE"
    else
        # WirePlumber 0.4.x — uses Lua format
        WP_DIR="$HOME/.config/wireplumber/bluetooth.lua.d"
        WP_FILE="$WP_DIR/50-disable-hfp-hf.lua"
        mkdir -p "$WP_DIR"
        cat > "$WP_FILE" << 'EOF'
-- Disable WirePlumber's hfp_hf role — HandsFree app handles HFP directly.
bluez_monitor.properties = {
  ["bluez5.roles"] = "[ a2dp_sink a2dp_source bap_sink bap_source hfp_ag ]",
}
EOF
        echo "  WirePlumber 0.4.x config written to $WP_FILE"
    fi

    echo "  Restarting WirePlumber..."
    systemctl --user restart wireplumber 2>/dev/null || \
        wpctl status >/dev/null 2>&1 && killall wireplumber 2>/dev/null && \
        nohup wireplumber &>/dev/null & \
        echo "  (restarted manually)"
else
    echo "WirePlumber not found — skipping HFP conflict config (PulseAudio system, no action needed)."
fi

echo ""
echo "Setup complete. Run the app with: python3 main.py"
