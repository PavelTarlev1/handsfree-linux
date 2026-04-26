"""
Floating call overlay — always-on-top mini window shown during any call.

Visible whether the main window is open, minimised or hidden.
Shows: avatar/initials · name · duration · Mute · End Call.
On call end: briefly shows a red "Call Ended" state then hides itself.
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

logger = logging.getLogger(__name__)

_ENDED_LINGER_MS = 2500   # how long to show "Call Ended" before hiding


class CallOverlay(QWidget):
    """
    Small always-on-top floating window.

    Signals
    -------
    hangup_requested  — user clicked End Call
    mute_toggled(bool) — True = muted
    """

    hangup_requested = pyqtSignal()
    mute_toggled     = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.Tool |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setWindowTitle("HandsFree — Call")

        self._muted = False
        self._linger_timer = QTimer(self)
        self._linger_timer.setSingleShot(True)
        self._linger_timer.timeout.connect(self.hide)

        self._build_ui()
        self._position_bottom_right()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.setFixedWidth(300)
        self.setStyleSheet("""
            CallOverlay {
                background: #1c1c1e;
                border: 1px solid #5f6368;
                border-radius: 12px;
            }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        # ── Top row: avatar + name/timer ─────────────────────────────────────
        top = QHBoxLayout()
        top.setSpacing(12)

        self._avatar_lbl = QLabel("?")
        self._avatar_lbl.setFixedSize(52, 52)
        self._avatar_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._avatar_lbl.setStyleSheet(
            "border-radius: 26px; background: #3c4043; border: 2px solid #5f6368;"
            "font-size: 20px; font-weight: bold; color: white;"
        )
        top.addWidget(self._avatar_lbl)

        info = QVBoxLayout()
        info.setSpacing(2)

        self._name_lbl = QLabel("Calling…")
        self._name_lbl.setStyleSheet(
            "font-size: 14px; font-weight: bold; color: #e8eaed; background: transparent;"
        )
        from PyQt6.QtWidgets import QGraphicsBlurEffect
        self._name_blur = QGraphicsBlurEffect()
        self._name_blur.setBlurRadius(6)
        self._name_lbl.setGraphicsEffect(self._name_blur)
        info.addWidget(self._name_lbl)

        self._timer_lbl = QLabel("Calling…")
        self._timer_lbl.setStyleSheet("font-size: 12px; color: #9aa0a6; background: transparent;")
        info.addWidget(self._timer_lbl)

        top.addLayout(info, 1)
        root.addLayout(top)

        # ── Button row: Mute + End Call ───────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self._btn_mute = QPushButton("Mute")
        self._btn_mute.setFixedHeight(36)
        self._btn_mute.setCheckable(True)
        self._btn_mute.setStyleSheet(self._mute_style(False))
        self._btn_mute.toggled.connect(self._on_mute_toggled)
        btn_row.addWidget(self._btn_mute)

        self._btn_end = QPushButton("End Call")
        self._btn_end.setFixedHeight(36)
        self._btn_end.setStyleSheet("""
            QPushButton {
                background: #ea4335; color: white;
                border-radius: 18px; font-size: 13px; font-weight: bold;
            }
            QPushButton:hover   { background: #c5352a; }
            QPushButton:pressed { background: #a02820; }
        """)
        self._btn_end.clicked.connect(self.hangup_requested)
        btn_row.addWidget(self._btn_end)

        root.addLayout(btn_row)

    @staticmethod
    def _mute_style(muted: bool) -> str:
        if muted:
            return (
                "QPushButton{background:#ea4335;color:white;border-radius:18px;"
                "font-size:13px;font-weight:bold;}"
                "QPushButton:hover{background:#c5352a;}"
            )
        return (
            "QPushButton{background:#2d2d2f;color:#e8eaed;border-radius:18px;"
            "font-size:13px;border:1px solid #5f6368;}"
            "QPushButton:hover{background:#3c4043;}"
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def show_calling(self, display_name: str, number: str, photo_bytes: Optional[bytes] = None):
        """Switch to 'dialling' state and show the overlay."""
        self._linger_timer.stop()
        self._set_normal_state()
        self._name_lbl.setText(display_name)
        self._timer_lbl.setText("Calling…")
        self._set_avatar(display_name, photo_bytes)
        self._btn_mute.setChecked(False)
        self._muted = False
        self._position_bottom_right()
        self.show()
        self.raise_()

    def show_active(self, display_name: str, number: str, photo_bytes: Optional[bytes] = None):
        """Switch to 'call active' state."""
        self._linger_timer.stop()
        self._set_normal_state()
        self._name_lbl.setText(display_name)
        self._timer_lbl.setText("0:00")
        self._set_avatar(display_name, photo_bytes)
        self._position_bottom_right()
        self.show()
        self.raise_()

    def update_timer(self, text: str):
        """Called every second from the main window's timer tick."""
        if self._linger_timer.isActive():
            return   # showing "Call Ended" — don't overwrite
        self._timer_lbl.setText(text)

    def show_ended(self):
        """Flash red 'Call Ended' state then auto-hide after linger period."""
        self.setStyleSheet("""
            CallOverlay {
                background: #3a1010;
                border: 1px solid #ea4335;
                border-radius: 12px;
            }
        """)
        self._avatar_lbl.setText("☎")
        self._avatar_lbl.setStyleSheet(
            "border-radius: 26px; background: #ea4335;"
            "font-size: 22px; color: white;"
        )
        self._name_lbl.setText("Call Ended")
        self._name_lbl.setStyleSheet(
            "font-size: 14px; font-weight: bold; color: #ea4335; background: transparent;"
        )
        self._timer_lbl.setText("")
        self._btn_mute.setVisible(False)
        self._btn_end.setVisible(False)
        self._position_bottom_right()
        self.show()
        self.raise_()
        self._linger_timer.start(_ENDED_LINGER_MS)

    # ── Private ───────────────────────────────────────────────────────────────

    def _set_normal_state(self):
        self.setStyleSheet("""
            CallOverlay {
                background: #1c1c1e;
                border: 1px solid #5f6368;
                border-radius: 12px;
            }
        """)
        self._name_lbl.setStyleSheet(
            "font-size: 14px; font-weight: bold; color: #e8eaed; background: transparent;"
        )
        self._btn_mute.setVisible(True)
        self._btn_end.setVisible(True)

    def _set_avatar(self, name: str, photo_bytes: Optional[bytes]):
        _SZ = 52
        if photo_bytes:
            px = QPixmap()
            if px.loadFromData(photo_bytes):
                px = px.scaled(
                    _SZ, _SZ,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._avatar_lbl.setPixmap(px.copy(0, 0, _SZ, _SZ))
                self._avatar_lbl.setStyleSheet(
                    "border-radius: 26px; background: #3c4043; border: 2px solid #5f6368;"
                )
                return

        words = name.split()
        initials = (words[0][0] + words[-1][0]).upper() if len(words) >= 2 else name[:2].upper() or "?"
        self._avatar_lbl.setText(initials)
        self._avatar_lbl.setStyleSheet(
            "border-radius: 26px; background: #3c4043; border: 2px solid #5f6368;"
            "font-size: 20px; font-weight: bold; color: white;"
        )

    def _on_mute_toggled(self, checked: bool):
        self._muted = checked
        self._btn_mute.setText("Unmute" if checked else "Mute")
        self._btn_mute.setStyleSheet(self._mute_style(checked))
        self.mute_toggled.emit(checked)

    def _position_bottom_right(self):
        screen = QApplication.primaryScreen()
        if not screen:
            return
        ag = screen.availableGeometry()
        self.adjustSize()
        margin = 16
        self.move(ag.right() - self.width() - margin,
                  ag.bottom() - self.height() - margin)

    # Allow dragging the overlay
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if e.buttons() == Qt.MouseButton.LeftButton and hasattr(self, "_drag_pos"):
            self.move(e.globalPosition().toPoint() - self._drag_pos)