"""
HFP Hands-Free (HF) profile — registers with BlueZ ProfileManager1.

BlueZ calls NewConnection() when a phone connects.
We receive a raw RFCOMM file descriptor and hand it to SLCConnection.

IMPORTANT: WirePlumber may also register an HFP handler.
If you get org.bluez.Error.AlreadyExists, see docs/wireplumber-setup.md.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# HFP Hands-Free (HF) service UUID
HFP_HF_UUID = "0000111e-0000-1000-8000-00805f9b34fb"
# D-Bus object path for our profile
PROFILE_DBUS_PATH = "/com/handsfree/HfpProfile"


class HfpProfileManager:
    """
    Registers the HFP HF profile with BlueZ and manages per-device SLC sessions.
    Must run within a GLib main loop (call start_glib_loop() from a daemon thread).
    """

    def __init__(
        self,
        adapter: str = "hci0",
        preferred_codec: str = "msbc",
        on_connected: Optional[Callable] = None,
        on_disconnected: Optional[Callable] = None,
        on_ring: Optional[Callable] = None,
        on_call_answered: Optional[Callable] = None,
        on_call_ended: Optional[Callable] = None,
        on_call_active: Optional[Callable] = None,
        on_codec_negotiated: Optional[Callable] = None,
        on_dial_error: Optional[Callable] = None,
        on_bt_powered: Optional[Callable] = None,   # called with True/False
    ):
        self._adapter = adapter
        self._preferred_codec = preferred_codec
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected
        self._on_ring = on_ring
        self._on_call_answered = on_call_answered
        self._on_call_ended = on_call_ended
        self._on_call_active = on_call_active
        self._on_codec_negotiated = on_codec_negotiated
        self._on_dial_error = on_dial_error
        self._on_bt_powered = on_bt_powered

        self._connections: dict[str, object] = {}   # device_path → SLCConnection
        self._loop = None
        self._registered = False

    # ── GLib / D-Bus bootstrap ────────────────────────────────────────────────

    def start(self):
        """
        Start the GLib main loop in a daemon thread and register the profile.
        Returns immediately; D-Bus runs in background.
        """
        t = threading.Thread(target=self._dbus_thread, daemon=True, name="DBus-GLib")
        t.start()

    def _dbus_thread(self):
        try:
            import dbus
            import dbus.mainloop.glib
            from gi.repository import GLib

            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
            self._bus = dbus.SystemBus()
            self._loop = GLib.MainLoop()

            self._register_profile()
            self._watch_adapter_power()
            logger.info("GLib main loop starting")
            self._loop.run()
        except Exception as e:
            logger.exception("D-Bus/GLib thread error: %s", e)

    def _watch_adapter_power(self):
        """Subscribe to BlueZ adapter Powered property changes."""
        try:
            import dbus

            adapter_path = f"/org/bluez/{self._adapter}"

            # Emit the initial power state immediately
            try:
                props_iface = dbus.Interface(
                    self._bus.get_object("org.bluez", adapter_path),
                    "org.freedesktop.DBus.Properties",
                )
                powered = bool(props_iface.Get("org.bluez.Adapter1", "Powered"))
                if self._on_bt_powered:
                    self._on_bt_powered(powered)
            except Exception as e:
                logger.debug("Could not read initial adapter power state: %s", e)

            def _on_properties_changed(interface, changed, invalidated, path=None):
                if interface != "org.bluez.Adapter1":
                    return
                if "Powered" in changed and self._on_bt_powered:
                    powered = bool(changed["Powered"])
                    logger.info("Bluetooth adapter %s", "powered on" if powered else "powered off")
                    self._on_bt_powered(powered)

            self._bus.add_signal_receiver(
                _on_properties_changed,
                signal_name="PropertiesChanged",
                dbus_interface="org.freedesktop.DBus.Properties",
                path=adapter_path,
                path_keyword="path",
            )
        except Exception as e:
            logger.debug("Could not watch adapter power: %s", e)

    def _register_profile(self):
        import dbus
        import dbus.service

        # Create the D-Bus profile object
        self._profile_obj = _HfpProfileObject(
            self._bus,
            PROFILE_DBUS_PATH,
            preferred_codec=self._preferred_codec,
            on_new_connection=self._handle_new_connection,
            on_request_disconnection=self._handle_disconnection,
        )

        # Register with BlueZ ProfileManager1
        profile_manager = dbus.Interface(
            self._bus.get_object("org.bluez", "/org/bluez"),
            "org.bluez.ProfileManager1",
        )

        options = {
            "Name": dbus.String("HandsFree"),
            "Role": dbus.String("client"),
            "Channel": dbus.UInt16(0),         # auto-assign RFCOMM channel
            "AutoConnect": dbus.Boolean(True),
            "RequireAuthentication": dbus.Boolean(True),
            "RequireAuthorization": dbus.Boolean(False),
        }

        try:
            profile_manager.RegisterProfile(
                dbus.ObjectPath(PROFILE_DBUS_PATH),
                dbus.String(HFP_HF_UUID),
                options,
            )
            self._registered = True
            logger.info("HFP HF profile registered (UUID=%s)", HFP_HF_UUID)
        except Exception as e:
            error_name = getattr(e, "_dbus_error_name", "")
            if "AlreadyExists" in error_name:
                logger.warning(
                    "HFP profile already registered (WirePlumber conflict?). "
                    "See docs/wireplumber-setup.md to disable WirePlumber HFP."
                )
            elif "NotPermitted" in error_name:
                logger.error(
                    "HFP UUID already claimed by another process. "
                    "Ensure WirePlumber hfp_hf role is disabled: "
                    "see ~/.config/wireplumber/bluetooth.lua.d/50-bluez-config.lua"
                )
            else:
                logger.error("Failed to register HFP profile: %s", e)

    def _stop_wireplumber_and_retry(self, profile_manager, options):
        import subprocess
        import time
        import dbus

        result = subprocess.run(
            ["systemctl", "--user", "stop", "wireplumber"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            logger.error(
                "Failed to stop WirePlumber: %s. "
                "Run 'systemctl --user stop wireplumber' manually.",
                result.stderr.strip(),
            )
            return

        logger.info("WirePlumber stopped. Retrying HFP registration...")
        time.sleep(1)

        try:
            profile_manager.RegisterProfile(
                dbus.ObjectPath(PROFILE_DBUS_PATH),
                dbus.String(HFP_HF_UUID),
                options,
            )
            self._registered = True
            logger.info("HFP HF profile registered after stopping WirePlumber.")
        except Exception as e:
            logger.error(
                "HFP registration still failed after stopping WirePlumber: %s", e
            )
            return

        # Restart WirePlumber so system audio keeps working.
        # WirePlumber will try to re-register HFP and get AlreadyExists
        # (our profile already holds the slot), so audio routing resumes
        # without displacing our HFP handler.
        subprocess.run(
            ["systemctl", "--user", "start", "wireplumber"],
            capture_output=True, text=True,
        )
        logger.info("WirePlumber restarted — system audio restored.")

    def stop(self):
        if self._loop:
            self._loop.quit()

    # ── Connection handlers ───────────────────────────────────────────────────

    def _handle_new_connection(self, device_path: str, sock: socket.socket):
        """Called from the D-Bus/GLib thread when a phone connects."""
        from bluetooth.slc import SLCConnection

        logger.info("New HFP connection from %s", device_path)

        # Close any existing connection for this device
        old = self._connections.get(device_path)
        if old:
            old.stop()

        slc = SLCConnection(
            sock=sock,
            device_path=device_path,
            preferred_codec=self._preferred_codec,
            on_established=self._on_connected,
            on_disconnected=self._handle_slc_disconnected,
            on_ring=self._on_ring,
            on_call_answered=self._on_call_answered,
            on_call_ended=self._on_call_ended,
            on_call_active=self._on_call_active,
            on_codec_negotiated=self._on_codec_negotiated,
            on_dial_error=self._on_dial_error,
        )
        self._connections[device_path] = slc
        slc.start()

    def _handle_disconnection(self, device_path: str):
        slc = self._connections.pop(device_path, None)
        if slc:
            slc.stop()
        if self._on_disconnected:
            self._on_disconnected(device_path)

    def _handle_slc_disconnected(self, device_path: str):
        self._connections.pop(device_path, None)
        if self._on_disconnected:
            self._on_disconnected(device_path)

    # ── Call control (delegate to active SLC) ─────────────────────────────────

    def get_active_slc(self, device_path: Optional[str] = None):
        """Return the SLC for device_path, or the first active one."""
        if device_path and device_path in self._connections:
            return self._connections[device_path]
        return next(iter(self._connections.values()), None)

    def answer(self, device_path: Optional[str] = None):
        slc = self.get_active_slc(device_path)
        if slc:
            slc.answer()

    def reject(self, device_path: Optional[str] = None):
        slc = self.get_active_slc(device_path)
        if slc:
            slc.reject()

    def hangup(self, device_path: Optional[str] = None):
        slc = self.get_active_slc(device_path)
        if slc:
            slc.hangup()

    def dial(self, number: str, device_path: Optional[str] = None):
        slc = self.get_active_slc(device_path)
        if slc:
            slc.dial(number)

    def get_device_alias(self, device_path: str) -> str:
        """Look up the device's friendly name from BlueZ."""
        try:
            import dbus
            device = self._bus.get_object("org.bluez", device_path)
            props = dbus.Interface(device, "org.freedesktop.DBus.Properties")
            return str(props.Get("org.bluez.Device1", "Alias"))
        except Exception:
            return device_path.split("/")[-1]

    def get_paired_devices(self) -> list[dict]:
        """Return paired devices that support HFP (phones / car kits only)."""
        HFP_AG_UUID = "0000111f-0000-1000-8000-00805f9b34fb"
        devices = []
        try:
            import dbus
            manager = dbus.Interface(
                self._bus.get_object("org.bluez", "/"),
                "org.freedesktop.DBus.ObjectManager",
            )
            objects = manager.GetManagedObjects()
            for path, interfaces in objects.items():
                if "org.bluez.Device1" in interfaces:
                    props = interfaces["org.bluez.Device1"]
                    if not props.get("Paired", False):
                        continue
                    uuids = [str(u).lower() for u in props.get("UUIDs", [])]
                    if HFP_AG_UUID not in uuids:
                        continue
                    devices.append({
                        "path": str(path),
                        "name": str(props.get("Alias", props.get("Name", "Unknown"))),
                        "address": str(props.get("Address", "")),
                        "connected": bool(props.get("Connected", False)),
                    })
        except Exception as e:
            logger.error("Failed to enumerate devices: %s", e)
        return devices


