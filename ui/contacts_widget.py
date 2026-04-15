"""
Contacts list widget — shows all stored contacts with search + rename/delete.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMenu, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from contacts.models import Contact
from contacts.store import ContactStore
from ui.contact_profile import ContactProfileDialog

logger = logging.getLogger(__name__)


class ContactsWidget(QWidget):
    """Embeddable contacts list with search and context menu actions."""

    dial_requested = pyqtSignal(str)  # Emitted with phone number when user clicks Dial

    def __init__(self, store: ContactStore, dial_cb=None, parent=None):
        super().__init__(parent)
        self._store = store
        self._dial_cb = dial_cb or self.dial_requested.emit
        self._contacts: list[Contact] = []
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Search bar
        search_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search contacts…")
        self._search.textChanged.connect(self._on_search)
        search_row.addWidget(self._search)

        btn_refresh = QPushButton("Sync")
        btn_refresh.setToolTip("Sync contacts from phone via PBAP")
        btn_refresh.setFixedWidth(60)
        btn_refresh.clicked.connect(self.refresh)
        search_row.addWidget(btn_refresh)
        layout.addLayout(search_row)

        # Count label
        self._count_label = QLabel("0 contacts")
        self._count_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self._count_label)

        # Contact list
        self._list = QListWidget()
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._show_context_menu)
        self._list.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self._list)

        # Bottom action buttons
        btn_row = QHBoxLayout()
        btn_add = QPushButton("Add Contact")
        btn_add.clicked.connect(self._on_add_contact)
        btn_row.addWidget(btn_add)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def refresh(self):
        """Reload contacts from the database."""
        query = self._search.text().strip()
        if query:
            self._contacts = self._store.search(query)
        else:
            self._contacts = self._store.get_all()
        self._populate_list(self._contacts)

    def _on_search(self, text: str):
        self.refresh()

    def _populate_list(self, contacts: list[Contact]):
        self._list.clear()
        for c in contacts:
            item = QListWidgetItem()
            item.setText(f"{c.effective_name}  ·  {c.phone_number}")
            item.setData(Qt.ItemDataRole.UserRole, c)
            if c.custom_name:
                item.setToolTip(f"Renamed from: {c.display_name}")
            self._list.addItem(item)
        total = self._store.count()
        self._count_label.setText(f"{total} contact{'s' if total != 1 else ''}")

    def _selected_contact(self) -> Optional[Contact]:
        item = self._list.currentItem()
        if not item:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    # ── Context menu ──────────────────────────────────────────────────────────

    def _show_context_menu(self, pos):
        item = self._list.itemAt(pos)
        if not item:
            return
        contact: Contact = item.data(Qt.ItemDataRole.UserRole)

        menu = QMenu(self)
        act_profile = menu.addAction(f"Open profile")
        act_dial = menu.addAction(f"Call {contact.effective_name}")
        menu.addSeparator()
        act_rename = menu.addAction("Rename…")
        if contact.custom_name:
            act_clear = menu.addAction("Clear custom name")
        else:
            act_clear = None
        menu.addSeparator()
        act_delete = menu.addAction("Delete contact")

        action = menu.exec(self._list.mapToGlobal(pos))
        if action == act_profile:
            self._open_profile(contact)
        elif action == act_dial:
            self.dial_requested.emit(contact.phone_number)
        elif action == act_rename:
            self._rename_contact(contact)
        elif act_clear and action == act_clear:
            self._store.rename_contact(contact.id, None)
            self.refresh()
        elif action == act_delete:
            self._delete_contact(contact)

    def _on_double_click(self, item: QListWidgetItem):
        contact: Contact = item.data(Qt.ItemDataRole.UserRole)
        self._open_profile(contact)

    def _open_profile(self, contact: Contact):
        dlg = ContactProfileDialog(
            store=self._store,
            dial_cb=self._dial_cb,
            contact=contact,
            parent=self,
        )
        dlg.exec()
        self.refresh()   # name/photo may have changed

    # ── Actions ───────────────────────────────────────────────────────────────

    def _rename_contact(self, contact: Contact):
        new_name, ok = QInputDialog.getText(
            self,
            "Rename Contact",
            f"New name for {contact.display_name}:",
            text=contact.custom_name or contact.display_name,
        )
        if ok and new_name.strip():
            self._store.rename_contact(contact.id, new_name.strip())
            self.refresh()

    def _delete_contact(self, contact: Contact):
        reply = QMessageBox.question(
            self,
            "Delete Contact",
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
