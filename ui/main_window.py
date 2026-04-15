"""
Main application window — contacts list, call log, device status, dial pad.
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import Qt, QRectF, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QFont, QKeySequence, QPainter, QPen, QShortcut
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFrame, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QPushButton, QSlider, QSplitter, QStackedWidget,
    QStatusBar, QTabWidget, QVBoxLayout, QWidget,
)

from contacts.store import ContactStore

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """
    Main application window.
    Kept hidden until the user clicks the tray icon or opens from menu.
    """

    dial_requested       = pyqtSignal(str)
    sync_requested       = pyqtSignal()
    connect_requested    = pyqtSignal(str)   # device_path
    disconnect_requested = pyqtSignal()
    hangup_requested     = pyqtSignal()
    mute_requested       = pyqtSignal(bool)  # True = mute on

    # Settings signals
    audio_output_changed = pyqtSignal(str)   # pactl sink name
    audio_input_changed  = pyqtSignal(str)   # pactl source name
    volume_changed       = pyqtSignal(int)   # 0-100

    def __init__(self, store: ContactStore, parent=None):
        super().__init__(parent)
        self._store = store
        self._devices: list[dict] = []

        self.setWindowTitle("HandsFree")
        self.setMinimumSize(520, 580)
        self.resize(640, 680)
        self._apply_style()
        self._build_ui()

        # Close to tray, not quit
        self.closeEvent = lambda e: (e.ignore(), self.hide())

        # Esc to hide
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self).activated.connect(self.hide)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(12, 12, 12, 4)

        # Status bar at top
        self._status_group = self._build_status_group()
        main_layout.addWidget(self._status_group)

        # Active-call banner (hidden when no call)
        self._call_banner = self._build_call_banner()
        main_layout.addWidget(self._call_banner)

        # Tabs: Contacts | Dial | Call Log
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        main_layout.addWidget(self._tabs)

        # ── Contacts tab ──
        from ui.contacts_widget import ContactsWidget
        self._contacts_widget = ContactsWidget(self._store, dial_cb=self.dial_requested.emit)
        self._contacts_widget.dial_requested.connect(self.dial_requested)
        self._tabs.addTab(self._contacts_widget, "Contacts")

        # ── Dial pad tab ──
        self._tabs.addTab(self._build_dial_tab(), "Dial")

        # ── Call log tab ──
        self._tabs.addTab(self._build_calllog_tab(), "Call Log")
        self._tabs.currentChanged.connect(self._on_tab_changed)

        # ── Settings tab ──
        self._tabs.addTab(self._build_settings_tab(), "Settings")

        # Status bar
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)
        self._statusbar.showMessage("Ready")

        # Floating call overlay (always-on-top, visible even when window minimised)
        from ui.call_overlay import CallOverlay
        self._call_overlay = CallOverlay()
        self._call_overlay.hangup_requested.connect(self.hangup_requested)
        self._call_overlay.mute_toggled.connect(self.mute_requested)

    def _build_call_banner(self) -> QWidget:
        """Red banner shown during an active call — number, timer, End Call button."""
        banner = QWidget()
        banner.setObjectName("callBanner")
        banner.setStyleSheet("""
            #callBanner {
                background: #3a1010;
                border: 1px solid #ea4335;
                border-radius: 6px;
            }
        """)
        row = QHBoxLayout(banner)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(12)

        # Red phone icon label
        icon_lbl = QLabel("📞")
        icon_lbl.setStyleSheet("font-size: 18px; background: transparent;")
        row.addWidget(icon_lbl)

        # Number + duration
        info_col = QVBoxLayout()
        info_col.setSpacing(2)
        self._call_banner_number = QLabel("Call active")
        self._call_banner_number.setStyleSheet(
            "font-size: 14px; font-weight: bold; color: #f28b82; background: transparent;"
        )
        info_col.addWidget(self._call_banner_number)
        self._call_banner_timer = QLabel("0:00")
        self._call_banner_timer.setStyleSheet(
            "font-size: 12px; color: #9aa0a6; background: transparent;"
        )
        info_col.addWidget(self._call_banner_timer)
        row.addLayout(info_col)

        row.addStretch()

        # End Call button
        btn_end = QPushButton("  End Call")
        btn_end.setFixedHeight(38)
        btn_end.setMinimumWidth(110)
        btn_end.setStyleSheet("""
            QPushButton {
                background: #ea4335; color: white;
                border-radius: 19px; font-size: 14px; font-weight: bold;
            }
            QPushButton:hover   { background: #c5352a; }
            QPushButton:pressed { background: #a02820; }
        """)
        btn_end.clicked.connect(self.hangup_requested)
        row.addWidget(btn_end)

        # Timer state
        self._call_elapsed_sec = 0
        self._call_timer = QTimer()
        self._call_timer.setInterval(1000)
        self._call_timer.timeout.connect(self._tick_call_timer)

        banner.setVisible(False)
        return banner

    def _tick_call_timer(self):
        self._call_elapsed_sec += 1
        m, s = divmod(self._call_elapsed_sec, 60)
        h, m = divmod(m, 60)
        text = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
        self._call_banner_timer.setText(text)
        self._incall_timer_lbl.setText(text)
        self._call_overlay.update_timer(text)

    def _build_status_group(self) -> QGroupBox:
        group = QGroupBox("Connection")
        layout = QHBoxLayout(group)

        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet("color: #888; font-size: 16px;")
        layout.addWidget(self._status_dot)

        self._status_label = QLabel("Not connected")
        self._status_label.setStyleSheet("font-size: 13px;")
        layout.addWidget(self._status_label)

        layout.addStretch()

        self._device_combo = QComboBox()
        self._device_combo.setMinimumWidth(200)
        self._device_combo.setPlaceholderText("Select device…")
        layout.addWidget(self._device_combo)

        self._btn_connect = QPushButton("Connect")
        self._btn_connect.clicked.connect(self._on_connect_clicked)
        layout.addWidget(self._btn_connect)

        self._btn_disconnect = QPushButton("Disconnect")
        self._btn_disconnect.setVisible(False)
        self._btn_disconnect.clicked.connect(self.disconnect_requested)
        layout.addWidget(self._btn_disconnect)

        self._btn_sync = QPushButton("Sync Contacts")
        self._btn_sync.setVisible(False)
        self._btn_sync.clicked.connect(self.sync_requested)
        layout.addWidget(self._btn_sync)

        return group

    def _build_dial_tab(self) -> QWidget:
        """
        Stacked widget with two pages:
          0 — dial pad (default)
          1 — in-call view (shown when a call is active)
        """
        self._dial_stack = QStackedWidget()
        self._dial_stack.addWidget(self._build_dialpad_page())    # page 0
        self._dial_stack.addWidget(self._build_incall_page())     # page 1
        return self._dial_stack

    # ── Page 0: dial pad ──────────────────────────────────────────────────────

    def _build_dialpad_page(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        input_row = QHBoxLayout()
        self._dial_input = QLineEdit()
        self._dial_input.setPlaceholderText("0899 123 456")
        self._dial_input.setMinimumHeight(42)
        self._dial_input.setStyleSheet(
            "font-size: 20px; letter-spacing: 2px; padding: 4px 10px;"
        )
        self._dial_input.returnPressed.connect(self._on_dial)
        input_row.addWidget(self._dial_input)

        btn_del = QPushButton("⌫")
        btn_del.setFixedSize(44, 42)
        btn_del.setStyleSheet("font-size: 18px; border: none; color: #9aa0a6;")
        btn_del.clicked.connect(self._on_del_digit)
        input_row.addWidget(btn_del)
        layout.addLayout(input_row)

        from PyQt6.QtWidgets import QGridLayout
        numpad = QWidget()
        pad = QGridLayout(numpad)
        pad.setSpacing(6)
        pad.setContentsMargins(0, 4, 0, 4)

        KEYS = [
            ("1", ""),   ("2", "ABC"),  ("3", "DEF"),
            ("4", "GHI"),("5", "JKL"),  ("6", "MNO"),
            ("7", "PQRS"),("8", "TUV"), ("9", "WXYZ"),
            ("*", ""),   ("0", "+"),    ("#", ""),
        ]
        for i, (digit, sub) in enumerate(KEYS):
            row, col = divmod(i, 3)
            btn = QPushButton()
            btn.setFixedSize(80, 52)
            btn.setStyleSheet("""
                QPushButton {
                    border: 1px solid #3c4043; border-radius: 6px;
                    font-size: 20px; font-weight: bold; color: #e8eaed;
                    background: #2d2d2f;
                }
                QPushButton:hover  { background: #3c3c3e; }
                QPushButton:pressed{ background: #4a4a4c; }
            """)
            btn_layout = QVBoxLayout(btn)
            btn_layout.setContentsMargins(0, 4, 0, 4)
            btn_layout.setSpacing(0)
            lbl_main = QLabel(digit)
            lbl_main.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl_main.setStyleSheet(
                "font-size: 20px; font-weight: bold; color: #e8eaed; background: transparent;"
            )
            btn_layout.addWidget(lbl_main)
            if sub:
                lbl_sub = QLabel(sub)
                lbl_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
                lbl_sub.setStyleSheet(
                    "font-size: 9px; color: #9aa0a6; background: transparent;"
                )
                btn_layout.addWidget(lbl_sub)
            btn.clicked.connect(self._make_digit_handler(digit))
            pad.addWidget(btn, row, col)

        layout.addWidget(numpad, alignment=Qt.AlignmentFlag.AlignHCenter)

        btn_call = QPushButton("  Call")
        btn_call.setFixedHeight(48)
        btn_call.setStyleSheet("""
            QPushButton {
                background: #34a853; color: white; border-radius: 24px;
                font-size: 16px; font-weight: bold;
            }
            QPushButton:hover  { background: #2d9249; }
            QPushButton:pressed{ background: #1e7a3d; }
        """)
        btn_call.clicked.connect(self._on_dial)
        layout.addWidget(btn_call)
        layout.addStretch()
        return w

    def _make_digit_handler(self, digit: str):
        """Return a slot that appends digit to the input (avoids late-binding closure bug)."""
        def _handler(checked: bool = False):
            self._dial_input.setText(self._dial_input.text() + digit)
        return _handler

    def _on_del_digit(self, checked: bool = False):
        self._dial_input.setText(self._dial_input.text()[:-1])

    # ── Page 1: in-call view ──────────────────────────────────────────────────

    def _build_incall_page(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: #1c1c1e;")
        layout = QVBoxLayout(w)
        layout.setContentsMargins(24, 32, 24, 24)
        layout.setSpacing(0)
        layout.addStretch(1)

        # Avatar
        self._incall_avatar = QLabel()
        self._incall_avatar.setFixedSize(88, 88)
        self._incall_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._incall_avatar.setStyleSheet(
            "border-radius: 44px; background: #3c4043; border: 2px solid #5f6368;"
            "font-size: 32px; font-weight: bold; color: white;"
        )
        self._incall_avatar.setText("?")
        layout.addWidget(self._incall_avatar, alignment=Qt.AlignmentFlag.AlignHCenter)

        layout.addSpacing(14)

        # Contact name (large) + number (small, below)
        self._incall_number_lbl = QLabel("")
        self._incall_number_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._incall_number_lbl.setStyleSheet(
            "font-size: 22px; font-weight: bold; color: #e8eaed; background: transparent;"
        )
        layout.addWidget(self._incall_number_lbl)

        self._incall_number_sub = QLabel("")   # raw number shown when name is known
        self._incall_number_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._incall_number_sub.setStyleSheet("font-size: 13px; color: #9aa0a6; background: transparent;")
        layout.addWidget(self._incall_number_sub)

        layout.addSpacing(6)

        # Duration timer
        self._incall_timer_lbl = QLabel("0:00")
        self._incall_timer_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._incall_timer_lbl.setStyleSheet(
            "font-size: 16px; color: #9aa0a6; background: transparent;"
        )
        layout.addWidget(self._incall_timer_lbl)

        layout.addSpacing(32)

        # Volume row  − [ ████░░ ] +
        vol_row = QHBoxLayout()
        vol_row.setSpacing(10)

        btn_vol_dn = QPushButton("−")
        btn_vol_dn.setFixedSize(44, 44)
        btn_vol_dn.setStyleSheet(
            "QPushButton{font-size:22px;font-weight:bold;"
            "border-radius:22px;background:#2d2d2f;color:#e8eaed;}"
            "QPushButton:hover{background:#3c4043;}"
        )
        btn_vol_dn.clicked.connect(self._on_incall_vol_down)
        vol_row.addWidget(btn_vol_dn)

        self._incall_vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._incall_vol_slider.setRange(0, 100)
        self._incall_vol_slider.setValue(80)
        self._incall_vol_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                background: #3c4043; height: 6px; border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: #34a853; height: 6px; border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: white; width: 18px; height: 18px;
                margin: -6px 0; border-radius: 9px;
            }
        """)
        self._incall_vol_slider.valueChanged.connect(self._on_incall_vol_slider)
        vol_row.addWidget(self._incall_vol_slider, 1)

        btn_vol_up = QPushButton("+")
        btn_vol_up.setFixedSize(44, 44)
        btn_vol_up.setStyleSheet(
            "QPushButton{font-size:22px;font-weight:bold;"
            "border-radius:22px;background:#2d2d2f;color:#e8eaed;}"
            "QPushButton:hover{background:#3c4043;}"
        )
        btn_vol_up.clicked.connect(self._on_incall_vol_up)
        vol_row.addWidget(btn_vol_up)

        self._incall_vol_lbl = QLabel("80%")
        self._incall_vol_lbl.setFixedWidth(40)
        self._incall_vol_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._incall_vol_lbl.setStyleSheet("color:#9aa0a6; background:transparent;")
        vol_row.addWidget(self._incall_vol_lbl)

        layout.addLayout(vol_row)
        layout.addSpacing(20)

        # ── Audio device pickers ──────────────────────────────────────────────
        dev_style = (
            "QLabel{color:#9aa0a6;font-size:11px;background:transparent;}"
            "QComboBox{background:#2d2d2f;color:#e8eaed;border:1px solid #5f6368;"
            "border-radius:6px;padding:4px 8px;font-size:12px;}"
            "QComboBox::drop-down{border:none;}"
            "QComboBox QAbstractItemView{background:#2d2d2f;color:#e8eaed;}"
        )
        dev_widget = QWidget()
        dev_widget.setStyleSheet(dev_style)
        dev_layout = QVBoxLayout(dev_widget)
        dev_layout.setContentsMargins(0, 0, 0, 0)
        dev_layout.setSpacing(6)

        out_row = QHBoxLayout()
        out_lbl = QLabel("Speaker:")
        out_lbl.setFixedWidth(72)
        out_row.addWidget(out_lbl)
        self._incall_output_combo = QComboBox()
        self._incall_output_combo.currentIndexChanged.connect(self._on_incall_output_changed)
        out_row.addWidget(self._incall_output_combo, 1)
        dev_layout.addLayout(out_row)

        in_row = QHBoxLayout()
        in_lbl = QLabel("Microphone:")
        in_lbl.setFixedWidth(72)
        in_row.addWidget(in_lbl)
        self._incall_input_combo = QComboBox()
        self._incall_input_combo.currentIndexChanged.connect(self._on_incall_input_changed)
        in_row.addWidget(self._incall_input_combo, 1)
        dev_layout.addLayout(in_row)

        layout.addWidget(dev_widget)
        layout.addSpacing(20)

        # ── Mute + End Call row ───────────────────────────────────────────────
        action_row = QHBoxLayout()
        action_row.setSpacing(14)

        self._incall_btn_mute = QPushButton("Mute")
        self._incall_btn_mute.setFixedHeight(56)
        self._incall_btn_mute.setCheckable(True)
        self._incall_btn_mute.setStyleSheet(
            "QPushButton{background:#2d2d2f;color:#e8eaed;"
            "border-radius:28px;font-size:16px;border:1px solid #5f6368;}"
            "QPushButton:hover{background:#3c4043;}"
            "QPushButton:checked{background:#ea4335;color:white;border:none;}"
        )
        self._incall_btn_mute.toggled.connect(self._on_incall_mute_toggled)
        action_row.addWidget(self._incall_btn_mute)

        btn_end = QPushButton("  End Call")
        btn_end.setFixedHeight(56)
        btn_end.setStyleSheet("""
            QPushButton {
                background: #ea4335; color: white;
                border-radius: 28px; font-size: 18px; font-weight: bold;
            }
            QPushButton:hover  { background: #c5352a; }
            QPushButton:pressed{ background: #a02820; }
        """)
        btn_end.clicked.connect(self.hangup_requested)
        action_row.addWidget(btn_end)

        layout.addLayout(action_row)

        layout.addStretch(1)
        return w

    def _on_incall_vol_down(self, checked: bool = False):
        self._incall_vol_slider.setValue(max(0, self._incall_vol_slider.value() - 5))

    def _on_incall_vol_up(self, checked: bool = False):
        self._incall_vol_slider.setValue(min(100, self._incall_vol_slider.value() + 5))

    def _on_incall_vol_slider(self, value: int):
        self._incall_vol_lbl.setText(f"{value}%")
        self.volume_changed.emit(value)
        # Keep Settings tab slider in sync
        if hasattr(self, "_vol_slider"):
            self._vol_slider.blockSignals(True)
            self._vol_slider.setValue(value)
            self._vol_slider.blockSignals(False)
            self._vol_label.setText(f"{value}%")

    def _on_incall_mute_toggled(self, checked: bool):
        self._incall_btn_mute.setText("Unmute" if checked else "Mute")
        self.mute_requested.emit(checked)
        # Keep overlay in sync
        if hasattr(self, "_call_overlay"):
            self._call_overlay._btn_mute.blockSignals(True)
            self._call_overlay._btn_mute.setChecked(checked)
            self._call_overlay._btn_mute.setText("Unmute" if checked else "Mute")
            self._call_overlay._btn_mute.blockSignals(False)

    def _on_incall_output_changed(self, _index: int):
        name = self._incall_output_combo.currentData() or ""
        self.audio_output_changed.emit(name)
        # Keep Settings tab combo in sync
        if hasattr(self, "_audio_output_combo"):
            self._audio_output_combo.blockSignals(True)
            for i in range(self._audio_output_combo.count()):
                if self._audio_output_combo.itemData(i) == name:
                    self._audio_output_combo.setCurrentIndex(i)
                    break
            self._audio_output_combo.blockSignals(False)

    def _on_incall_input_changed(self, _index: int):
        name = self._incall_input_combo.currentData() or ""
        self.audio_input_changed.emit(name)
        # Keep Settings tab combo in sync
        if hasattr(self, "_audio_input_combo"):
            self._audio_input_combo.blockSignals(True)
            for i in range(self._audio_input_combo.count()):
                if self._audio_input_combo.itemData(i) == name:
                    self._audio_input_combo.setCurrentIndex(i)
                    break
            self._audio_input_combo.blockSignals(False)

    def _build_calllog_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self._calllog_list = QListWidget()
        self._calllog_list.setStyleSheet(
            "QListWidget::item { padding: 6px 4px; border-bottom: 1px solid #2d2d2f; }"
        )
        self._calllog_list.itemDoubleClicked.connect(self._on_calllog_double_click)
        self._calllog_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._calllog_list.customContextMenuRequested.connect(self._on_calllog_context_menu)
        layout.addWidget(self._calllog_list)

        btn_row = QHBoxLayout()
        lbl = QLabel("Double-click to open profile · Right-click for options")
        lbl.setStyleSheet("color: #5f6368; font-size: 11px;")
        btn_row.addWidget(lbl)
        btn_row.addStretch()
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self.refresh_call_log)
        btn_row.addWidget(btn_refresh)
        layout.addLayout(btn_row)
        return w

    def _on_calllog_double_click(self, item: QListWidgetItem):
        self._calllog_open_profile(item)

    def _on_calllog_context_menu(self, pos):
        item = self._calllog_list.itemAt(pos)
        if not item:
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        number = entry.number if entry else ""
        if not number:
            return

        menu = QMenu(self)
        act_profile = menu.addAction("Open profile")
        act_call = menu.addAction(f"Call  {number}")
        action = menu.exec(self._calllog_list.mapToGlobal(pos))
        if action == act_profile:
            self._calllog_open_profile(item)
        elif action == act_call:
            self.dial_requested.emit(number)

    def _calllog_open_profile(self, item: QListWidgetItem):
        from ui.contact_profile import ContactProfileDialog
        entry = item.data(Qt.ItemDataRole.UserRole)
        if not entry:
            return
        number = entry.number
        contact = self._store.lookup_by_number(number)
        dlg = ContactProfileDialog(
            store=self._store,
            dial_cb=self.dial_requested.emit,
            contact=contact,
            number=number,
            parent=self,
        )
        dlg.exec()

    def _on_tab_changed(self, index: int):
        tab_name = self._tabs.tabText(index)
        if tab_name == "Call Log":
            self.refresh_call_log()
        elif tab_name == "Settings":
            if not self._audio_devices_loaded:
                self._refresh_audio_devices()
                self._audio_devices_loaded = True
        else:
            # Stop mic test if user navigates away
            if getattr(self, "_mic_test_proc", None):
                self._stop_mic_test()

    def _build_settings_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        # ── Call audio group ──────────────────────────────────────────────────
        audio_group = QGroupBox("Call Audio Device")
        ag_layout = QVBoxLayout(audio_group)
        ag_layout.setSpacing(10)

        # Output device row
        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Output (speaker):"))
        self._audio_output_combo = QComboBox()
        self._audio_output_combo.currentIndexChanged.connect(self._on_audio_output_changed)
        out_row.addWidget(self._audio_output_combo, 1)
        ag_layout.addLayout(out_row)

        # Input device row
        in_row = QHBoxLayout()
        in_row.addWidget(QLabel("Input (microphone):"))
        self._audio_input_combo = QComboBox()
        self._audio_input_combo.currentIndexChanged.connect(self._on_audio_input_changed)
        in_row.addWidget(self._audio_input_combo, 1)
        ag_layout.addLayout(in_row)

        # ── Dial test section ─────────────────────────────────────────────────
        dials_row = QHBoxLayout()
        dials_row.setSpacing(24)
        dials_row.addStretch()

        # Speaker dial column
        spk_col = QVBoxLayout()
        spk_col.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._spk_dial = _LevelDial(label="Speaker")
        spk_col.addWidget(self._spk_dial, alignment=Qt.AlignmentFlag.AlignHCenter)
        self._btn_test_spk = QPushButton("Test speaker")
        self._btn_test_spk.setFixedWidth(110)
        self._btn_test_spk.setToolTip("Play a short beep through the selected output")
        self._btn_test_spk.clicked.connect(self._test_output)
        spk_col.addWidget(self._btn_test_spk, alignment=Qt.AlignmentFlag.AlignHCenter)
        dials_row.addLayout(spk_col)

        # Mic dial column
        mic_col = QVBoxLayout()
        mic_col.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._mic_dial = _LevelDial(label="Microphone")
        mic_col.addWidget(self._mic_dial, alignment=Qt.AlignmentFlag.AlignHCenter)
        self._btn_test_mic = QPushButton("Test mic")
        self._btn_test_mic.setFixedWidth(110)
        self._btn_test_mic.setToolTip("Start/stop live microphone level monitor")
        self._btn_test_mic.setCheckable(True)
        self._btn_test_mic.clicked.connect(self._toggle_mic_test)
        mic_col.addWidget(self._btn_test_mic, alignment=Qt.AlignmentFlag.AlignHCenter)
        dials_row.addLayout(mic_col)

        dials_row.addStretch()
        ag_layout.addLayout(dials_row)

        # Internal state for mic test
        self._mic_test_proc = None
        self._mic_test_timer = QTimer()
        self._mic_test_timer.setInterval(60)   # ~16 fps
        self._mic_test_timer.timeout.connect(self._update_mic_level)

        # Internal state for speaker dial animation
        self._spk_anim_timer = QTimer()
        self._spk_anim_timer.setInterval(30)   # ~30 fps during beep
        self._spk_anim_step = 0
        self._spk_anim_timer.timeout.connect(self._animate_spk_dial)

        # Refresh button
        btn_refresh = QPushButton("Refresh device list")
        btn_refresh.setFixedWidth(160)
        btn_refresh.clicked.connect(self._refresh_audio_devices)
        ag_layout.addWidget(btn_refresh, alignment=Qt.AlignmentFlag.AlignLeft)

        layout.addWidget(audio_group)

        # ── Volume group ──────────────────────────────────────────────────────
        vol_group = QGroupBox("Call Volume")
        vg_layout = QHBoxLayout(vol_group)
        vg_layout.setSpacing(8)

        btn_vol_down = QPushButton("−")
        btn_vol_down.setFixedSize(36, 36)
        btn_vol_down.setStyleSheet("font-size: 18px; font-weight: bold;")
        btn_vol_down.clicked.connect(self._on_settings_vol_down)
        vg_layout.addWidget(btn_vol_down)

        self._vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setValue(80)
        self._vol_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._vol_slider.setTickInterval(10)
        self._vol_slider.valueChanged.connect(self._on_volume_slider)
        vg_layout.addWidget(self._vol_slider, 1)

        btn_vol_up = QPushButton("+")
        btn_vol_up.setFixedSize(36, 36)
        btn_vol_up.setStyleSheet("font-size: 18px; font-weight: bold;")
        btn_vol_up.clicked.connect(self._on_settings_vol_up)
        vg_layout.addWidget(btn_vol_up)

        self._vol_label = QLabel("80%")
        self._vol_label.setFixedWidth(42)
        self._vol_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        vg_layout.addWidget(self._vol_label)

        layout.addWidget(vol_group)

        # ── Config file hint ──────────────────────────────────────────────────
        hint = QLabel("Advanced settings: ~/.config/handsfree/config.toml")
        hint.setStyleSheet("color: #5f6368; font-size: 11px;")
        layout.addWidget(hint)

        layout.addStretch()

        # Populate devices when this tab is first shown
        self._audio_devices_loaded = False
        return w

    # ── Settings helpers ──────────────────────────────────────────────────────

    def _refresh_audio_devices(self):
        """Query pactl for available sinks and sources and fill the dropdowns."""
        import subprocess, json
        sinks   = [("(system default)", "")]
        sources = [("(system default)", "")]
        try:
            raw = subprocess.check_output(
                ["pactl", "--format=json", "list", "sinks"], text=True, timeout=5
            )
            for d in json.loads(raw):
                name  = d.get("name", "")
                descr = d.get("description", name)
                sinks.append((descr, name))
        except Exception as e:
            logger.debug("pactl sinks: %s", e)
        try:
            raw = subprocess.check_output(
                ["pactl", "--format=json", "list", "sources"], text=True, timeout=5
            )
            for d in json.loads(raw):
                name  = d.get("name", "")
                descr = d.get("description", name)
                # Skip monitor sources (loopback artefacts)
                if not name.endswith(".monitor"):
                    sources.append((descr, name))
        except Exception as e:
            logger.debug("pactl sources: %s", e)

        # All combos to update: Settings tab + in-call page
        out_combos = [self._audio_output_combo]
        in_combos  = [self._audio_input_combo]
        if hasattr(self, "_incall_output_combo"):
            out_combos.append(self._incall_output_combo)
        if hasattr(self, "_incall_input_combo"):
            in_combos.append(self._incall_input_combo)

        prev_out = self._audio_output_combo.currentData() or ""
        prev_in  = self._audio_input_combo.currentData()  or ""

        for combo in out_combos + in_combos:
            combo.blockSignals(True)

        for combo in out_combos:
            combo.clear()
            for descr, name in sinks:
                combo.addItem(descr, userData=name)

        for combo in in_combos:
            combo.clear()
            for descr, name in sources:
                combo.addItem(descr, userData=name)

        # Restore previous selection in all combos
        for combo in out_combos:
            for i in range(combo.count()):
                if combo.itemData(i) == prev_out:
                    combo.setCurrentIndex(i)
                    break
        for combo in in_combos:
            for i in range(combo.count()):
                if combo.itemData(i) == prev_in:
                    combo.setCurrentIndex(i)
                    break

        for combo in out_combos + in_combos:
            combo.blockSignals(False)

    def set_audio_selection(self, output_name: str, input_name: str, volume: int):
        """Called by HandsFreeApp to restore saved settings into the UI."""
        # Ensure devices are loaded first
        if not self._audio_devices_loaded:
            self._refresh_audio_devices()
            self._audio_devices_loaded = True

        for combo, name in ((self._audio_output_combo, output_name),
                            (self._audio_input_combo,  input_name)):
            combo.blockSignals(True)
            for i in range(combo.count()):
                if combo.itemData(i) == name:
                    combo.setCurrentIndex(i)
                    break
            combo.blockSignals(False)

        self._vol_slider.blockSignals(True)
        self._vol_slider.setValue(volume)
        self._vol_label.setText(f"{volume}%")
        self._vol_slider.blockSignals(False)

    def _on_audio_output_changed(self, _index: int):
        name = self._audio_output_combo.currentData() or ""
        self.audio_output_changed.emit(name)

    def _on_audio_input_changed(self, _index: int):
        name = self._audio_input_combo.currentData() or ""
        self.audio_input_changed.emit(name)

    def _on_volume_slider(self, value: int):
        self._vol_label.setText(f"{value}%")
        self.volume_changed.emit(value)

    def _on_volume_button(self, delta: int):
        new_val = max(0, min(100, self._vol_slider.value() + delta))
        self._vol_slider.setValue(new_val)   # triggers _on_volume_slider

    def _on_settings_vol_down(self, checked: bool = False):
        self._on_volume_button(-5)

    def _on_settings_vol_up(self, checked: bool = False):
        self._on_volume_button(+5)

    # ── Audio tests ───────────────────────────────────────────────────────────

    def _test_output(self):
        """Play a 440 Hz beep through the selected output; animate the speaker dial."""
        import math, struct, subprocess, threading
        sink = self._audio_output_combo.currentData() or ""
        vol  = self._vol_slider.value()

        rate     = 44100
        duration = 0.8
        freq     = 440
        n        = int(rate * duration)
        fade     = int(rate * 0.02)
        samples  = []
        for i in range(n):
            amp = 1.0
            if i < fade:
                amp = i / fade
            elif i > n - fade:
                amp = (n - i) / fade
            samples.append(int(32767 * amp * math.sin(2 * math.pi * freq * i / rate)))
        pcm = struct.pack(f"{n}h", *samples)

        cmd = [
            "pacat", "--playback", "--raw",
            "--format=s16le", "--rate=44100", "--channels=1",
            f"--volume={int(vol * 655.35)}",
        ]
        if sink:
            cmd.append(f"--device={sink}")

        self._btn_test_spk.setEnabled(False)
        self._btn_test_spk.setText("Playing…")
        self._spk_anim_step = 0
        self._spk_anim_timer.start()

        def _run():
            try:
                proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                proc.communicate(input=pcm, timeout=3)
            except Exception as e:
                logger.debug("Speaker test error: %s", e)
            finally:
                QTimer.singleShot(0, self._finish_spk_test)

        threading.Thread(target=_run, daemon=True).start()

    def _animate_spk_dial(self):
        """Simulate a sine-wave VU reading on the speaker dial while beep plays."""
        import math
        self._spk_anim_step += 1
        # Pulsing sine that mirrors the 440 Hz envelope visually
        t = self._spk_anim_step * 0.08
        level = int(55 + 35 * math.sin(t) * abs(math.sin(t * 0.3)))
        self._spk_dial.set_level(max(0, min(100, level)))

    def _finish_spk_test(self):
        self._spk_anim_timer.stop()
        self._spk_dial.set_level(0)
        self._btn_test_spk.setText("Test speaker")
        self._btn_test_spk.setEnabled(True)

    def _toggle_mic_test(self, checked: bool):
        if checked:
            self._start_mic_test()
        else:
            self._stop_mic_test()

    def _start_mic_test(self):
        import subprocess
        source = self._audio_input_combo.currentData() or ""
        cmd = [
            "pacat", "--record", "--raw",
            "--format=s16le", "--rate=16000", "--channels=1",
            "--latency-msec=40",
        ]
        if source:
            cmd.append(f"--device={source}")
        try:
            self._mic_test_proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
            )
            self._btn_test_mic.setText("Stop")
            self._mic_test_timer.start()
        except Exception as e:
            logger.warning("Mic test start failed: %s", e)
            self._btn_test_mic.setChecked(False)

    def _stop_mic_test(self):
        self._mic_test_timer.stop()
        if self._mic_test_proc:
            try:
                self._mic_test_proc.terminate()
                self._mic_test_proc.wait(timeout=1)
            except Exception:
                pass
            self._mic_test_proc = None
        self._mic_dial.set_level(0)
        self._btn_test_mic.setText("Test mic")
        self._btn_test_mic.setChecked(False)

    def _update_mic_level(self):
        """Read a chunk from pacat and drive the mic dial (called by timer)."""
        import math, select, struct
        if not self._mic_test_proc:
            self._stop_mic_test()
            return
        try:
            ready, _, _ = select.select([self._mic_test_proc.stdout], [], [], 0)
            if not ready:
                return
            chunk = self._mic_test_proc.stdout.read(640)
            if not chunk:
                self._stop_mic_test()
                return
        except Exception:
            self._stop_mic_test()
            return

        n = len(chunk) // 2
        if n == 0:
            return
        samples = struct.unpack(f"{n}h", chunk[:n * 2])
        rms = math.sqrt(sum(s * s for s in samples) / n)
        level = 0 if rms < 1 else int(min(100, 20 * math.log10(rms / 32767.0) + 100))
        self._mic_dial.set_level(level)

    # ── Public update methods (call from app.py via Qt signals) ───────────────

    @pyqtSlot(str)
    def on_connected(self, device_name: str):
        self._status_dot.setStyleSheet("color: #34a853; font-size: 16px;")
        self._status_label.setText(f"Connected: {device_name}")
        self._btn_connect.setVisible(False)
        self._btn_disconnect.setVisible(True)
        self._btn_sync.setVisible(True)
        self._contacts_widget.refresh()

    @pyqtSlot()
    def on_disconnected(self):
        self._status_dot.setStyleSheet("color: #888; font-size: 16px;")
        self._status_label.setText("Not connected")
        self._btn_connect.setVisible(True)
        self._btn_disconnect.setVisible(False)
        self._btn_sync.setVisible(False)

    @pyqtSlot(str)
    # ── In-call contact helpers ───────────────────────────────────────────────

    def _set_incall_contact(self, number: str) -> tuple[str, bytes | None]:
        """Populate avatar/labels from store. Returns (display_name, photo_bytes)."""
        from PyQt6.QtGui import QPixmap
        contact = self._store.lookup_by_number(number) if number else None
        name = contact.effective_name if contact else (number or "Unknown")
        self._incall_number_lbl.setText(name)
        if contact and name != number:
            self._incall_number_sub.setText(number)
            self._incall_number_sub.setVisible(True)
        else:
            self._incall_number_sub.setVisible(False)

        photo_bytes = None
        if contact:
            if contact.photo_data:
                photo_bytes = contact.photo_data
            elif contact.raw_vcard:
                from ui.contact_profile import _extract_vcard_photo
                photo_bytes = _extract_vcard_photo(contact.raw_vcard)

        _SZ = 88
        if photo_bytes:
            px = QPixmap()
            if px.loadFromData(photo_bytes):
                px = px.scaled(_SZ, _SZ,
                               Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                               Qt.TransformationMode.SmoothTransformation)
                self._incall_avatar.setPixmap(px.copy(0, 0, _SZ, _SZ))
                self._incall_avatar.setStyleSheet(
                    "border-radius: 44px; background: #3c4043; border: 2px solid #5f6368;"
                )
                return name, photo_bytes

        words = name.split()
        initials = (words[0][0] + words[-1][0]).upper() if len(words) >= 2 else name[:2].upper() or "?"
        self._incall_avatar.setText(initials)
        self._incall_avatar.setStyleSheet(
            "border-radius: 44px; background: #3c4043; border: 2px solid #5f6368;"
            "font-size: 28px; font-weight: bold; color: white;"
        )
        return name, photo_bytes

    def on_dialling(self, number: str):
        """Show in-call screen immediately when the user dials (before remote answers)."""
        self._refresh_audio_devices()
        self._incall_btn_mute.setChecked(False)
        self._incall_btn_mute.setText("Mute")
        display, photo = self._set_incall_contact(number)
        self._incall_timer_lbl.setText("Calling…")
        vol = self._vol_slider.value() if hasattr(self, "_vol_slider") else 80
        self._incall_vol_slider.blockSignals(True)
        self._incall_vol_slider.setValue(vol)
        self._incall_vol_slider.blockSignals(False)
        self._incall_vol_lbl.setText(f"{vol}%")
        self._dial_stack.setCurrentIndex(1)
        self._opened_for_call = not self.isVisible()
        self.show()
        self.raise_()
        for i in range(self._tabs.count()):
            if self._tabs.tabText(i) == "Dial":
                self._tabs.setCurrentIndex(i)
                break
        self._statusbar.showMessage(f"Calling {display}…")
        self._call_overlay.show_calling(display, number, photo)

    def on_call_active(self, number: str):
        display, photo = self._set_incall_contact(number)
        self._call_banner_number.setText(display)
        self._call_elapsed_sec = 0
        self._call_banner_timer.setText("0:00")
        self._call_banner.setVisible(True)
        self._call_timer.start()
        self._incall_timer_lbl.setText("0:00")
        vol = self._vol_slider.value() if hasattr(self, "_vol_slider") else 80
        self._incall_vol_slider.blockSignals(True)
        self._incall_vol_slider.setValue(vol)
        self._incall_vol_slider.blockSignals(False)
        self._incall_vol_lbl.setText(f"{vol}%")
        self._dial_stack.setCurrentIndex(1)
        self._opened_for_call = not self.isVisible()
        self.show()
        self.raise_()
        for i in range(self._tabs.count()):
            if self._tabs.tabText(i) == "Dial":
                self._tabs.setCurrentIndex(i)
                break
        self._statusbar.showMessage(f"Call active: {display}")
        self._call_overlay.show_active(display, number, photo)

    @pyqtSlot()
    def on_call_ended(self):
        self._call_timer.stop()
        self._call_banner.setVisible(False)
        self._dial_stack.setCurrentIndex(0)
        self._statusbar.showMessage("Call ended")
        self.refresh_call_log()
        # Show red "Call Ended" overlay then auto-hide it
        self._call_overlay.show_ended()
        # Hide main window if it was opened automatically for this call
        if getattr(self, "_opened_for_call", False):
            self._opened_for_call = False
            self.hide()

    def set_devices(self, devices: list[dict]):
        self._devices = devices
        self._device_combo.clear()
        for d in devices:
            self._device_combo.addItem(
                f"{d['name']} ({d['address']})",
                userData=d["path"],
            )

    def refresh_call_log(self):
        self._calllog_list.clear()
        logs = self._store.get_call_log(limit=200)
        for entry in logs:
            contact = self._store.get_contact_by_id(entry.contact_id) if entry.contact_id else None
            # Try lookup by number if contact_id not set (e.g. new calls)
            if not contact:
                contact = self._store.lookup_by_number(entry.number)
            name = contact.effective_name if contact else entry.number

            icon  = {"incoming": "↙", "outgoing": "↗", "missed": "✗"}.get(entry.direction, "?")
            color = {"incoming": "#34a853", "outgoing": "#1a73e8", "missed": "#ea4335"}.get(entry.direction, "#9aa0a6")

            # Format duration
            if entry.duration_sec >= 60:
                dur = f"  {entry.duration_sec // 60}m {entry.duration_sec % 60:02d}s"
            elif entry.duration_sec > 0:
                dur = f"  {entry.duration_sec}s"
            else:
                dur = ""

            # Format timestamp: today → time only, otherwise date+time
            from datetime import datetime, timezone
            try:
                dt = datetime.fromisoformat(entry.started_at.replace("Z", "+00:00"))
                today = datetime.now(timezone.utc).date()
                ts = dt.strftime("%H:%M") if dt.date() == today else dt.strftime("%d %b %H:%M")
            except Exception:
                ts = entry.started_at[:16]

            item = QListWidgetItem(f"{icon}  {name}    {ts}{dur}")
            item.setForeground(QColor(color))
            item.setData(Qt.ItemDataRole.UserRole, entry)   # full CallLog for profile/redial
            item.setToolTip(f"{entry.direction.capitalize()}  •  {entry.number}  •  {entry.started_at[:16]}")
            self._calllog_list.addItem(item)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_dial(self):
        number = self._dial_input.text().strip()
        if number:
            self.dial_requested.emit(number)
            self._dial_input.clear()

    def _on_connect_clicked(self):
        idx = self._device_combo.currentIndex()
        if idx >= 0:
            path = self._device_combo.itemData(idx)
            self.connect_requested.emit(path)

    # ── Style ─────────────────────────────────────────────────────────────────

    def _apply_style(self):

        self.setStyleSheet("""
            QMainWindow, QWidget {
                background: #1c1c1e;
                color: #e8eaed;
                font-family: "Segoe UI", "Ubuntu", sans-serif;
                font-size: 13px;
            }
            QGroupBox {
                border: 1px solid #3c4043;
                border-radius: 6px;
                margin-top: 8px;
                padding: 4px;
            }
            QGroupBox::title { color: #9aa0a6; padding: 0 4px; }
            QTabWidget::pane { border: 1px solid #3c4043; border-radius: 4px; }
            QTabBar::tab {
                background: #2d2d2f; color: #9aa0a6;
                padding: 6px 16px; border-radius: 4px 4px 0 0;
            }
            QTabBar::tab:selected { background: #1c1c1e; color: #e8eaed; }
            QListWidget {
                background: #2d2d2f; border: 1px solid #3c4043;
                border-radius: 4px;
            }
            QListWidget::item:selected { background: #3c4043; }
            QLineEdit {
                background: #2d2d2f; color: #e8eaed;
                border: 1px solid #3c4043; border-radius: 4px;
                padding: 4px 8px;
            }
            QPushButton {
                background: #3c4043; color: #e8eaed;
                border: none; border-radius: 4px; padding: 6px 14px;
            }
            QPushButton:hover { background: #4a4d50; }
            QPushButton:pressed { background: #5a5d60; }
            QComboBox {
                background: #2d2d2f; color: #e8eaed;
                border: 1px solid #3c4043; border-radius: 4px;
                padding: 4px 8px;
            }
            QStatusBar { color: #9aa0a6; }
        """)


# ── Level dial widget ─────────────────────────────────────────────────────────

class _LevelDial(QWidget):
    """
    Circular VU-meter dial.

    Draws a dark circle with a coloured arc sweeping from 7 o'clock to 5 o'clock
    (240° total sweep) that fills according to the current level (0–100).
    Colour transitions green → yellow → red as level rises.
    The numeric level is printed in the centre.
    """

    SIZE = 110   # pixels

    def __init__(self, label: str = "", parent=None):
        super().__init__(parent)
        self._level  = 0       # 0-100
        self._label  = label
        self.setFixedSize(self.SIZE, self.SIZE + 18)  # +18 for label below

    def set_level(self, level: int):
        level = max(0, min(100, level))
        if level != self._level:
            self._level = level
            self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx, cy = self.SIZE // 2, self.SIZE // 2
        r = self.SIZE // 2 - 6

        # ── Background circle ─────────────────────────────────────────────────
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#2d2d2f"))
        p.drawEllipse(cx - r - 4, cy - r - 4, (r + 4) * 2, (r + 4) * 2)

        # ── Track arc (full sweep, dark) ──────────────────────────────────────
        rect = QRectF(cx - r, cy - r, r * 2, r * 2)
        START_ANGLE = 225    # degrees (7 o'clock), Qt units = degrees * 16
        SWEEP       = 270    # total sweep degrees

        track_pen = QPen(QColor("#3c4043"), 8, Qt.PenStyle.SolidLine,
                         Qt.PenCapStyle.RoundCap)
        p.setPen(track_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(rect,
                  int(START_ANGLE * 16),
                  -int(SWEEP * 16))

        # ── Level arc (coloured fill) ─────────────────────────────────────────
        if self._level > 0:
            fill_sweep = SWEEP * self._level / 100.0

            # Gradient: green (0-60) → yellow (60-80) → red (80-100)
            if self._level <= 60:
                r_c, g_c, b_c = 52, 168, 83      # green
            elif self._level <= 80:
                t = (self._level - 60) / 20.0
                r_c = int(52  + t * (251 - 52))
                g_c = int(168 + t * (188 - 168))
                b_c = int(83  + t * (4   - 83))
            else:
                t = (self._level - 80) / 20.0
                r_c = int(251 + t * (234 - 251))
                g_c = int(188 + t * (67  - 188))
                b_c = int(4   + t * (53  - 4))

            level_pen = QPen(QColor(r_c, g_c, b_c), 8, Qt.PenStyle.SolidLine,
                             Qt.PenCapStyle.RoundCap)
            p.setPen(level_pen)
            p.drawArc(rect,
                      int(START_ANGLE * 16),
                      -int(fill_sweep * 16))

        # ── Centre text (level %) ─────────────────────────────────────────────
        font = QFont()
        font.setPointSize(14)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor("#e8eaed"))
        p.drawText(QRectF(0, 0, self.SIZE, self.SIZE),
                   Qt.AlignmentFlag.AlignCenter,
                   f"{self._level}")

        # ── Label below circle ────────────────────────────────────────────────
        if self._label:
            font2 = QFont()
            font2.setPointSize(9)
            p.setFont(font2)
            p.setPen(QColor("#9aa0a6"))
            p.drawText(QRectF(0, self.SIZE, self.SIZE, 18),
                       Qt.AlignmentFlag.AlignCenter,
                       self._label)

        p.end()