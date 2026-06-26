"""
Contacts list widget — avatar bubble + name, single-click opens profile.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QKeySequence, QPainter, QPainterPath, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMenu, QMessageBox, QPushButton, QSplitter, QVBoxLayout, QWidget,
)

from contacts.models import Contact
from contacts.store import ContactStore
from ui.contact_profile import ContactProfileDialog, ContactProfilePanel

logger = logging.getLogger(__name__)

# Palette for initials bubbles — cycles by hash of name
_BUBBLE_COLORS = [
    "#4a90d9", "#7b68ee", "#e06c75", "#56b6c2",
    "#98c379", "#d19a66", "#c678dd", "#61afef",
]

_AVATAR_SIZE = 42   # px — diameter of the bubble


def _make_initials(name: str) -> str:
    """Return up to 2 initials from a display name, or last 4 digits for phone numbers."""
    words = name.strip().split()
    if not words or not words[0]:
        return "?"
    # Phone number (all digits/+/-) — show last 4 digits in the bubble
    digits = name.replace("+", "").replace("-", "").replace(" ", "")
    if digits.isdigit():
        return digits[-4:]
    if len(words) == 1:
        return words[0][0].upper()
    return (words[0][0] + words[-1][0]).upper()


def _bubble_color(name: str) -> str:
    return _BUBBLE_COLORS[hash(name) % len(_BUBBLE_COLORS)]


def _avatar_pixmap(photo_data: bytes | None, name: str, size: int) -> QPixmap:
    """
    Build a circular QPixmap.
    - If photo_data: decode, centre-crop to square, clip to circle.
    - Otherwise: solid colour + 2-letter initials.
    """
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Clip all drawing to a circle
    path = QPainterPath()
    path.addEllipse(0, 0, size, size)
    painter.setClipPath(path)

    if photo_data:
        src = QPixmap()
        src.loadFromData(photo_data)
        if not src.isNull():
            # Centre-crop to square
            side = min(src.width(), src.height())
            x = (src.width()  - side) // 2
            y = (src.height() - side) // 2
            src = src.copy(x, y, side, side).scaled(
                size, size,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.drawPixmap(0, 0, src)
            painter.end()
            return pm
        # fall through to initials if decode failed

    # Initials bubble
    painter.setBrush(QColor(_bubble_color(name)))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRect(0, 0, size, size)

    painter.setPen(QColor("white"))
    font = QFont()
    font.setPointSize(int(size * 0.32))
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(0, 0, size, size, Qt.AlignmentFlag.AlignCenter, _make_initials(name))

    painter.end()
    return pm


class _ContactRow(QWidget):
    """Single row: avatar bubble + name label."""

    def __init__(self, contact: Contact, parent=None):
        super().__init__(parent)
        self.contact = contact
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(12)

        # Avatar — transparent background so the circle is truly circular
        avatar = QLabel()
        avatar.setStyleSheet("background: transparent; border: none;")
        pm = _avatar_pixmap(contact.photo_data, contact.effective_name, _AVATAR_SIZE)
        avatar.setPixmap(pm)
        avatar.setFixedSize(_AVATAR_SIZE, _AVATAR_SIZE)
        layout.addWidget(avatar)

        # Name + optional favourite star
        name_lbl = QLabel(contact.effective_name)
        name_lbl.setStyleSheet("background: transparent; border: none;")
        font = name_lbl.font()
        font.setPointSize(11)
        name_lbl.setFont(font)
        layout.addWidget(name_lbl, 1)

        if contact.is_favorite:
            star = QLabel("★")
            star.setStyleSheet("color: #f0a500; background: transparent; border: none; font-size: 14px;")
            layout.addWidget(star)


class ContactsWidget(QWidget):
    """Embeddable contacts list with search and context menu actions."""

    dial_requested = pyqtSignal(str)

    def __init__(self, store: ContactStore, dial_cb=None, parent=None):
        super().__init__(parent)
        self._store = store
        self._dial_cb = dial_cb or self.dial_requested.emit
        self._contacts: list[Contact] = []
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setChildrenCollapsible(True)

        # ── Left: list pane ───────────────────────────────────────────────────
        left = QWidget()
        layout = QVBoxLayout(left)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(4)

        search_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search contacts…")
        self._search.textChanged.connect(self._on_search)
        search_row.addWidget(self._search)

        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self._focus_search)
        btn_refresh = QPushButton("Sync")
        btn_refresh.setToolTip("Sync contacts from phone via PBAP")
        btn_refresh.setFixedWidth(60)
        btn_refresh.clicked.connect(self.refresh)
        search_row.addWidget(btn_refresh)
        layout.addLayout(search_row)

        self._count_label = QLabel("0 contacts")
        self._count_label.setObjectName("hintLabel")
        layout.addWidget(self._count_label)

        self._list = QListWidget()
        self._list.setSpacing(2)
        self._list.setFrameShape(QListWidget.Shape.NoFrame)
        self._list.setStyleSheet("""
            QListWidget { border: none; outline: none; }
            QListWidget::item {
                border: none;
                border-radius: 8px;
                margin: 1px 6px;
            }
            QListWidget::item:selected,
            QListWidget::item:hover {
                background: rgba(127,127,127,0.12);
            }
        """)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._show_context_menu)
        self._list.itemClicked.connect(self._on_click)
        layout.addWidget(self._list)

        btn_row = QHBoxLayout()
        btn_add = QPushButton("Add Contact")
        btn_add.clicked.connect(self._on_add_contact)
        btn_row.addWidget(btn_add)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        splitter.addWidget(left)

        # ── Right: profile panel ──────────────────────────────────────────────
        self._profile_panel = ContactProfilePanel(
            store=self._store, dial_cb=self._dial_cb
        )
        self._profile_panel.contact_changed.connect(self.refresh)
        self._profile_panel.back_requested.connect(self._on_back)
        splitter.addWidget(self._profile_panel)

        self._splitter = splitter
        self._profile_panel.hide()
        splitter.setSizes([500, 0])
        root.addWidget(splitter)

    def refresh(self):
        query = self._search.text().strip()
        self._contacts = self._store.search(query) if query else self._store.get_all()
        self._populate_list(self._contacts)

    def _on_search(self, _text: str):
        self.refresh()

    def _populate_list(self, contacts: list[Contact]):
        self._list.clear()
        for c in contacts:
            item = QListWidgetItem(self._list)
            row = _ContactRow(c)
            item.setSizeHint(QSize(0, _AVATAR_SIZE + 12))
            item.setData(Qt.ItemDataRole.UserRole, c)
            self._list.addItem(item)
            self._list.setItemWidget(item, row)
        total = self._store.count()
        self._count_label.setText(f"{total} contact{'s' if total != 1 else ''}")

    def _contact_from_item(self, item: QListWidgetItem) -> Optional[Contact]:
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    # ── Interactions ──────────────────────────────────────────────────────────

    def _focus_search(self):
        self._search.setFocus()
        self._search.selectAll()

    def _on_back(self):
        self._profile_panel.hide()
        self._splitter.setSizes([500, 0])
        self._list.clearSelection()

    def _on_click(self, item: QListWidgetItem):
        contact = self._contact_from_item(item)
        if contact:
            self._profile_panel.show()
            self._profile_panel.load_contact(contact)
            self._splitter.setSizes([190, 310])

    # ── Context menu ──────────────────────────────────────────────────────────

    def _show_context_menu(self, pos):
        item = self._list.itemAt(pos)
        if not item:
            return
        contact: Contact = self._contact_from_item(item)

        menu = QMenu(self)
        act_profile = menu.addAction("Open profile")
        act_dial    = menu.addAction(f"Call {contact.effective_name}")
        menu.addSeparator()
        fav_label   = "★  Remove from favourites" if contact.is_favorite else "☆  Add to favourites"
        act_fav     = menu.addAction(fav_label)
        menu.addSeparator()
        act_rename  = menu.addAction("Rename…")
        act_clear   = menu.addAction("Clear custom name") if contact.custom_name else None
        menu.addSeparator()
        act_delete  = menu.addAction("Delete contact")

        action = menu.exec(self._list.mapToGlobal(pos))
        if action == act_profile:
            self._profile_panel.show()
            self._profile_panel.load_contact(contact)
            self._splitter.setSizes([190, 310])
        elif action == act_dial:
            self.dial_requested.emit(contact.phone_number)
        elif action == act_fav:
            self._store.toggle_favorite(contact.id)
            self.refresh()
        elif action == act_rename:
            self._rename_contact(contact)
        elif act_clear and action == act_clear:
            self._store.rename_contact(contact.id, None)
            self.refresh()
        elif action == act_delete:
            self._delete_contact(contact)

    # ── Actions ───────────────────────────────────────────────────────────────

    def _rename_contact(self, contact: Contact):
        new_name, ok = QInputDialog.getText(
            self, "Rename Contact",
            f"New name for {contact.display_name}:",
            text=contact.custom_name or contact.display_name,
        )
        if ok and new_name.strip():
            self._store.rename_contact(contact.id, new_name.strip())
            self.refresh()

    def _delete_contact(self, contact: Contact):
        reply = QMessageBox.question(
            self, "Delete Contact",
            f"Delete {contact.effective_name}?\n"
            "This only removes the local copy — not from your phone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._store.delete_contact(contact.id)
            self.refresh()

    def _on_add_contact(self):
        dlg = _AddContactDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            name, number = dlg.get_values()
            if name and number:
                from contacts.models import Contact as C
                import time
                c = C(
                    phone_uid=f"__manual_{name}_{number}__",
                    display_name=name,
                    phone_number=number,
                    phone_number_normalized=C.normalize_number(number),
                    last_synced=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                )
                self._store.upsert_contact(c)
                self.refresh()


class _AddContactDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Contact")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Name:"))
        self._name = QLineEdit()
        layout.addWidget(self._name)
        layout.addWidget(QLabel("Phone number:"))
        self._number = QLineEdit()
        self._number.setPlaceholderText("+1234567890")
        layout.addWidget(self._number)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_values(self) -> tuple[str, str]:
        return self._name.text().strip(), self._number.text().strip()
