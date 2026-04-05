# WirePlumber HFP Conflict — Setup Guide

## The Problem

Modern Linux systems running PipeWire use **WirePlumber** as the session manager.
WirePlumber automatically registers its own HFP Hands-Free profile with BlueZ.

When HandsFree tries to register the same UUID (`0000111e-...`), BlueZ returns:
```
org.bluez.Error.AlreadyExists
```

## Solution: Disable WirePlumber's HFP Management

Create a WirePlumber config file to prevent it from managing HFP:

```bash
sudo mkdir -p /etc/wireplumber/bluetooth.lua.d/
sudo tee /etc/wireplumber/bluetooth.lua.d/51-handsfree-hfp.lua << 'EOF'
-- Disable WirePlumber's HFP hands-free management
-- HandsFree app handles HFP directly via BlueZ D-Bus
rule = {
  matches = {
    {
      { "node.name", "matches", "*hfp*" },
    },
  },
  apply_properties = {
    ["node.disabled"] = true,
  },
}
table.insert(alsa_monitor.rules, rule)
EOF
```

Then restart WirePlumber:
```bash
systemctl --user restart wireplumber
```

## Alternative: Run HandsFree Before WirePlumber Connects

If you start HandsFree on login (before WirePlumber connects to BlueZ),
HandsFree registers first and WirePlumber won't override it.

Add to your autostart:
```bash
# ~/.config/autostart/handsfree.desktop
[Desktop Entry]
Type=Application
Name=HandsFree
Exec=python3 /home/kenshin_pc/Dev/HandsFree-Linux/main.py
Hidden=false
X-GNOME-Autostart-enabled=true
```

## Verify Registration

Check that HandsFree's UUID is registered:
```bash
busctl introspect org.bluez /org/bluez/hci0 | grep -i profile
```

Or using bluetoothctl:
```bash
bluetoothctl show
```
Look for `HandsfreeUnit` in the UUIDs section.

## Audio Routing

HandsFree uses PipeWire's PulseAudio compatibility layer (`pactl`) to route
SCO audio when a call starts. The HFP SCO transport is managed by WirePlumber
(for the SCO audio link itself) but HandsFree controls which sink/source is default.

This means:
- WirePlumber handles the SCO link establishment (fine to let it)
- HandsFree handles AT commands and call control
- HandsFree routes audio via `pactl set-default-sink/source`
