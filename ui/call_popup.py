"""
Incoming call popup — borderless, always-on-top, auto-dismisses.
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from contacts.models import Contact

logger = logging.getLogger(__name__)


class AvatarWidget(QWidget):
    """Circle with the contact's initials."""

    def __init__(self, initials: str, parent=None):
        super().__init__(parent)
        self._initials = initials[:2].upper()
        self.setFixedSize(56, 56)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addEllipse(2, 2, 52, 52)
        painter.fillPath(path, QColor("#1a73e8"))
        painter.setPen(QColor("white"))
        font = QFont("Arial", 18, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._initials)


class IncomingCallPopup(QDialog):
    """
    Non-blocking popup shown on incoming HFP call.
    Emits answered / rejected after the user interacts (or auto-dismiss timeout).
    """

    answered = pyqtSignal()
    rejected = pyqtSignal()
    silenced = pyqtSignal()

    def __init__(
        self,
        number: str,
        contact: Optional[Contact] = None,
        timeout_seconds: int = 30,
        parent=None,
    ):
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self._number = number
        self._contact = contact
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Incoming Call")

        self._build_ui()
        self._position_top_right()

        # Auto-dismiss timer
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(timeout_seconds * 1000)
        self._timer.timeout.connect(self._auto_dismiss)
        self._timer.start()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        name = self._contact.effective_name if self._contact else self._number
        initials = self._get_initials(name)

        # Outer container with rounded corners + drop shadow
        container = QWidget(self)
        container.setObjectName("popup_container")
        container.setStyleSheet("""
            #popup_container {
                background: #202124;
                border-radius: 12px;
                border: 1px solid #3c4043;
            }
        """)

        # Avatar
        avatar = AvatarWidget(initials, container)

        # Name + number labels
        name_label = QLabel(name)
        name_label.setStyleSheet("color: #e8eaed; font-size: 15px; font-weight: bold;")
        num_label = QLabel(self._number)
        num_label.setStyleSheet("color: #9aa0a6; font-size: 12px;")
        calling_label = QLabel("Incoming call via Bluetooth")
        calling_label.setStyleSheet("color: #5f6368; font-size: 11px;")

        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)
        info_layout.addWidget(name_label)
        info_layout.addWidget(num_label)
        info_layout.addWidget(calling_label)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(16, 16, 16, 8)
        top_row.setSpacing(12)
        top_row.addWidget(avatar)
        top_row.addLayout(info_layout)

        # Buttons
        btn_answer = QPushButton("Answer")
        btn_answer.setFixedHeight(36)
        btn_answer.setStyleSheet("""
            QPushButton {
                background: #34a853; color: white;
                border-radius: 18px; font-size: 13px; font-weight: bold;
                padding: 0 20px;
            }
            QPushButton:hover { background: #2d9249; }
        """)
        btn_answer.clicked.connect(self._on_answer)

        btn_reject = QPushButton("Decline")
        btn_reject.setFixedHeight(36)
        btn_reject.setStyleSheet("""
            QPushButton {
                background: #ea4335; color: white;
                border-radius: 18px; font-size: 13px; font-weight: bold;
                padding: 0 20px;
            }
            QPushButton:hover { background: #d33828; }
        """)
        btn_reject.clicked.connect(self._on_reject)

        btn_silence = QPushButton("Silence")
        btn_silence.setFixedHeight(36)
        btn_silence.setStyleSheet("""
            QPushButton {
                background: #444; color: #9aa0a6;
                border-radius: 18px; font-size: 12px;
                padding: 0 16px;
            }
            QPushButton:hover { background: #555; }
        """)
        btn_silence.clicked.connect(self._on_silence)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(16, 4, 16, 16)
        btn_row.setSpacing(8)
        btn_row.addWidget(btn_answer)
        btn_row.addWidget(btn_reject)
        btn_row.addWidget(btn_silence)

        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        container_layout.addLayout(top_row)
        container_layout.addLayout(btn_row)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.addWidget(container)

        self.setFixedWidth(340)

    def _position_top_right(self):
        """Place popup in the top-right corner of the screen."""
        from PyQt6.QtWidgets import QApplication
        screen = QApplication.primaryScreen().geometry()
        self.adjustSize()
        self.move(screen.right() - self.width() - 16, screen.top() + 48)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_answer(self):
        self._timer.stop()
        self.answered.emit()
        self.close()

    def _on_reject(self):
        self._timer.stop()
        self.rejected.emit()
        self.close()

    def _on_silence(self):
        self._timer.stop()
        self.silenced.emit()
        self.close()

    def _auto_dismiss(self):
        logger.debug("Call popup auto-dismissed")
        self.rejected.emit()
        self.close()

    @staticmethod
    def _get_initials(name: str) -> str:
        parts = name.strip().split()
        if not parts:
            return "?"
        if len(parts) == 1:
            return parts[0][0]
        return parts[0][0] + parts[-1][0]