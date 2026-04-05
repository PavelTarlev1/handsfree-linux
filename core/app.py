"""
HandsFreeApp — the central coordinator.

Threading model:
  Main thread:  Qt event loop (all UI)
  GLib thread:  BlueZ D-Bus callbacks (HfpProfileManager._dbus_thread)
  SLC thread:   Per-connection blocking RFCOMM I/O (SLCConnection._thread)
  VoIP thread:  Periodic polling (VoIPDetector._thread)
  PBAP thread:  Contact sync (ContactSyncWorker, runs once per connection)

All cross-thread communication goes through Qt signals (queued connections).
"""
from __future__ import annotations

import logging
import platform
import threading
import time
from typing import Optional

from PyQt6.QtCore import QMetaObject, QObject, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QApplication, QMessageBox

from audio.manager import AudioManager
from contacts.store import ContactStore
from core.config import Config, DB_FILE
from voip.detector import VoIPDetector

logger = logging.getLogger(__name__)


class _Bridge(QObject):
    """
    Thin Qt object used to cross thread boundaries safely.
    All signals are declared here; slots execute on the Qt main thread.
    """
    sig_connected = pyqtSignal(str, str)    # device_path, device_name
    sig_disconnected = pyqtSignal(str)      # device_path
    sig_ring = pyqtSignal(str)              # caller_number
    sig_call_active = pyqtSignal()
    sig_call_ended = pyqtSignal()
    sig_codec = pyqtSignal(int)             # codec (1=CVSD, 2=mSBC)
    sig_voip_started = pyqtSignal()
    sig_voip_ended = pyqtSignal()
    sig_sync_done = pyqtSignal(int)         # number of contacts synced
    sig_devices_ready = pyqtSignal(object)  # list[dict] of paired devices
    sig_dial_error = pyqtSignal()


