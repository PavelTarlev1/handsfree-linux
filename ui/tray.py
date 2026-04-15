"""
System tray icon — shows connection status and provides the quick-action menu.
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

logger = logging.getLogger(__name__)


def _make_headset_icon(color: str, size: int = 22) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(QColor(0, 0, 0, 0))

    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = QColor(color)

    from PyQt6.QtCore import QRect
    from PyQt6.QtGui import QPen
    pen = QPen(c, max(1, size // 8))
    p.setPen(pen)
    p.setBrush(QColor(0, 0, 0, 0))
    p.drawArc(QRect(size // 6, size // 6, size * 2 // 3, size * 2 // 3), 0, 180 * 16)

    p.setBrush(c)
    p.setPen(QColor(0, 0, 0, 0))
    p.drawEllipse(1, size * 5 // 10, size // 5, size // 4)
    p.drawEllipse(size - 1 - size // 5, size * 5 // 10, size // 5, size // 4)

    p.end()
    return QIcon(pix)


_ICON_INACTIVE = None
_ICON_ACTIVE   = None
_ICON_CALL     = None


def _get_icons():
    global _ICON_INACTIVE, _ICON_ACTIVE, _ICON_CALL
    if _ICON_INACTIVE is None:
        _ICON_INACTIVE = _make_headset_icon("#888888")
        _ICON_ACTIVE   = _make_headset_icon("#34a853")
        _ICON_CALL     = _make_headset_icon("#ea4335")
    return _ICON_INACTIVE, _ICON_ACTIVE, _ICON_CALL


class TrayIcon(QObject):
    action_show_window   = pyqtSignal()
    action_connect       = pyqtSignal()
    action_disconnect    = pyqtSignal()
    action_sync_contacts = pyqtSignal()
    action_quit          = pyqtSignal()
    action_answer        = pyqtSignal()
    action_hangup        = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        icon_inactive, _, _ = _get_icons()

        self._tray = QSystemTrayIcon(icon_inactive)
        self._tray.setToolTip("HandsFree — Not connected")
        self._tray.activated.connect(self._on_activated)

        self._menu = self._build_menu()
        self._tray.setContextMenu(self._menu)
        self._tray.show()

        self._connected_device: Optional[str] = None
        self._in_call = False

    def set_disconnected(self):
        self._connected_device = None
        self._in_call = False
        icon_inactive, _, _ = _get_icons()
        self._tray.setIcon(icon_inactive)
        self._tray.setToolTip("HandsFree — Not connected")
        self._update_menu_state()

    def set_connected(self, device_name: str):
        self._connected_device = device_name
        self._in_call = False
        _, icon_active, _ = _get_icons()
        self._tray.setIcon(icon_active)
        self._tray.setToolTip(f"HandsFree — {device_name}")
        self._update_menu_state()

    def set_in_call(self, number: str = ""):
        self._in_call = True
        _, _, icon_call = _get_icons()
        self._tray.setIcon(icon_call)
        tip = "HandsFree — Call active"
        if number:
            tip += f" ({number})"
        if self._connected_device:
            tip += f" via {self._connected_device}"
        self._tray.setToolTip(tip)
        self._update_menu_state()

    def set_call_ended(self):
        self._in_call = False
        if self._connected_device:
            self.set_connected(self._connected_device)
        else:
            self.set_disconnected()

    def show_notification(self, title: str, message: str):
        self._tray.showMessage(
            title, message,
            QSystemTrayIcon.MessageIcon.Information,
            3000,
        )

    def _build_menu(self) -> QMenu:
        menu = QMenu()
        menu.setStyleSheet("""
            QMenu {
                background: #202124;
                color: #e8eaed;
                border: 1px solid #3c4043;
                border-radius: 4px;
                padding: 4px;
            }
            QMenu::item { padding: 6px 20px; border-radius: 3px; }
            QMenu::item:selected { background: #3c4043; }
            QMenu::separator { height: 1px; background: #3c4043; margin: 4px 8px; }
        """)

        self._act_show = menu.addAction("Open HandsFree")
        self._act_show.triggered.connect(self.action_show_window)

        menu.addSeparator()

        self._act_connect = menu.addAction("Connect phone")
        self._act_connect.triggered.connect(self.action_connect)

        self._act_disconnect = menu.addAction("Disconnect")
        self._act_disconnect.triggered.connect(self.action_disconnect)
        self._act_disconnect.setVisible(False)

        self._act_sync = menu.addAction("Sync contacts")
        self._act_sync.triggered.connect(self.action_sync_contacts)
        self._act_sync.setVisible(False)

        menu.addSeparator()

        self._act_answer = menu.addAction("Answer call")
        self._act_answer.triggered.connect(self.action_answer)
        self._act_answer.setVisible(False)

        self._act_hangup = menu.addAction("Hang up")
        self._act_hangup.triggered.connect(self.action_hangup)
        self._act_hangup.setVisible(False)

        menu.addSeparator()

        act_quit = menu.addAction("Quit HandsFree")
        act_quit.triggered.connect(self.action_quit)

        return menu

    def _update_menu_state(self):
        connected = self._connected_device is not None
        self._act_connect.setVisible(not connected)
        self._act_disconnect.setVisible(connected)
        self._act_sync.setVisible(connected)
        self._act_answer.setVisible(self._in_call)
        self._act_hangup.setVisible(self._in_call)

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.action_show_window.emit()
