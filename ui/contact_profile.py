"""
Contact profile dialog.

Shows photo, name, number, call history for one contact.
Actions: Call, Edit name, Add/Change photo.
Can also be opened from a call-log entry for an unknown number.
"""
from __future__ import annotations

import base64
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
    QInputDialog, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget, QScrollArea,
)

from contacts.models import Contact, CallLog
from contacts.store import ContactStore

logger = logging.getLogger(__name__)

_AVATAR_SIZE = 96


class ContactProfileDialog(QDialog):
    """
    Full-screen contact profile.

    Parameters
    ----------
    contact : Contact or None
        If None, a stub is created from `number`.
    number : str
        Used when contact is None (unknown caller).
    store : ContactStore
    dial_cb : callable(number: str)
        Called when the user clicks Call.
    """

    def __init__(
        self,
        store: ContactStore,
        dial_cb,
        contact: Optional[Contact] = None,
        number: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._store = store
        self._dial_cb = dial_cb
        self._contact = contact
        self._number = number or (contact.phone_number if contact else "")
        self.setWindowTitle("Contact")
        self.setMinimumWidth(380)
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(12)

        # ── Top: photo + name/number ──────────────────────────────────────────
        top = QHBoxLayout()
        top.setSpacing(16)

        self._avatar_label = QLabel()
        self._avatar_label.setFixedSize(_AVATAR_SIZE, _AVATAR_SIZE)
        self._avatar_label.setStyleSheet(
            "border-radius: 48px; background: #3c4043; border: 2px solid #5f6368;"
        )
        self._avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._load_avatar()
        top.addWidget(self._avatar_label)

        info = QVBoxLayout()
        info.setSpacing(4)

        self._name_label = QLabel(self._display_name())
        self._name_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        self._name_label.setWordWrap(True)
        info.addWidget(self._name_label)

        num_label = QLabel(self._number)
        num_label.setStyleSheet("color: #9aa0a6; font-size: 13px;")
        info.addWidget(num_label)

        info.addStretch()

        btn_edit = QPushButton("Edit name")
        btn_edit.setFixedWidth(90)
        btn_edit.clicked.connect(self._on_edit_name)
        info.addWidget(btn_edit)

        top.addLayout(info, 1)
        root.addLayout(top)

        # ── Photo button ──────────────────────────────────────────────────────
        btn_photo = QPushButton("Change photo…")
        btn_photo.clicked.connect(self._on_change_photo)
        root.addWidget(btn_photo)

        # ── Call history ──────────────────────────────────────────────────────
        root.addWidget(_section("Call history"))
        self._history_list = QListWidget()
        self._history_list.setMaximumHeight(220)
        self._history_list.setStyleSheet(
            "QListWidget::item { padding: 5px 4px; border-bottom: 1px solid #2d2d2f; }"
        )
        root.addWidget(self._history_list)
        self._load_history()

        # ── Action buttons ────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_call = QPushButton("📞  Call")
        btn_call.setStyleSheet(
            "background: #34a853; color: white; font-size: 14px; padding: 8px 20px; border-radius: 6px;"
        )
        btn_call.clicked.connect(self._on_call)
        btn_row.addWidget(btn_call)
        btn_row.addStretch()
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        root.addLayout(btn_row)

    def _display_name(self) -> str:
        if self._contact:
            return self._contact.effective_name
        return self._number or "Unknown"

    # ── Avatar ────────────────────────────────────────────────────────────────

    def _load_avatar(self):
        """Try photo_data (user-uploaded), then vCard PHOTO, else show initials."""
        photo_bytes = None
        if self._contact and self._contact.photo_data:
            photo_bytes = self._contact.photo_data
        elif self._contact and self._contact.raw_vcard:
            photo_bytes = _extract_vcard_photo(self._contact.raw_vcard)

        if photo_bytes:
            px = QPixmap()
            if px.loadFromData(photo_bytes):
                px = px.scaled(
                    _AVATAR_SIZE, _AVATAR_SIZE,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._avatar_label.setPixmap(
                    px.copy(0, 0, _AVATAR_SIZE, _AVATAR_SIZE)
                )
                return

        # Fallback: initials
        initials = _initials(self._display_name())
        self._avatar_label.setText(initials)
        self._avatar_label.setStyleSheet(
            "border-radius: 48px; background: #3c4043; border: 2px solid #5f6368;"
            "font-size: 32px; font-weight: bold; color: white;"
        )

    # ── History ───────────────────────────────────────────────────────────────

    def _load_history(self):
        self._history_list.clear()
        if not self._number:
            return
        contact_id = self._contact.id if self._contact else None
        entries = self._store.get_call_log_for_number(
            self._number, limit=50, contact_id=contact_id
        )
        if not entries:
            item = QListWidgetItem("No call history")
            item.setForeground(QColor("#5f6368"))
            self._history_list.addItem(item)
            return

        for e in entries:
            icon  = {"incoming": "↙", "outgoing": "↗", "missed": "✗"}.get(e.direction, "?")
            color = {"incoming": "#34a853", "outgoing": "#1a73e8", "missed": "#ea4335"}.get(e.direction, "#9aa0a6")

            try:
                dt = datetime.fromisoformat(e.started_at.replace("Z", "+00:00"))
                ts = dt.astimezone().strftime("%d %b %Y  %H:%M")
            except Exception:
                ts = e.started_at[:16]

            if e.duration_sec >= 3600:
                dur = f"{e.duration_sec // 3600}h {(e.duration_sec % 3600) // 60}m"
            elif e.duration_sec >= 60:
                dur = f"{e.duration_sec // 60}m {e.duration_sec % 60:02d}s"
            elif e.duration_sec > 0:
                dur = f"{e.duration_sec}s"
            else:
                dur = "—"

            text = f"{icon}  {ts}    {dur}"
            item = QListWidgetItem(text)
            item.setForeground(QColor(color))
            self._history_list.addItem(item)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_call(self, checked: bool = False):
        if self._number:
            self.accept()
            self._dial_cb(self._number)

    def _on_edit_name(self, checked: bool = False):
        current = self._contact.custom_name or self._contact.display_name if self._contact else ""
        new_name, ok = QInputDialog.getText(
            self, "Edit Name", "Name:", text=current
        )
        if ok and new_name.strip():
            if self._contact and self._contact.id:
                self._store.rename_contact(self._contact.id, new_name.strip())
                self._contact.custom_name = new_name.strip()
            self._name_label.setText(self._display_name())

    def _on_change_photo(self, checked: bool = False):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Photo", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        if not path:
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
            if self._contact and self._contact.id:
                self._store.set_contact_photo(self._contact.id, data)
                self._contact.raw_vcard = None   # force re-read from DB
                self._contact = self._store.get_contact_by_id(self._contact.id)
            self._load_avatar()
        except Exception as e:
            logger.warning("Failed to load photo: %s", e)


# ── Inline panel (used in split-view contacts tab) ───────────────────────────

class ContactProfilePanel(QWidget):
    """Inline contact profile — shown on the right side of the contacts split view."""

    dial_requested = pyqtSignal(str)
    contact_changed = pyqtSignal()   # emitted after rename / photo change

    _AVATAR_SIZE = 72

    def __init__(self, store: ContactStore, dial_cb=None, parent=None):
        super().__init__(parent)
        self._store = store
        self._dial_cb = dial_cb
        self._contact: Optional[Contact] = None
        self._number: str = ""
        self._build_ui()
        self._show_placeholder()

    back_requested = pyqtSignal()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # Back button
        btn_back = QPushButton("← Back")
        btn_back.setFixedWidth(80)
        btn_back.clicked.connect(self.back_requested)
        root.addWidget(btn_back, 0, Qt.AlignmentFlag.AlignLeft)

        # Avatar
        self._avatar_label = QLabel()
        self._avatar_label.setFixedSize(self._AVATAR_SIZE, self._AVATAR_SIZE)
        self._avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._avatar_label, 0, Qt.AlignmentFlag.AlignHCenter)

        # Name
        self._name_label = QLabel()
        self._name_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._name_label.setWordWrap(True)
        self._name_label.setStyleSheet("font-size: 15px; font-weight: bold;")
        root.addWidget(self._name_label)

        # Number
        self._num_label = QLabel()
        self._num_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._num_label.setStyleSheet("font-size: 12px; color: #9aa0a6;")
        root.addWidget(self._num_label)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self._btn_call = QPushButton("Call")
        self._btn_call.setStyleSheet(
            "background: #34a853; color: white; border-radius: 6px; padding: 6px 12px;"
        )
        self._btn_call.clicked.connect(self._on_call)
        btn_row.addWidget(self._btn_call)

        self._btn_fav = QPushButton("☆")
        self._btn_fav.setFixedWidth(36)
        self._btn_fav.setToolTip("Add to favourites")
        self._btn_fav.clicked.connect(self._on_toggle_favorite)
        btn_row.addWidget(self._btn_fav)

        self._btn_edit = QPushButton("Edit name")
        self._btn_edit.clicked.connect(self._on_edit_name)
        btn_row.addWidget(self._btn_edit)

        self._btn_photo = QPushButton("Photo")
        self._btn_photo.clicked.connect(self._on_change_photo)
        btn_row.addWidget(self._btn_photo)
        root.addLayout(btn_row)

        # Call history
        hist_lbl = _section("Call history")
        root.addWidget(hist_lbl)

        self._history_list = QListWidget()
        self._history_list.setFrameShape(QListWidget.Shape.NoFrame)
        self._history_list.setStyleSheet(
            "QListWidget::item { padding: 4px 2px; border-bottom: 1px solid rgba(127,127,127,0.15); }"
        )
        root.addWidget(self._history_list, 1)

        self._placeholder = QLabel("Select a contact")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("color: #9aa0a6; font-size: 13px;")
        root.addWidget(self._placeholder)

    def load_contact(self, contact: Optional[Contact], number: str = ""):
        self._contact = contact
        self._number = number or (contact.phone_number if contact else "")
        if not self._contact and not self._number:
            self._show_placeholder()
            return
        self._placeholder.setVisible(False)
        self._avatar_label.setVisible(True)
        self._name_label.setVisible(True)
        self._num_label.setVisible(True)
        self._btn_call.setVisible(True)
        self._btn_fav.setVisible(True)
        self._btn_edit.setVisible(True)
        self._btn_photo.setVisible(True)
        self._history_list.setVisible(True)

        self._name_label.setText(contact.effective_name if contact else self._number)
        self._num_label.setText(self._number)
        self._update_fav_btn()
        self._load_avatar()
        self._load_history()

    def _show_placeholder(self):
        self._avatar_label.setVisible(False)
        self._name_label.setVisible(False)
        self._num_label.setVisible(False)
        self._btn_call.setVisible(False)
        self._btn_fav.setVisible(False)
        self._btn_edit.setVisible(False)
        self._btn_photo.setVisible(False)
        self._history_list.setVisible(False)
        self._placeholder.setVisible(True)

    def _load_avatar(self):
        photo_bytes = None
        if self._contact and self._contact.photo_data:
            photo_bytes = self._contact.photo_data
        elif self._contact and self._contact.raw_vcard:
            photo_bytes = _extract_vcard_photo(self._contact.raw_vcard)
        s = self._AVATAR_SIZE
        if photo_bytes:
            px = QPixmap()
            if px.loadFromData(photo_bytes):
                px = px.scaled(s, s, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                               Qt.TransformationMode.SmoothTransformation)
                self._avatar_label.setPixmap(px.copy(0, 0, s, s))
                self._avatar_label.setStyleSheet(f"border-radius: {s//2}px;")
                return
        initials = _initials(self._contact.effective_name if self._contact else self._number)
        self._avatar_label.setText(initials)
        self._avatar_label.setStyleSheet(
            f"border-radius: {s//2}px; background: #3c4043; border: 2px solid #5f6368;"
            "font-size: 24px; font-weight: bold; color: white;"
        )

    def _load_history(self):
        self._history_list.clear()
        if not self._number:
            return
        contact_id = self._contact.id if self._contact else None
        entries = self._store.get_call_log_for_number(self._number, limit=30, contact_id=contact_id)
        if not entries:
            item = QListWidgetItem("No call history")
            item.setForeground(QColor("#5f6368"))
            self._history_list.addItem(item)
            return
        for e in entries:
            icon  = {"incoming": "↙", "outgoing": "↗", "missed": "✗"}.get(e.direction, "?")
            color = {"incoming": "#34a853", "outgoing": "#1a73e8", "missed": "#ea4335"}.get(e.direction, "#9aa0a6")
            try:
                dt = datetime.fromisoformat(e.started_at.replace("Z", "+00:00"))
                ts = dt.astimezone().strftime("%d %b  %H:%M")
            except Exception:
                ts = e.started_at[:10]
            if e.duration_sec >= 60:
                dur = f"{e.duration_sec // 60}m {e.duration_sec % 60:02d}s"
            elif e.duration_sec > 0:
                dur = f"{e.duration_sec}s"
            else:
                dur = "—"
            item = QListWidgetItem(f"{icon}  {ts}  {dur}")
            item.setForeground(QColor(color))
            self._history_list.addItem(item)

    def _update_fav_btn(self):
        is_fav = self._contact.is_favorite if self._contact else False
        self._btn_fav.setText("★" if is_fav else "☆")
        self._btn_fav.setToolTip("Remove from favourites" if is_fav else "Add to favourites")
        self._btn_fav.setStyleSheet(
            "color: #f0a500; font-size: 16px;" if is_fav else "font-size: 16px;"
        )

    def _on_toggle_favorite(self):
        if not self._contact or not self._contact.id:
            return
        new_state = self._store.toggle_favorite(self._contact.id)
        self._contact = self._store.get_contact_by_id(self._contact.id)
        self._update_fav_btn()
        self.contact_changed.emit()

    def _on_call(self):
        if self._number:
            if self._dial_cb:
                self._dial_cb(self._number)
            else:
                self.dial_requested.emit(self._number)

    def _on_edit_name(self):
        if not self._contact:
            return
        current = self._contact.custom_name or self._contact.display_name
        new_name, ok = QInputDialog.getText(self, "Edit Name", "Name:", text=current)
        if ok and new_name.strip():
            self._store.rename_contact(self._contact.id, new_name.strip())
            self._contact = self._store.get_contact_by_id(self._contact.id)
            self._name_label.setText(self._contact.effective_name)
            self.contact_changed.emit()

    def _on_change_photo(self):
        if not self._contact:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Photo", "", "Images (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        if not path:
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
            self._store.set_contact_photo(self._contact.id, data)
            self._contact = self._store.get_contact_by_id(self._contact.id)
            self._load_avatar()
            self.contact_changed.emit()
        except Exception as e:
            logger.warning("Failed to load photo: %s", e)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _section(title: str) -> QLabel:
    lbl = QLabel(title)
    lbl.setStyleSheet(
        "font-size: 11px; font-weight: bold; color: #9aa0a6; "
        "text-transform: uppercase; letter-spacing: 1px;"
    )
    return lbl


def _initials(name: str) -> str:
    words = name.split()
    if len(words) >= 2:
        return (words[0][0] + words[-1][0]).upper()
    return name[:2].upper() if name else "?"


def _extract_vcard_photo(raw_vcard: str) -> Optional[bytes]:
    """Extract PHOTO base64 data from a vCard string."""
    try:
        # Unfold lines (vCard line folding: CRLF + whitespace)
        unfolded = re.sub(r"\r?\n[ \t]", "", raw_vcard)
        for line in unfolded.splitlines():
            upper = line.upper()
            if upper.startswith("PHOTO"):
                # Could be PHOTO;ENCODING=BASE64;TYPE=JPEG:...
                # or PHOTO;BASE64:... or PHOTO;TYPE=JPEG;ENCODING=b:...
                colon = line.find(":")
                if colon < 0:
                    continue
                b64 = line[colon + 1:].strip()
                if b64:
                    return base64.b64decode(b64 + "==")
    except Exception:
        pass
    return None
