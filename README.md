# HandsFree for Linux

Turn your Linux computer into a Bluetooth hands-free kit — like a car speakerphone.  
Your phone connects via HFP (Hands-Free Profile) and HandsFree handles calls, contacts, and audio routing.

---

## Features

- **Incoming & outgoing calls** — answer, decline, or dial from your computer
- **Contact sync** — pulls your phone's contact book via PBAP (including profile photos)
- **Contact profiles** — photo, name, full call history, one-click call
- **In-call screen** — shows contact name/photo, live duration timer, volume slider, Mute and End Call buttons
- **Floating call overlay** — always-on-top mini window visible even when the app is minimised; shows avatar, timer, Mute, End Call; flashes red "Call Ended" when the call ends
- **Ringtone** — plays system ringtone on incoming calls; plays ringback tone while waiting for the other person to pick up
- **Call log** — incoming / outgoing / missed calls with contact names, duration, timestamps; right-click to open profile or redial
- **Audio device selection** — choose speaker and microphone directly from the in-call screen
- **System tray icon** — grey (disconnected) · green (connected) · red (in call)
- **VoIP detection** — suppresses Bluetooth calls when Teams / Zoom / Meet / Slack / Discord is active

---

## Requirements

### Linux

```bash
# System packages
sudo apt install bluez bluez-obexd python3-dbus python3-gi

# Python packages
pip install -r requirements.txt
```

| Package | Purpose |
|---|---|
| `PyQt6` | UI framework |
| `dbus-python` | BlueZ / obexd D-Bus communication |
| `PyGObject` (python3-gi) | GLib main loop integration |
| `vobject` | vCard parsing for PBAP contact sync |
| `psutil` | VoIP process detection |

### Audio

HandsFree uses PipeWire (via `pactl`) for audio routing. PipeWire is the default on Ubuntu 22.04+, Fedora 34+, and most modern distros.

---

## Quick Start

```bash
git clone https://github.com/PavelTarlev1/handsfree-linux.git
cd handsfree-linux
pip install -r requirements.txt
python3 main.py
```

The app starts in the system tray. Right-click the headset icon to connect your phone.

**Debug mode** (shows detailed logs):
```bash
python3 main.py --debug
```

---

## Pairing your phone

1. Pair your phone with your computer via system Bluetooth settings (just once).
2. Open HandsFree → right-click tray icon → **Connect**.
3. On Android: accept the contacts permission popup on your phone.  
   On iPhone: go to **Settings → Bluetooth → tap ⓘ next to your computer → enable "Sync Contacts"**.

---

## WirePlumber conflict

On systems running WirePlumber, it also tries to register the HFP Hands-Free role, causing:

```
org.bluez.Error.AlreadyExists
```

Fix: disable WirePlumber's HFP node. See **[docs/wireplumber-setup.md](docs/wireplumber-setup.md)** for step-by-step instructions.

---

## Project structure

```
main.py                  Entry point
core/
  app.py                 Main coordinator — wires all subsystems together
bluetooth/
  hfp_profile.py         Registers HFP HF profile with BlueZ via D-Bus
  slc.py                 SLC state machine — AT command handshake
  at_handler.py          AT command parser/builder
  pbap_client.py         Pulls contacts + photos from phone via PBAP/obexd
  agent.py               BlueZ pairing agent (auto-accepts)
audio/
  manager.py             Routes PipeWire SCO sink/source on call start/end
  sco_bridge.py          Low-level SCO socket ↔ pacat audio bridge
  ringer.py              Looping ringtone / ringback tone player
  recorder.py            Records calls to stereo WAV (RX + TX channels)
contacts/
  models.py              Contact and CallLog dataclasses
  store.py               SQLite CRUD — contacts, call log, photos
voip/
  detector.py            Detects active VoIP apps via /proc, pactl, pw-dump
ui/
  main_window.py         Main window — contacts, dial pad, call log, settings
  contacts_widget.py     Contact list with search, rename, delete
  contact_profile.py     Contact profile dialog — photo, history, call button
  call_popup.py          Incoming call popup — Answer / Decline / Silence
  call_overlay.py        Floating always-on-top in-call overlay
  tray.py                System tray icon and menu
```

---

## Configuration

Config file: `~/.config/handsfree/config.toml` (created with defaults on first run)  
Database: `~/.config/handsfree/contacts.db`  
Logs: `~/.config/handsfree/handsfree.log`

---

## License

MIT