# ── D-Bus Profile Object ──────────────────────────────────────────────────────

class _HfpProfileObject:
    """
    The actual D-Bus service object that BlueZ calls NewConnection/RequestDisconnection on.
    Uses dbus.service.Object under the hood.
    """

    def __init__(self, bus, path, preferred_codec, on_new_connection, on_request_disconnection):
        import dbus
        import dbus.service

        class _Inner(dbus.service.Object):
            @dbus.service.method(
                "org.bluez.Profile1",
                in_signature="oha{sv}",
                out_signature="",
                sender_keyword=None,
            )
            def NewConnection(inner_self, device_path, fd, fd_properties):
                raw_fd = fd.take()
                # Duplicate the fd before wrapping (fromfd dups it, close original)
                sock = socket.fromfd(raw_fd, socket.AF_BLUETOOTH,
                                     socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
                os.close(raw_fd)
                on_new_connection(str(device_path), sock)

            @dbus.service.method(
                "org.bluez.Profile1",
                in_signature="o",
                out_signature="",
            )
            def RequestDisconnection(inner_self, device_path):
                on_request_disconnection(str(device_path))

            @dbus.service.method(
                "org.bluez.Profile1",
                in_signature="",
                out_signature="",
            )
            def Release(inner_self):
                logger.info("Profile released by BlueZ")

        self._obj = _Inner(bus, path)