class HandsFreeApp(QObject):
    """
    Owns all subsystems. Wires everything together.
    """

    def __init__(self, qt_app: QApplication):
        super().__init__()
        self._qt_app = qt_app
        self._cfg = Config.load()
        self._store = ContactStore(DB_FILE)
        self._audio = AudioManager()
        self._pbap_lock = threading.Lock()  # prevents concurrent PBAP syncs

        self._bridge = _Bridge()
        self._current_device_path: Optional[str] = None
        self._current_device_name: Optional[str] = None
        self._active_codec = 1
        self._ring_number: str = ""
        self._last_dialled: str = ""
        self._popup = None
        self._call_log_id: Optional[int] = None
        self._reconnect_timer: Optional[threading.Timer] = None
        self._call_start_time: Optional[float] = None

        self._init_ui()
        self._connect_bridge_signals()
        self._apply_saved_audio_settings()

    def start(self):
        """Called after Qt app is set up. Starts all background services."""
        self._start_bluetooth()
        self._start_voip_detector()

        if self._cfg.ui.show_main_window_on_start:
            self._window.show()

        self._tray.show_notification(
            "HandsFree ready",
            "Waiting for Bluetooth connection…",
        )

    # ── Initialisation helpers ────────────────────────────────────────────────

    def _init_ui(self):
        from ui.main_window import MainWindow
        from ui.tray import TrayIcon

        self._window = MainWindow(self._store)
        self._tray = TrayIcon()

        # Window signals → app
        self._window.dial_requested.connect(self._on_dial_requested)
        self._window.sync_requested.connect(self._trigger_pbap_sync)
        self._window.connect_requested.connect(self._on_connect_device_requested)
        self._window.disconnect_requested.connect(self._on_disconnect_requested)
        self._window.audio_output_changed.connect(self._on_audio_output_changed)
        self._window.audio_input_changed.connect(self._on_audio_input_changed)
        self._window.volume_changed.connect(self._on_volume_changed)
        self._window.hangup_requested.connect(self._on_hangup)

        # Tray signals → app
        self._tray.action_show_window.connect(self._toggle_window)
        self._tray.action_connect.connect(lambda: self._window.show())
        self._tray.action_disconnect.connect(self._on_disconnect_requested)
        self._tray.action_sync_contacts.connect(self._trigger_pbap_sync)
        self._tray.action_answer.connect(self._on_answer)
        self._tray.action_hangup.connect(self._on_hangup)
        self._tray.action_quit.connect(self._on_quit)

        # Refresh devices list periodically
        from PyQt6.QtCore import QTimer
        self._device_refresh_timer = QTimer()
        self._device_refresh_timer.setInterval(10_000)  # 10 seconds
        self._device_refresh_timer.timeout.connect(self._refresh_devices)
        self._device_refresh_timer.start()

    def _connect_bridge_signals(self):
        """Wire the cross-thread bridge signals to UI slots."""
        b = self._bridge
        b.sig_connected.connect(self._on_connected)
        b.sig_disconnected.connect(self._on_disconnected)
        b.sig_ring.connect(self._on_ring)
        b.sig_call_active.connect(self._on_call_active)
        b.sig_call_ended.connect(self._on_call_ended)
        b.sig_codec.connect(self._on_codec_negotiated)
        b.sig_voip_started.connect(self._on_voip_started)
        b.sig_voip_ended.connect(self._on_voip_ended)
        b.sig_sync_done.connect(self._on_sync_done)
        b.sig_devices_ready.connect(lambda devs: self._window.set_devices(devs))
        b.sig_dial_error.connect(self._on_dial_error)

    def _start_bluetooth(self):
        if platform.system() != "Linux":
            logger.warning("HFP is only supported on Linux in this version.")
            return
        try:
            from bluetooth.hfp_profile import HfpProfileManager
            self._hfp = HfpProfileManager(
                adapter=self._cfg.bluetooth.adapter,
                preferred_codec=self._cfg.bluetooth.preferred_codec,
                on_connected=self._cb_connected,
                on_disconnected=self._cb_disconnected,
                on_ring=self._cb_ring,
                on_call_answered=self._cb_call_answered,
                on_call_ended=self._cb_call_ended,
                on_call_active=self._cb_call_active,
                on_codec_negotiated=self._cb_codec,
                on_dial_error=self._cb_dial_error,
            )
            self._hfp.start()
        except Exception as e:
            logger.exception("Failed to start Bluetooth HFP: %s", e)
            self._hfp = None

    def _start_voip_detector(self):
        if not self._cfg.voip.enabled:
            return
        self._voip = VoIPDetector(
            poll_interval=self._cfg.voip.poll_interval_seconds,
            process_names=set(self._cfg.voip.process_names),
            on_started=lambda: self._bridge.sig_voip_started.emit(),
            on_ended=lambda: self._bridge.sig_voip_ended.emit(),
        )
        self._voip.start()

    # ── Bluetooth callbacks (called from GLib/SLC threads) ────────────────────

    def _cb_connected(self, device_path: str):
        name = self._hfp.get_device_alias(device_path) if self._hfp else device_path
        # Give AudioManager the device context it needs to open the SCO link
        adapter_addr = self._get_adapter_address()
        bus = getattr(self._hfp, "_bus", None)
        self._audio.set_call_context(device_path, adapter_addr, bus)
        self._bridge.sig_connected.emit(device_path, name)
        if self._cfg.pbap.sync_on_connect and self._should_sync():
            threading.Thread(
                target=self._pbap_sync_thread,
                args=(device_path,),
                daemon=True,
                name="PBAP-Sync",
            ).start()

    def _should_sync(self) -> bool:
        """Only sync if we haven't synced in the last hour — prevents reconnect loop spam."""
        last = getattr(self, "_last_sync_time", 0)
        if time.monotonic() - last > 3600:
            return True
        logger.debug("Skipping PBAP sync — synced recently")
        return False

    def _cb_disconnected(self, device_path: str):
        self._bridge.sig_disconnected.emit(device_path)

    def _cb_ring(self, number: str):
        self._ring_number = number
        self._bridge.sig_ring.emit(number)

    def _cb_call_answered(self):
        self._bridge.sig_call_active.emit()

    def _cb_call_active(self):
        self._bridge.sig_call_active.emit()

    def _cb_call_ended(self):
        self._bridge.sig_call_ended.emit()

    def _cb_codec(self, codec: int):
        self._bridge.sig_codec.emit(codec)

    def _cb_dial_error(self):
        self._bridge.sig_dial_error.emit()

    @pyqtSlot()
    def _on_dial_error(self):
        self._show_dial_error_dialog(self._last_dialled)

    def _show_dial_error_dialog(self, number: str):
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPushButton, QVBoxLayout, QHBoxLayout
        from PyQt6.QtGui import QClipboard
        from PyQt6.QtWidgets import QApplication

        dlg = QDialog(self._window)
        dlg.setWindowTitle("Call failed")
        dlg.setMinimumWidth(380)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(12)

        msg = QLabel(
            "<b>iPhone blocked the outgoing call.</b><br><br>"
            "Apple restricts Bluetooth hands-free dialling to CarPlay-certified devices.<br><br>"
            "<b>To make a call:</b><br>"
            "1. Dial the number on your iPhone as normal<br>"
            "2. The audio will automatically come through your computer<br><br>"
            "Or copy the number below and paste it into your iPhone:"
        )
        msg.setWordWrap(True)
        msg.setOpenExternalLinks(False)
        layout.addWidget(msg)

        if number:
            num_row = QHBoxLayout()
            num_label = QLabel(f"<b>{number}</b>")
            num_label.setStyleSheet("font-size: 16px; color: #1a73e8;")
            num_row.addWidget(num_label)
            btn_copy = QPushButton("Copy")
            btn_copy.setFixedWidth(70)
            btn_copy.clicked.connect(lambda: (
                QApplication.clipboard().setText(number),
                btn_copy.setText("Copied ✓"),
            ))
            num_row.addWidget(btn_copy)
            layout.addLayout(num_row)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        btns.accepted.connect(dlg.accept)
        layout.addWidget(btns)
        dlg.exec()

    # ── Qt slots (always on main thread) ─────────────────────────────────────

    @pyqtSlot(str, str)
    def _on_connected(self, device_path: str, device_name: str):
        self._current_device_path = device_path
        self._current_device_name = device_name
        self._tray.set_connected(device_name)
        self._window.on_connected(device_name)
        self._tray.show_notification("HandsFree", f"Connected to {device_name}")
        self._refresh_devices()

    @pyqtSlot(str)
    def _on_disconnected(self, device_path: str):
        if device_path == self._current_device_path:
            name = self._current_device_name or "phone"
            self._current_device_path = None
            self._current_device_name = None
            self._tray.set_disconnected()
            self._window.on_disconnected()
            logger.info("Disconnected from %s — auto-reconnecting in 3s", name)
            self._schedule_reconnect(device_path)

    def _schedule_reconnect(self, device_path: str):
        """Auto-reconnect after a short delay (handles iOS idle drops)."""
        if self._reconnect_timer:
            self._reconnect_timer.cancel()
        self._reconnect_timer = threading.Timer(
            3.0, self._reconnect_device, args=(device_path,)
        )
        self._reconnect_timer.daemon = True
        self._reconnect_timer.start()

    def _reconnect_device(self, device_path: str):
        """Called from timer thread — ask BlueZ to reconnect."""
        if self._current_device_path:
            return  # Already reconnected via phone-initiated connect
        logger.info("Auto-reconnecting to %s", device_path)
        self._connect_device_thread(device_path)

    @pyqtSlot(str)
    def _on_ring(self, number: str):
        # Suppress popup if a VoIP call is in progress
        if hasattr(self, "_voip") and self._voip.is_active():
            logger.info("Incoming call suppressed — VoIP in progress")
            # Log as missed
            contact = self._store.lookup_by_number(number)
            self._store.log_call(
                "missed", number,
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                contact_id=contact.id if contact else None,
            )
            self._tray.show_notification(
                "Incoming call (suppressed)",
                f"From {number} — VoIP call in progress",
            )
            return

        # Close any existing popup
        if self._popup:
            try:
                self._popup.close()
            except Exception:
                pass

        contact = self._store.lookup_by_number(number)
        from ui.call_popup import IncomingCallPopup
        self._popup = IncomingCallPopup(
            number=number,
            contact=contact,
            timeout_seconds=self._cfg.ui.call_popup_timeout_seconds,
        )
        self._popup.answered.connect(self._on_answer)
        self._popup.rejected.connect(self._on_reject)
        self._popup.show()

        self._tray.set_in_call(number)

        # Start logging as incoming
        contact = self._store.lookup_by_number(number)
        self._call_log_id = self._store.log_call(
            "incoming", number,
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            contact_id=contact.id if contact else None,
        )
        self._call_start_time = time.monotonic()

    @pyqtSlot()
    def _on_call_active(self):
        self._tray.set_in_call(self._ring_number)
        self._window.on_call_active(self._ring_number)
        self._audio.on_call_started(self._active_codec)
        if not self._call_start_time:
            self._call_start_time = time.monotonic()

    @pyqtSlot()
    def _on_call_ended(self):
        if self._popup:
            try:
                self._popup.close()
            except Exception:
                pass
            self._popup = None

        self._tray.set_call_ended()
        self._window.on_call_ended()
        self._audio.on_call_ended()

        # Update call log duration
        if self._call_log_id and self._call_start_time:
            duration = int(time.monotonic() - self._call_start_time)
            self._store.update_call_duration(self._call_log_id, duration)
        self._call_log_id = None
        self._call_start_time = None
        self._ring_number = ""

    @pyqtSlot(int)
    def _on_codec_negotiated(self, codec: int):
        self._active_codec = codec

    @pyqtSlot(str)
    def _on_audio_output_changed(self, sink_name: str):
        self._cfg.audio.call_output_device = sink_name
        self._audio.set_call_devices(sink_name, self._cfg.audio.call_input_device)
        self._save_audio_config()

    @pyqtSlot(str)
    def _on_audio_input_changed(self, source_name: str):
        self._cfg.audio.call_input_device = source_name
        self._audio.set_call_devices(self._cfg.audio.call_output_device, source_name)
        self._save_audio_config()

    @pyqtSlot(int)
    def _on_volume_changed(self, pct: int):
        self._cfg.audio.call_volume = pct
        self._audio.set_call_volume(pct)
        self._save_audio_config()

    def _apply_saved_audio_settings(self):
        """Push config values into AudioManager and the Settings UI dropdowns."""
        self._audio.set_call_devices(
            self._cfg.audio.call_output_device,
            self._cfg.audio.call_input_device,
        )
        self._audio.set_call_volume(self._cfg.audio.call_volume)
        # Populate the Settings dropdowns (loads device list from pactl)
        self._window.set_audio_selection(
            self._cfg.audio.call_output_device,
            self._cfg.audio.call_input_device,
            self._cfg.audio.call_volume,
        )

    def _save_audio_config(self):
        """Persist audio settings back to config.toml."""
        from core.config import CONFIG_FILE
        try:
            text = CONFIG_FILE.read_text()
            # Update call_output_device, call_input_device, call_volume lines
            import re
            def _replace_or_append(t, key, value):
                quoted = isinstance(value, str)
                new_line = f'{key} = "{value}"' if quoted else f'{key} = {value}'
                pattern = rf'^{re.escape(key)}\s*=.*$'
                if re.search(pattern, t, re.MULTILINE):
                    return re.sub(pattern, new_line, t, flags=re.MULTILINE)
                # Append after [audio] section
                return re.sub(r'(\[audio\][^\[]*)', rf'\1{new_line}\n', t, count=1)

            text = _replace_or_append(text, "call_output_device", self._cfg.audio.call_output_device)
            text = _replace_or_append(text, "call_input_device",  self._cfg.audio.call_input_device)
            text = _replace_or_append(text, "call_volume",        self._cfg.audio.call_volume)
            CONFIG_FILE.write_text(text)
        except Exception as e:
            logger.warning("Could not save audio config: %s", e)

    @pyqtSlot()
    def _on_voip_started(self):
        logger.info("VoIP call detected — HFP suppressed")
        self._tray.show_notification(
            "VoIP call active",
            "Bluetooth calls suppressed while in VoIP call",
        )

    @pyqtSlot()
    def _on_voip_ended(self):
        logger.info("VoIP call ended — HFP re-enabled")

    @pyqtSlot(int)
    def _on_sync_done(self, count: int):
        self._contacts_refreshed = True
        self._window._contacts_widget.refresh()
        self._tray.show_notification(
            "Contacts synced",
            f"{count} contact{'s' if count != 1 else ''} updated from phone",
        )

    # ── Actions ───────────────────────────────────────────────────────────────

    def _on_answer(self):
        if self._hfp:
            self._hfp.answer(self._current_device_path)

    def _on_reject(self):
        if self._hfp:
            self._hfp.reject(self._current_device_path)

    def _on_hangup(self):
        if self._hfp:
            self._hfp.hangup(self._current_device_path)

    @pyqtSlot(str)
    def _on_dial_requested(self, number: str):
        if not self._hfp or not self._current_device_path:
            QMessageBox.warning(
                self._window, "Not connected",
                "No phone connected via Bluetooth.\nPlease connect your phone first.",
            )
            return

        number = self._format_dial_number(number)
        self._last_dialled = number
        logger.info("Dialing: %s", number)
        self._hfp.dial(number, self._current_device_path)

        contact = self._store.lookup_by_number(number)
        self._call_log_id = self._store.log_call(
            "outgoing", number,
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            contact_id=contact.id if contact else None,
        )
        self._call_start_time = time.monotonic()

        # Show iPhone-specific hint — iOS blocks ATD when screen is locked
        self._tray.show_notification(
            "Dialing…",
            f"Calling {number} via {self._current_device_name or 'phone'}\n"
            "If it fails, unlock your iPhone screen first.",
        )

    def _format_dial_number(self, number: str) -> str:
        """
        Apply country code expansion so the user never has to type +359.
        e.g. "0899123456" → "+359899123456" when country_code = "359"
        """
        import re
        # Strip spaces, dashes, parentheses — keep +, digits, *, #
        number = re.sub(r"[\s\-\(\)]", "", number)
        if number.startswith("+") or not self._cfg.dial.country_code:
            return number
        # Strip one leading zero before adding country code
        if self._cfg.dial.strip_leading_zero and number.startswith("0"):
            number = number[1:]
        return f"+{self._cfg.dial.country_code}{number}"

    @pyqtSlot()
    def _trigger_pbap_sync(self):
        if not self._current_device_path or not self._hfp:
            return
        # Get device address from BlueZ
        address = ""
        for d in self._hfp.get_paired_devices():
            if d["path"] == self._current_device_path:
                address = d["address"]
                break
        if address:
            threading.Thread(
                target=self._pbap_sync_thread,
                args=(self._current_device_path,),
                daemon=True,
                name="PBAP-Manual",
            ).start()

    @pyqtSlot(str)
    def _on_connect_device_requested(self, device_path: str):
        """User clicked Connect — ask BlueZ to connect to the device."""
        if not self._hfp:
            return
        # Run in a thread — Connect() is slow and blocks until the phone responds.
        threading.Thread(
            target=self._connect_device_thread,
            args=(device_path,),
            daemon=True,
        ).start()

    def _connect_device_thread(self, device_path: str):
        try:
            import dbus
            bus = self._hfp._bus
            device = dbus.Interface(
                bus.get_object("org.bluez", device_path),
                "org.bluez.Device1",
            )
            device.Connect()
        except Exception as e:
            err_name = getattr(e, "_dbus_error_name", "")
            # NoReply is common when the phone connects before D-Bus times out — not an error
            if "NoReply" in err_name or "AlreadyConnected" in err_name:
                logger.debug("Connect D-Bus reply: %s (connection may have succeeded)", err_name)
            else:
                logger.error("Connect failed: %s", e)

    @pyqtSlot()
    def _on_disconnect_requested(self):
        if not self._hfp or not self._current_device_path:
            return
        try:
            import dbus
            bus = self._hfp._bus
            device = dbus.Interface(
                bus.get_object("org.bluez", self._current_device_path),
                "org.bluez.Device1",
            )
            device.Disconnect()
        except Exception as e:
            logger.error("Disconnect failed: %s", e)

    def _toggle_window(self):
        if self._window.isVisible():
            self._window.hide()
        else:
            self._window.show()
            self._window.raise_()
            self._window.activateWindow()

    def _refresh_devices(self):
        # Run D-Bus device enumeration in a thread — doing it on the Qt main
        # thread blocks the UI while BlueZ responds.
        if not self._hfp:
            return
        threading.Thread(target=self._refresh_devices_thread, daemon=True).start()

    def _refresh_devices_thread(self):
        try:
            devices = self._hfp.get_paired_devices()
            # Post result back to Qt main thread via signal
            self._bridge.sig_devices_ready.emit(devices)
        except Exception as e:
            logger.debug("Device refresh error: %s", e)

    def _get_adapter_address(self) -> str:
        """Return the local Bluetooth adapter MAC address (e.g. 'F8:3D:C6:88:41:9B')."""
        try:
            import dbus
            bus = getattr(self._hfp, "_bus", None)
            if bus is None:
                return ""
            adapter_path = f"/org/bluez/{self._cfg.bluetooth.adapter}"
            props = dbus.Interface(
                bus.get_object("org.bluez", adapter_path),
                "org.freedesktop.DBus.Properties",
            )
            return str(props.Get("org.bluez.Adapter1", "Address"))
        except Exception as e:
            logger.debug("Could not read adapter address: %s", e)
            return ""

    def _on_quit(self):
        if self._reconnect_timer:
            self._reconnect_timer.cancel()
        if hasattr(self, "_voip"):
            self._voip.stop()
        if hasattr(self, "_hfp") and self._hfp:
            self._hfp.stop()
        self._store.close()
        self._qt_app.quit()

    # ── PBAP sync (runs in its own thread) ────────────────────────────────────

    def _pbap_sync_thread(self, device_path: str):
        """Pull contacts from phone and upsert into local DB."""
        if not self._pbap_lock.acquire(blocking=False):
            logger.debug("PBAP sync already in progress — skipping")
            return
        try:
            # Small delay so the HFP SLC can fully settle before PBAP starts
            time.sleep(2)

            address = ""
            if self._hfp:
                for d in self._hfp.get_paired_devices():
                    if d["path"] == device_path:
                        address = d["address"]
                        break
            if not address:
                logger.warning("PBAP sync: could not find address for %s", device_path)
                return

            from bluetooth.pbap_client import PBAPClient
            client = PBAPClient()
            try:
                if not client.connect(address):
                    return
                contacts = client.pull_all_contacts()
                count = 0
                for c in contacts:
                    self._store.upsert_contact(c)
                    count += 1
                self._last_sync_time = time.monotonic()
                self._bridge.sig_sync_done.emit(count)
            finally:
                client.disconnect()
        finally:
            self._pbap_lock.release()