"""
System tray icon — shows connection status and provides the quick-action menu.
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap, QTransform
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

logger = logging.getLogger(__name__)


def _make_tray_icon(color: str, size: int = 22,
                    muted: bool = False, badge: int = 0) -> QIcon:
    """Render the tray phone icon with optional mute slash and missed-call badge."""
    from PyQt6.QtCore import QRectF, Qt as _Qt
    from PyQt6.QtGui import QFont

    pix = QPixmap(size, size)
    pix.fill(QColor(0, 0, 0, 0))

    # ── Phone glyph ───────────────────────────────────────────────────────────
    tmp = QPixmap(size, size)
    tmp.fill(QColor(0, 0, 0, 0))
    p = QPainter(tmp)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QColor("white"))
    font = QFont()
    font.setPixelSize(int(size * 0.75))
    p.setFont(font)
    p.drawText(QRectF(0, 0, size, size), _Qt.AlignmentFlag.AlignCenter, "☎")
    p.end()

    colored = QPixmap(size, size)
    colored.fill(QColor(0, 0, 0, 0))
    p2 = QPainter(colored)
    p2.drawPixmap(0, 0, tmp)
    p2.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p2.fillRect(0, 0, size, size, QColor(color))
    p2.end()

    p3 = QPainter(pix)
    p3.setRenderHint(QPainter.RenderHint.Antialiasing)
    p3.drawPixmap(0, 0, colored)

    # ── Mute slash ────────────────────────────────────────────────────────────
    if muted:
        pen = p3.pen()
        from PyQt6.QtCore import Qt as _Qt2
        from PyQt6.QtGui import QPen
        slash_pen = QPen(QColor("#ea4335"), max(2, size // 10))
        slash_pen.setCapStyle(_Qt2.PenCapStyle.RoundCap)
        p3.setPen(slash_pen)
        p3.drawLine(int(size * 0.65), int(size * 0.05),
                    int(size * 0.05), int(size * 0.75))
        p3.setPen(pen)

    # ── Missed-call badge (red circle + number, top-right) ────────────────────
    if badge > 0:
        count = min(badge, 99)
        label = str(count)
        br = size // 2 - 1          # badge radius
        bx = size - br - 1          # centre x
        by = br + 1                 # centre y

        p3.setBrush(QColor("#ea4335"))
        p3.setPen(QColor(0, 0, 0, 0))
        p3.drawEllipse(bx - br, by - br, br * 2, br * 2)

        font2 = QFont()
        font2.setPixelSize(max(6, br + 2))
        font2.setBold(True)
        p3.setFont(font2)
        p3.setPen(QColor("white"))
        p3.drawText(QRectF(bx - br, by - br, br * 2, br * 2),
                    _Qt.AlignmentFlag.AlignCenter, label)

    p3.end()
    return QIcon(pix)


class TrayIcon(QObject):
    action_show_window   = pyqtSignal()
    action_connect       = pyqtSignal()
    action_disconnect    = pyqtSignal()
    action_sync_contacts = pyqtSignal()
    action_quit          = pyqtSignal()
    action_answer        = pyqtSignal()
    action_hangup        = pyqtSignal()
    mute_changed         = pyqtSignal(bool)   # True = muted

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tray = QSystemTrayIcon(_make_tray_icon("#888888"))
        self._tray.setToolTip("HandsFree — Not connected")
        self._tray.activated.connect(self._on_activated)

        self._menu = self._build_menu()
        self._tray.setContextMenu(self._menu)
        self._tray.show()

        self._connected_device: Optional[str] = None
        self._in_call = False
        self._muted = False
        self._missed_calls = 0
        self._mute_timer = QTimer(self)
        self._mute_timer.setSingleShot(True)
        self._mute_timer.timeout.connect(self._unmute)

    def _refresh_icon(self):
        if self._in_call:
            color = "#ea4335"
        elif self._connected_device:
            color = "#34a853"
        else:
            color = "#888888"
        self._tray.setIcon(_make_tray_icon(
            color, muted=self._muted, badge=self._missed_calls
        ))

    def add_missed_call(self):
        self._missed_calls = min(self._missed_calls + 1, 99)
        self._refresh_icon()

    def clear_missed_calls(self):
        self._missed_calls = 0
        self._refresh_icon()

    def set_disconnected(self):
        self._connected_device = None
        self._in_call = False
        self._tray.setToolTip("HandsFree — Not connected")
        self._update_menu_state()
        self._refresh_icon()

    def set_connected(self, device_name: str):
        self._connected_device = device_name
        self._in_call = False
        self._tray.setToolTip(f"HandsFree — {device_name}")
        self._update_menu_state()
        self._refresh_icon()

    def set_in_call(self, number: str = ""):
        self._in_call = True
        tip = "HandsFree — Call active"
        if number:
            tip += f" ({number})"
        if self._connected_device:
            tip += f" via {self._connected_device}"
        self._tray.setToolTip(tip)
        self._update_menu_state()
        self._refresh_icon()

    def set_call_ended(self):
        self._in_call = False
        if self._connected_device:
            self.set_connected(self._connected_device)
        else:
            self.set_disconnected()

    def is_muted(self) -> bool:
        return self._muted

    def _mute_for(self, minutes: int):
        self._muted = True
        self._mute_timer.start(minutes * 60 * 1000)
        self._update_mute_label()
        self._refresh_icon()
        self.mute_changed.emit(True)

    def _mute_indefinitely(self):
        self._muted = True
        self._mute_timer.stop()
        self._update_mute_label()
        self._refresh_icon()
        self.mute_changed.emit(True)

    def _unmute(self):
        self._muted = False
        self._mute_timer.stop()
        self._update_mute_label()
        self._refresh_icon()
        self.mute_changed.emit(False)

    def _update_mute_label(self):
        self._act_unmute.setVisible(self._muted)
        self._act_mute_toggle.setVisible(not self._muted)

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

        # Unmute (shown only when muted)
        self._act_unmute = menu.addAction("🔔  Unmute now")
        self._act_unmute.triggered.connect(self._unmute)
        self._act_unmute.setVisible(False)

        # Mute submenu (shown only when not muted)
        self._act_mute_toggle = menu.addAction("🔕  Mute calls…")
        mute_menu = QMenu("Mute for…", menu)
        mute_menu.setStyleSheet(menu.styleSheet())
        for label, minutes in [
            ("15 minutes",  15),
            ("30 minutes",  30),
            ("1 hour",      60),
            ("2 hours",    120),
            ("4 hours",    240),
        ]:
            act = mute_menu.addAction(label)
            act.triggered.connect(lambda checked, m=minutes: self._mute_for(m))
        mute_menu.addSeparator()
        act_indef = mute_menu.addAction("Until I turn it back on")
        act_indef.triggered.connect(self._mute_indefinitely)
        self._act_mute_toggle.setMenu(mute_menu)

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
