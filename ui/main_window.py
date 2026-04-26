"""
Main application window — contacts list, call log, device status, dial pad.
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import Qt, QRectF, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QFont, QKeySequence, QPainter, QPainterPath, QPen, QShortcut
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFrame, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QPushButton, QScrollArea, QSlider, QSplitter,
    QStackedWidget, QStatusBar, QTabWidget, QVBoxLayout, QWidget,
)

from contacts.store import ContactStore

logger = logging.getLogger(__name__)


from PyQt6.QtCore import QObject, QEvent

class _NoScrollFilter(QObject):
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel:
            event.ignore()
            return True
        return False

_no_scroll = _NoScrollFilter()


def _no_wheel(widget):
    """Prevent a widget from reacting to scroll wheel events."""
    widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    widget.installEventFilter(_no_scroll)


class _ComboBox(QComboBox):
    """QComboBox that always opens downward."""

    def showPopup(self):
        super().showPopup()
        popup = self.findChild(QFrame)
        if popup and popup.isWindow():
            popup.move(self.mapToGlobal(self.rect().bottomLeft()))


class _TitleBar(QWidget):
    """VS Code-style frameless title bar: drag area + title + min/close buttons."""

    def __init__(self, window: "MainWindow", parent=None):
        super().__init__(parent)
        self._win = window
        self._drag_pos = None
        self.setFixedHeight(32)
        self.setObjectName("titleBar")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 4, 0)
        layout.setSpacing(6)

        import os
        from PyQt6.QtGui import QPixmap
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "resources", "icon_16.png")
        if os.path.exists(icon_path):
            icon_lbl = QLabel()
            icon_lbl.setPixmap(QPixmap(icon_path))
            icon_lbl.setFixedSize(16, 16)
            icon_lbl.setStyleSheet("background: transparent;")
            layout.addWidget(icon_lbl)

        layout.addStretch()

        self._btn_min = QPushButton("─")
        self._btn_min.setObjectName("titleBtn")
        self._btn_min.setFixedSize(46, 32)
        self._btn_min.clicked.connect(window.showMinimized)
        layout.addWidget(self._btn_min)

        self._btn_close = QPushButton("✕")
        self._btn_close.setObjectName("titleBtnClose")
        self._btn_close.setFixedSize(46, 32)
        self._btn_close.clicked.connect(window.hide)
        layout.addWidget(self._btn_close)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self._win.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() == Qt.MouseButton.LeftButton:
            self._win.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    def mouseDoubleClickEvent(self, event):
        pass  # prevent accidental maximise on double-click


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
    mute_btn_clicked     = pyqtSignal()

    # Settings signals
    audio_output_changed    = pyqtSignal(str)   # pactl sink name
    audio_input_changed     = pyqtSignal(str)   # pactl source name
    volume_changed          = pyqtSignal(int)   # 0-100
    ring_volume_changed     = pyqtSignal(int)   # 0-100
    mic_sensitivity_changed = pyqtSignal(int)   # 0-100
    theme_changed           = pyqtSignal(str)   # theme name

    def __init__(self, store: ContactStore, parent=None):
        super().__init__(parent)
        self._store = store
        self._devices: list[dict] = []

        self.setWindowTitle("HandsFree")
        self.setFixedSize(520, 580)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)

        import os
        from PyQt6.QtGui import QIcon as _QIcon
        _ico = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "resources", "icon_256.png")
        if os.path.exists(_ico):
            self.setWindowIcon(_QIcon(_ico))
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
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self._title_bar = _TitleBar(self)
        main_layout.addWidget(self._title_bar)

        # Active-call banner (hidden until a call is active)
        self._call_banner = self._build_call_banner()
        self._call_banner.setVisible(False)
        main_layout.addWidget(self._call_banner)

        # Tabs: Contacts | Dial | Call Log | Settings
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

        # ── Bottom status bar ──
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)

        # Left side: connection dot + label
        self._status_dot = QLabel("⚠")
        self._status_dot.setStyleSheet("color: #ffffff; font-size: 14px; background: transparent;")
        self._status_label = QLabel("Not connected")
        self._status_label.setStyleSheet("font-size: 12px; color: #ffffff; background: transparent;")
        self._mute_btn = QPushButton("🔔")
        self._mute_btn.setFlat(True)
        self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mute_btn.setStyleSheet(
            "QPushButton { font-size: 13px; background: transparent; border: none; padding: 0 2px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.15); border-radius: 3px; }"
        )
        self._mute_btn.setToolTip("Click to mute calls")
        self._mute_btn.clicked.connect(self.mute_btn_clicked)
        self._statusbar.addWidget(self._status_dot)
        self._statusbar.addWidget(self._status_label)
        self._statusbar.addWidget(self._mute_btn)

        # Right side: sync widget + update badge
        self._sync_widget = _SyncWidget()
        self._statusbar.addPermanentWidget(self._sync_widget)

        self._update_badge = _UpdateBadge()
        self._update_badge.clicked.connect(self._on_update_badge_clicked)
        self._statusbar.addPermanentWidget(self._update_badge)
        QTimer.singleShot(5000, self._silent_update_check)
        self._update_check_timer = QTimer(self)
        self._update_check_timer.setInterval(4 * 60 * 60 * 1000)  # every 4 hours
        self._update_check_timer.timeout.connect(self._silent_update_check)
        self._update_check_timer.start()

        # Floating call overlay (always-on-top, visible even when window minimised)
        from ui.call_overlay import CallOverlay
        self._call_overlay = CallOverlay()
        self._call_overlay.hangup_requested.connect(self.hangup_requested)
        self._call_overlay.mute_toggled.connect(self.mute_requested)

    def _build_call_banner(self) -> QWidget:
        """Red banner shown during an active call — number, timer, End Call button."""
        banner = QWidget()
        banner.setObjectName("callBanner")
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
        self._call_banner_number.setObjectName("callBannerNumber")
        info_col.addWidget(self._call_banner_number)
        self._call_banner_timer = QLabel("0:00")
        self._call_banner_timer.setObjectName("callBannerTimer")
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
        if hasattr(self, "_dev_timer_lbl"):
            self._dev_timer_lbl.setText(text)

    def _build_device_group(self) -> QGroupBox:
        """Device connection controls — lives in the Settings tab."""
        group = QGroupBox("Device")
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Combo + Connect row (shown when disconnected)
        combo_row = QHBoxLayout()
        self._device_combo = _ComboBox()
        self._device_combo.setMinimumWidth(200)
        self._device_combo.setPlaceholderText("Select device…")
        combo_row.addWidget(self._device_combo, 1)

        self._btn_connect = QPushButton("Connect")
        self._btn_connect.clicked.connect(self._on_connect_clicked)
        combo_row.addWidget(self._btn_connect)
        layout.addLayout(combo_row)

        # Disconnect + Sync row (shown when connected)
        action_row = QHBoxLayout()
        self._btn_disconnect = QPushButton("Disconnect")
        self._btn_disconnect.setVisible(False)
        self._btn_disconnect.clicked.connect(self.disconnect_requested)
        action_row.addWidget(self._btn_disconnect, 1)

        self._btn_sync = QPushButton("Sync Contacts")
        self._btn_sync.setVisible(False)
        self._btn_sync.clicked.connect(self.sync_requested)
        action_row.addWidget(self._btn_sync, 1)
        layout.addLayout(action_row)

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
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(16)

        input_row = QHBoxLayout()
        input_row.setSpacing(0)
        self._dial_input = QLineEdit()
        self._dial_input.setPlaceholderText("Enter number…")
        self._dial_input.setMinimumHeight(44)
        self._dial_input.setObjectName("dialInput")
        self._dial_input.setStyleSheet(
            "font-size: 22px; letter-spacing: 3px; padding: 4px 12px;"
            "border: none; border-bottom: 1px solid palette(mid);"
            "background: transparent;"
        )
        self._dial_input.returnPressed.connect(self._on_dial)
        input_row.addWidget(self._dial_input)

        btn_del = QPushButton("⌫")
        btn_del.setObjectName("dialDelBtn")
        btn_del.setFixedSize(44, 44)
        btn_del.clicked.connect(self._on_del_digit)
        input_row.addWidget(btn_del)
        layout.addLayout(input_row)

        from PyQt6.QtWidgets import QGridLayout
        numpad = QWidget()
        pad = QGridLayout(numpad)
        pad.setSpacing(10)
        pad.setContentsMargins(0, 8, 0, 8)

        KEYS = [
            ("1", ""),    ("2", "ABC"),  ("3", "DEF"),
            ("4", "GHI"), ("5", "JKL"),  ("6", "MNO"),
            ("7", "PQRS"),("8", "TUV"),  ("9", "WXYZ"),
            ("*", ""),    ("0", "+"),    ("#", ""),
        ]
        for i, (digit, sub) in enumerate(KEYS):
            row, col = divmod(i, 3)
            btn = QPushButton()
            btn.setObjectName("dialBtn")
            btn.setFixedSize(72, 72)
            btn_layout = QVBoxLayout(btn)
            btn_layout.setContentsMargins(0, 6, 0, 6)
            btn_layout.setSpacing(1)
            lbl_main = QLabel(digit)
            lbl_main.setObjectName("dialBtnLabel")
            lbl_main.setAlignment(Qt.AlignmentFlag.AlignCenter)
            btn_layout.addWidget(lbl_main)
            if sub:
                lbl_sub = QLabel(sub)
                lbl_sub.setObjectName("dialBtnSub")
                lbl_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
                btn_layout.addWidget(lbl_sub)
            btn.clicked.connect(self._make_digit_handler(digit))
            pad.addWidget(btn, row, col)

        layout.addWidget(numpad, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._btn_call = QPushButton("Call")
        self._btn_call.setObjectName("callBtn")
        self._btn_call.setFixedHeight(52)
        self._btn_call.clicked.connect(self._on_dial)
        layout.addWidget(self._btn_call)
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
        w.setObjectName("incallPage")
        layout = QVBoxLayout(w)
        layout.setContentsMargins(24, 32, 24, 24)
        layout.setSpacing(0)
        layout.addStretch(1)

        # Avatar
        self._incall_avatar = QLabel()
        self._incall_avatar.setObjectName("incallAvatar")
        self._incall_avatar.setFixedSize(88, 88)
        self._incall_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._incall_avatar.setText("?")
        layout.addWidget(self._incall_avatar, alignment=Qt.AlignmentFlag.AlignHCenter)

        layout.addSpacing(14)

        # Contact name (large) + number (small, below)
        self._incall_number_lbl = QLabel("")
        self._incall_number_lbl.setObjectName("incallName")
        self._incall_number_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._incall_number_lbl)

        self._incall_number_sub = QLabel("")
        self._incall_number_sub.setObjectName("incallSub")
        self._incall_number_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._incall_number_sub)

        layout.addSpacing(6)

        # Duration timer
        self._incall_timer_lbl = QLabel("0:00")
        self._incall_timer_lbl.setObjectName("incallTimer")
        self._incall_timer_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._incall_timer_lbl)

        layout.addSpacing(32)

        # Volume row  − [ ████░░ ] +
        vol_row = QHBoxLayout()
        vol_row.setSpacing(10)

        btn_vol_dn = QPushButton("−")
        btn_vol_dn.setObjectName("incallVolBtn")
        btn_vol_dn.setFixedSize(44, 44)
        btn_vol_dn.clicked.connect(self._on_incall_vol_down)
        vol_row.addWidget(btn_vol_dn)

        self._incall_vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._incall_vol_slider.setObjectName("incallSlider")
        self._incall_vol_slider.setRange(0, 100)
        self._incall_vol_slider.setValue(80)
        self._incall_vol_slider.valueChanged.connect(self._on_incall_vol_slider)
        vol_row.addWidget(self._incall_vol_slider, 1)

        btn_vol_up = QPushButton("+")
        btn_vol_up.setObjectName("incallVolBtn")
        btn_vol_up.setFixedSize(44, 44)
        btn_vol_up.clicked.connect(self._on_incall_vol_up)
        vol_row.addWidget(btn_vol_up)

        self._incall_vol_lbl = QLabel("80%")
        self._incall_vol_lbl.setObjectName("incallVolLbl")
        self._incall_vol_lbl.setFixedWidth(40)
        self._incall_vol_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        vol_row.addWidget(self._incall_vol_lbl)

        layout.addLayout(vol_row)
        layout.addSpacing(20)

        # ── Audio device pickers ──────────────────────────────────────────────
        dev_widget = QWidget()
        dev_widget.setObjectName("incallDevWidget")
        dev_layout = QVBoxLayout(dev_widget)
        dev_layout.setContentsMargins(0, 0, 0, 0)
        dev_layout.setSpacing(6)

        out_row = QHBoxLayout()
        out_lbl = QLabel("Speaker:")
        out_lbl.setObjectName("incallDevLabel")
        out_lbl.setFixedWidth(72)
        out_row.addWidget(out_lbl)
        self._incall_output_combo = _ComboBox()
        self._incall_output_combo.currentIndexChanged.connect(self._on_incall_output_changed)
        out_row.addWidget(self._incall_output_combo, 1)
        dev_layout.addLayout(out_row)

        in_row = QHBoxLayout()
        in_lbl = QLabel("Microphone:")
        in_lbl.setObjectName("incallDevLabel")
        in_lbl.setFixedWidth(72)
        in_row.addWidget(in_lbl)
        self._incall_input_combo = _ComboBox()
        self._incall_input_combo.currentIndexChanged.connect(self._on_incall_input_changed)
        in_row.addWidget(self._incall_input_combo, 1)
        dev_layout.addLayout(in_row)

        layout.addWidget(dev_widget)
        layout.addSpacing(20)

        # ── Mute + End Call row ───────────────────────────────────────────────
        action_row = QHBoxLayout()
        action_row.setSpacing(14)

        self._incall_btn_mute = QPushButton("Mute")
        self._incall_btn_mute.setObjectName("incallMuteBtn")
        self._incall_btn_mute.setFixedHeight(56)
        self._incall_btn_mute.setCheckable(True)
        self._incall_btn_mute.toggled.connect(self._on_incall_mute_toggled)
        action_row.addWidget(self._incall_btn_mute)

        btn_end = QPushButton("  End Call")
        btn_end.setObjectName("incallEndBtn")
        btn_end.setFixedHeight(56)
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

        self._calllog_search = QLineEdit()
        self._calllog_search.setPlaceholderText("Search call log…")
        self._calllog_search.setClearButtonEnabled(True)
        self._calllog_search.textChanged.connect(self._filter_calllog)
        layout.addWidget(self._calllog_search)

        self._calllog_list = QListWidget()
        self._calllog_list.setObjectName("calllogList")
        self._calllog_list.itemDoubleClicked.connect(self._on_calllog_double_click)
        self._calllog_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._calllog_list.customContextMenuRequested.connect(self._on_calllog_context_menu)
        layout.addWidget(self._calllog_list)

        btn_row = QHBoxLayout()
        lbl = QLabel("Double-click to open profile · Right-click for options")
        lbl.setObjectName("hintLabel")
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
        # Outer widget returned to the tab — contains only the scroll area
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer_layout.addWidget(scroll)

        # Inner widget that actually holds all the settings content
        w = QWidget()
        scroll.setWidget(w)

        layout = QVBoxLayout(w)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        # ── Theme group ───────────────────────────────────────────────────────
        theme_group = QGroupBox("Appearance")
        tg_layout = QHBoxLayout(theme_group)
        tg_layout.addWidget(QLabel("Theme:"))
        self._theme_combo = _ComboBox()
        from ui.themes import ALL as _ALL_THEMES
        for t in _ALL_THEMES:
            self._theme_combo.addItem(t.name)
        self._theme_combo.currentTextChanged.connect(self._on_theme_combo_changed)
        _no_wheel(self._theme_combo)
        tg_layout.addWidget(self._theme_combo, 1)
        layout.addWidget(theme_group)

        # ── Device group ──────────────────────────────────────────────────────
        layout.addWidget(self._build_device_group())

        # ── Call audio group ──────────────────────────────────────────────────
        audio_group = QGroupBox("Call Audio Device")
        audio_group.setMinimumHeight(280)
        ag_layout = QVBoxLayout(audio_group)
        ag_layout.setSpacing(10)

        # Output device row
        out_row = QHBoxLayout()
        _out_lbl = QLabel("Output (speaker):")
        _out_lbl.setFixedWidth(130)
        out_row.addWidget(_out_lbl)
        self._audio_output_combo = _ComboBox()
        self._audio_output_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._audio_output_combo.currentIndexChanged.connect(self._on_audio_output_changed)
        _no_wheel(self._audio_output_combo)
        out_row.addWidget(self._audio_output_combo, 1)
        ag_layout.addLayout(out_row)

        # Input device row
        in_row = QHBoxLayout()
        _in_lbl = QLabel("Input (microphone):")
        _in_lbl.setFixedWidth(130)
        in_row.addWidget(_in_lbl)
        self._audio_input_combo = _ComboBox()
        self._audio_input_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._audio_input_combo.currentIndexChanged.connect(self._on_audio_input_changed)
        _no_wheel(self._audio_input_combo)
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
        _no_wheel(self._spk_dial)
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
        _no_wheel(self._mic_dial)
        mic_col.addWidget(self._mic_dial, alignment=Qt.AlignmentFlag.AlignHCenter)
        self._btn_test_mic = QPushButton("Record mic")
        self._btn_test_mic.setFixedWidth(110)
        self._btn_test_mic.setToolTip("Record 3 seconds of audio and play it back")
        self._btn_test_mic.clicked.connect(self._start_mic_record)
        mic_col.addWidget(self._btn_test_mic, alignment=Qt.AlignmentFlag.AlignHCenter)
        dials_row.addLayout(mic_col)

        dials_row.addStretch()
        ag_layout.addLayout(dials_row)

        # Internal state for mic record/playback test
        self._mic_test_proc = None
        self._mic_test_timer = QTimer()
        self._mic_test_timer.setInterval(60)   # ~16 fps
        self._mic_test_timer.timeout.connect(self._update_mic_level)
        self._mic_record_buf = bytearray()
        self._mic_record_timer = QTimer()
        self._mic_record_timer.setSingleShot(True)
        self._mic_record_timer.timeout.connect(self._finish_mic_record)

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

        # ── Levels group (iOS-style slim sliders) ─────────────────────────────
        levels_group = QGroupBox("Levels")
        lg2 = QVBoxLayout(levels_group)
        lg2.setContentsMargins(16, 12, 16, 12)
        lg2.setSpacing(10)

        def _ios_row(name: str, slider_attr: str, label_attr: str,
                     lo: int, hi: int, val: int, on_change):
            row = QHBoxLayout()
            row.setSpacing(10)
            ico = QLabel(name)
            ico.setFixedWidth(90)
            ico.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            ico.setStyleSheet("font-size: 12px; background: transparent;")
            row.addWidget(ico)
            sl = QSlider(Qt.Orientation.Horizontal)
            sl.setRange(lo, hi)
            sl.setValue(val)
            sl.setFixedHeight(20)
            sl.valueChanged.connect(on_change)
            _no_wheel(sl)
            setattr(self, slider_attr, sl)
            row.addWidget(sl, 1)
            lbl = QLabel(f"{val}%")
            lbl.setFixedWidth(36)
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            lbl.setStyleSheet("font-size: 11px; background: transparent;")
            setattr(self, label_attr, lbl)
            row.addWidget(lbl)
            return row

        lg2.addLayout(_ios_row("Speaker",     "_vol_slider",      "_vol_label",      0, 100, 80, self._on_volume_slider))
        lg2.addLayout(_ios_row("Ring",        "_ring_vol_slider", "_ring_vol_label", 0, 100, 80, self._on_ring_volume_slider))
        lg2.addLayout(_ios_row("Microphone",  "_mic_sens_slider", "_mic_sens_label", 0, 150, 80, self._on_mic_sensitivity_slider))

        layout.addWidget(levels_group)

        # ── Developer mode ────────────────────────────────────────────────────
        dev_group = QGroupBox("Developer")
        dev_layout = QVBoxLayout(dev_group)
        self._chk_dev_mode = QCheckBox("Developer mode")
        self._chk_dev_mode.setToolTip(
            "Show verbose debug output in the terminal"
        )
        self._chk_dev_mode.toggled.connect(self._on_dev_mode_toggled)
        dev_layout.addWidget(self._chk_dev_mode)
        layout.addWidget(dev_group)

        # ── Config file hint ──────────────────────────────────────────────────
        hint = QLabel("Advanced settings: ~/.config/handsfree/config.toml")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # ── About / Update ────────────────────────────────────────────────────
        layout.addWidget(self._build_about_group())

        layout.addStretch()

        # Populate devices when this tab is first shown
        self._audio_devices_loaded = False
        return outer

    # ── Settings helpers ──────────────────────────────────────────────────────

    def _build_about_group(self) -> "QGroupBox":
        from PyQt6.QtWidgets import QGroupBox, QVBoxLayout, QHBoxLayout, QPushButton, QLabel
        from core.version import __version__

        group = QGroupBox("About")
        v = QVBoxLayout(group)
        v.setSpacing(8)

        ver_lbl = QLabel(f"HandsFree  v{__version__}  —  MIT License, free to use and modify")
        ver_lbl.setStyleSheet("font-weight: bold;")
        ver_lbl.setWordWrap(True)
        v.addWidget(ver_lbl)

        update_row = QHBoxLayout()
        update_row.setSpacing(12)

        self._btn_check_update = QPushButton("Check for updates")
        self._btn_check_update.setMinimumWidth(160)
        self._btn_check_update.clicked.connect(self._check_for_updates)
        update_row.addWidget(self._btn_check_update)

        self._btn_download_update = QPushButton("Install update")
        self._btn_download_update.setMinimumWidth(160)
        self._btn_download_update.setVisible(False)
        self._btn_download_update.clicked.connect(self._start_download)
        update_row.addWidget(self._btn_download_update)

        self._update_status = QLabel("")
        self._update_status.setStyleSheet("font-size: 12px; color: #8e8e93;")
        self._update_status.setWordWrap(True)
        update_row.addWidget(self._update_status, 1)

        v.addLayout(update_row)

        # QThread workers kept alive as instance vars
        self._checker    = None
        self._downloader = None
        self._pending_asset_url = None
        return group

    def _check_for_updates(self):
        from core.updater import UpdateChecker
        self._btn_check_update.setEnabled(False)
        self._btn_check_update.setText("Checking…")
        self._btn_download_update.setVisible(False)
        self._update_status.setText("")

        self._checker = UpdateChecker(self)
        self._checker.update_available.connect(self._on_update_available)
        self._checker.up_to_date.connect(self._on_up_to_date)
        self._checker.check_failed.connect(self._on_check_failed)
        self._checker.start()

    def _silent_update_check(self):
        from core.updater import UpdateChecker
        self._silent_checker = UpdateChecker(self)
        self._silent_checker.update_available.connect(self._on_silent_update_available)
        self._silent_checker.start()

    def _on_silent_update_available(self, info: dict):
        self._update_badge.show_update(info["tag"])

    def _on_update_badge_clicked(self):
        # Switch to Settings tab and scroll to About group
        for i in range(self._tabs.count()):
            if self._tabs.tabText(i) == "Settings":
                self._tabs.setCurrentIndex(i)
                break

    def _on_update_available(self, info: dict):
        self._pending_asset_url = info.get("asset_url")
        self._update_status.setText(f"New version available: {info['tag']}")
        self._btn_download_update.setVisible(True)
        self._btn_check_update.setEnabled(True)
        self._btn_check_update.setText("Check for updates")

    def _on_up_to_date(self):
        self._btn_check_update.setEnabled(True)
        self._btn_check_update.setText("Check for updates")
        self._update_status.setText("You are running the latest version.")

    def _on_check_failed(self, msg: str):
        self._btn_check_update.setEnabled(True)
        self._btn_check_update.setText("Check for updates")
        self._update_status.setText(f"Update check failed: {msg}")

    def _start_download(self):
        from core.updater import UpdateDownloader, apply_update_linux
        if not self._pending_asset_url:
            return
        self._btn_download_update.setEnabled(False)
        self._update_status.setText("Downloading… 0%")

        self._downloader = UpdateDownloader(self._pending_asset_url, self)
        self._downloader.progress.connect(self._on_download_progress)
        self._downloader.finished.connect(self._on_download_finished)
        self._downloader.failed.connect(self._on_download_failed)
        self._downloader.start()

    def _on_download_progress(self, done: int, total: int):
        pct = int(done * 100 / total) if total else 0
        self._update_status.setText(f"Downloading… {pct}%")

    def _on_download_finished(self, tmp_path: str):
        from core.updater import apply_update_linux
        self._update_status.setText("Applying update and restarting…")
        apply_update_linux(tmp_path)

    def _on_download_failed(self, msg: str):
        self._btn_download_update.setEnabled(True)
        self._update_status.setText(f"Download failed: {msg}")

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

    def set_audio_selection(self, output_name: str, input_name: str, volume: int,
                            ring_volume: int = 80, mic_sensitivity: int = 80):
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

        for slider, label, value in (
            (self._vol_slider,      self._vol_label,      volume),
            (self._ring_vol_slider, self._ring_vol_label, ring_volume),
            (self._mic_sens_slider, self._mic_sens_label, mic_sensitivity),
        ):
            slider.blockSignals(True)
            slider.setValue(value)
            label.setText(f"{value}%")
            slider.blockSignals(False)

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

    def _on_ring_volume_slider(self, value: int):
        self._ring_vol_label.setText(f"{value}%")
        self.ring_volume_changed.emit(value)

    def _on_ring_volume_button(self, delta: int):
        new_val = max(0, min(100, self._ring_vol_slider.value() + delta))
        self._ring_vol_slider.setValue(new_val)

    def _on_mic_sensitivity_slider(self, value: int):
        self._mic_sens_label.setText(f"{value}%")
        self.mic_sensitivity_changed.emit(value)

    def _on_mic_sensitivity_button(self, delta: int):
        new_val = max(0, min(150, self._mic_sens_slider.value() + delta))
        self._mic_sens_slider.setValue(new_val)

    def _on_theme_combo_changed(self, name: str):
        self.apply_theme(name)
        self.theme_changed.emit(name)

    def _on_dev_mode_toggled(self, enabled: bool):
        import logging as _logging
        level = _logging.DEBUG if enabled else _logging.INFO
        _logging.getLogger().setLevel(level)
        if enabled:
            # Add the Dev Logs tab if not already there
            if not hasattr(self, "_devlogs_tab_index"):
                tab = self._build_devlogs_tab()
                self._devlogs_tab_index = self._tabs.addTab(tab, "Dev Logs")
            else:
                self._tabs.setTabVisible(self._devlogs_tab_index, True)
            self._qt_log_handler.setLevel(_logging.DEBUG)
            _logging.getLogger().addHandler(self._qt_log_handler)
        else:
            if hasattr(self, "_devlogs_tab_index"):
                self._tabs.setTabVisible(self._devlogs_tab_index, False)
            _logging.getLogger().removeHandler(self._qt_log_handler)

    def _build_devlogs_tab(self) -> QWidget:
        from PyQt6.QtWidgets import QListWidget, QListWidgetItem
        from PyQt6.QtCore import pyqtSignal as _sig
        import logging as _logging

        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # ── Call overlay preview ──────────────────────────────────────────────
        preview_group = QGroupBox("Call Screen Preview")
        pg_layout = QVBoxLayout(preview_group)
        pg_layout.setContentsMargins(10, 10, 10, 10)

        preview_container = QWidget()
        preview_container.setStyleSheet(
            "background: #1c1c1e; border-radius: 12px; border: 1px solid #5f6368;"
        )
        pc_layout = QVBoxLayout(preview_container)
        pc_layout.setContentsMargins(14, 12, 14, 12)
        pc_layout.setSpacing(10)

        # Top row: avatar + name/status
        top_row = QHBoxLayout()
        top_row.setSpacing(12)
        self._dev_avatar = QLabel("AB")
        self._dev_avatar.setFixedSize(52, 52)
        self._dev_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._dev_avatar.setStyleSheet(
            "border-radius: 26px; background: #3c4043; border: 2px solid #5f6368;"
            "font-size: 20px; font-weight: bold; color: white;"
        )
        top_row.addWidget(self._dev_avatar)
        info_col = QVBoxLayout()
        info_col.setSpacing(2)
        self._dev_name_lbl = QLabel("No active call")
        self._dev_name_lbl.setStyleSheet(
            "font-size: 14px; font-weight: bold; color: #e8eaed; background: transparent;"
        )
        info_col.addWidget(self._dev_name_lbl)
        self._dev_timer_lbl = QLabel("—")
        self._dev_timer_lbl.setStyleSheet(
            "font-size: 12px; color: #9aa0a6; background: transparent;"
        )
        info_col.addWidget(self._dev_timer_lbl)
        top_row.addLayout(info_col, 1)
        pc_layout.addLayout(top_row)

        # Button row (purely visual mock — not wired)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        mute_preview = QPushButton("Mute")
        mute_preview.setFixedHeight(36)
        mute_preview.setEnabled(False)
        mute_preview.setStyleSheet(
            "QPushButton{background:#2d2d2f;color:#e8eaed;border-radius:18px;"
            "font-size:13px;border:1px solid #5f6368;}"
        )
        btn_row.addWidget(mute_preview)
        end_preview = QPushButton("End Call")
        end_preview.setFixedHeight(36)
        end_preview.setEnabled(False)
        end_preview.setStyleSheet(
            "QPushButton{background:#ea4335;color:white;border-radius:18px;"
            "font-size:13px;font-weight:bold;}"
        )
        btn_row.addWidget(end_preview)
        pc_layout.addLayout(btn_row)

        pg_layout.addWidget(preview_container)
        layout.addWidget(preview_group)

        # ── Log list ──────────────────────────────────────────────────────────
        log_group = QGroupBox("Logs")
        lg_layout = QVBoxLayout(log_group)
        lg_layout.setContentsMargins(6, 6, 6, 6)

        self._dev_log_list = QListWidget()
        self._dev_log_list.setFrameShape(QListWidget.Shape.NoFrame)
        self._dev_log_list.setStyleSheet("""
            QListWidget { border: none; font-family: monospace; font-size: 11px; }
            QListWidget::item { padding: 1px 4px; border: none; }
        """)
        lg_layout.addWidget(self._dev_log_list)

        btn_clear = QPushButton("Clear")
        btn_clear.setFixedWidth(70)
        btn_clear.clicked.connect(self._dev_log_list.clear)
        lg_layout.addWidget(btn_clear, alignment=Qt.AlignmentFlag.AlignRight)

        layout.addWidget(log_group, 1)  # stretch — log list takes remaining space

        # ── Qt log handler ────────────────────────────────────────────────────
        self._qt_log_handler = _QtLogHandler(self._dev_log_list)
        self._qt_log_handler.setFormatter(
            _logging.Formatter("%(levelname)s  %(name)s — %(message)s")
        )

        return w

    def _update_dev_preview(self, name: str, timer: str, avatar_text: str = ""):
        """Called by on_call_active / on_call_ended to keep the preview in sync."""
        if not hasattr(self, "_dev_name_lbl"):
            return
        self._dev_name_lbl.setText(name)
        self._dev_timer_lbl.setText(timer)
        if avatar_text:
            self._dev_avatar.setText(avatar_text)

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

    def _start_mic_record(self):
        """Record 3 seconds from the selected mic input, then play it back."""
        import subprocess
        source = self._audio_input_combo.currentData() or ""
        self._mic_record_buf.clear()
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
            self._btn_test_mic.setEnabled(False)
            self._btn_test_mic.setText("Recording…")
            self._mic_test_timer.start()
            self._mic_record_timer.start(3000)   # record for 3 seconds
        except Exception as e:
            logger.warning("Mic record start failed: %s", e)
            self._btn_test_mic.setEnabled(True)

    def _update_mic_level(self):
        """Read a chunk from pacat, drive the dial, and accumulate into the buffer."""
        import math, select, struct
        if not self._mic_test_proc:
            return
        try:
            ready, _, _ = select.select([self._mic_test_proc.stdout], [], [], 0)
            if not ready:
                return
            chunk = self._mic_test_proc.stdout.read(640)
            if not chunk:
                return
        except Exception:
            return

        self._mic_record_buf.extend(chunk)
        n = len(chunk) // 2
        if n == 0:
            return
        samples = struct.unpack(f"{n}h", chunk[:n * 2])
        rms = math.sqrt(sum(s * s for s in samples) / n)
        level = 0 if rms < 1 else int(min(100, 20 * math.log10(rms / 32767.0) + 100))
        self._mic_dial.set_level(level)

    def _finish_mic_record(self):
        """Stop recording and play back the captured audio."""
        import subprocess, threading
        self._mic_test_timer.stop()
        if self._mic_test_proc:
            try:
                self._mic_test_proc.terminate()
                # Drain any bytes still in the pipe before the process fully exits
                remaining = self._mic_test_proc.stdout.read()
                if remaining:
                    self._mic_record_buf.extend(remaining)
                self._mic_test_proc.wait(timeout=1)
            except Exception:
                pass
            self._mic_test_proc = None
        self._mic_dial.set_level(0)

        pcm = bytes(self._mic_record_buf)
        self._mic_record_buf.clear()

        if not pcm:
            self._btn_test_mic.setText("Record mic")
            self._btn_test_mic.setEnabled(True)
            return

        sink = self._audio_output_combo.currentData() or ""
        vol  = self._vol_slider.value()
        cmd = [
            "pacat", "--playback", "--raw",
            "--format=s16le", "--rate=16000", "--channels=1",
            f"--volume={int(vol * 655.35)}",
        ]
        if sink:
            cmd.append(f"--device={sink}")

        self._btn_test_mic.setText("Playing…")

        def _play():
            try:
                proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                proc.communicate(input=pcm, timeout=10)
            except Exception as e:
                logger.debug("Mic playback error: %s", e)
            finally:
                QTimer.singleShot(0, self._finish_mic_playback)

        threading.Thread(target=_play, daemon=True).start()

    def _finish_mic_playback(self):
        self._mic_dial.set_level(0)
        self._btn_test_mic.setText("Record mic")
        self._btn_test_mic.setEnabled(True)

    # ── Public update methods (call from app.py via Qt signals) ───────────────

    @pyqtSlot(str)
    def on_connecting(self, name: str):
        self._device_combo.setVisible(False)
        self._btn_connect.setVisible(False)
        self._status_label.setText(f"Connecting to {name}…")
        # dot stays ⚠ amber

    @pyqtSlot(str)
    def on_connected(self, device_name: str):
        self._status_dot.setText("●")
        self._status_dot.setStyleSheet("color: #34a853; font-size: 14px; background: transparent;")
        self._status_label.setText(device_name)
        self._device_combo.setVisible(False)
        self._btn_connect.setVisible(False)
        self._btn_disconnect.setVisible(True)
        self._btn_sync.setVisible(True)
        self._contacts_widget.refresh()

    @pyqtSlot()
    def on_disconnected(self):
        self._status_dot.setText("⚠")
        self._status_dot.setStyleSheet("color: #ffffff; font-size: 14px; background: transparent;")
        self._status_label.setText("Not connected")
        self._device_combo.setVisible(True)
        self._btn_connect.setVisible(True)
        self._btn_disconnect.setVisible(False)
        self._btn_sync.setVisible(False)
        self._sync_widget.set_idle()

    @pyqtSlot()
    def on_sync_started(self):
        self._sync_widget.set_syncing()

    @pyqtSlot(int, int)
    def on_sync_done(self, _updated: int, total: int):
        self._sync_widget.set_synced(total)

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
                self._incall_avatar.setObjectName("incallAvatar")
                return name, photo_bytes

        words = name.split()
        initials = (words[0][0] + words[-1][0]).upper() if len(words) >= 2 else name[:2].upper() or "?"
        self._incall_avatar.setText(initials)
        self._incall_avatar.setObjectName("incallAvatar")
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
        self._update_dev_preview(display, "Calling…", display[:2].upper() if display else "?")

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
        self._update_dev_preview(display, "0:00", display[:2].upper() if display else "?")

    @pyqtSlot()
    def set_muted(self, muted: bool):
        if muted:
            self._mute_btn.setText("🔕")
            self._mute_btn.setToolTip("Calls muted — click to unmute")
        else:
            self._mute_btn.setText("🔔")
            self._mute_btn.setToolTip("Click to mute calls")

    def on_call_ended(self):
        self._call_timer.stop()
        self._call_banner.setVisible(False)
        self._dial_stack.setCurrentIndex(0)
        self._statusbar.showMessage("Call ended")
        self.refresh_call_log()
        self._update_dev_preview("No active call", "—", "")
        # Show red "Call Ended" overlay then auto-hide it
        self._call_overlay.show_ended()
        # Hide main window if it was opened automatically for this call
        if getattr(self, "_opened_for_call", False):
            self._opened_for_call = False
            self.hide()

    def set_devices(self, devices: list[dict]):
        self._devices = devices
        prev_path = self._device_combo.currentData()
        self._device_combo.blockSignals(True)
        self._device_combo.clear()
        restore_idx = 0
        for i, d in enumerate(devices):
            offline = d.get("offline", False)
            label = f"{d['name']} ({d['address']})" + (" — last seen" if offline else "")
            self._device_combo.addItem(label, userData=d["path"])
            if offline:
                # Dim the item to signal it's not currently in range
                self._device_combo.setItemData(
                    i, QColor("#888888"), Qt.ItemDataRole.ForegroundRole
                )
            if d["path"] == prev_path:
                restore_idx = i
        if prev_path:
            self._device_combo.setCurrentIndex(restore_idx)
        self._device_combo.blockSignals(False)

    def refresh_call_log(self):
        from datetime import datetime, timezone
        self._calllog_list.clear()
        logs = self._store.get_call_log(limit=200)
        for entry in logs:
            contact = self._store.get_contact_by_id(entry.contact_id) if entry.contact_id else None
            if not contact:
                contact = self._store.lookup_by_number(entry.number)
            name = contact.effective_name if contact else entry.number
            photo = contact.photo_data if contact else None

            # Format duration
            if entry.duration_sec >= 60:
                dur = f"{entry.duration_sec // 60}m {entry.duration_sec % 60:02d}s"
            elif entry.duration_sec > 0:
                dur = f"{entry.duration_sec}s"
            else:
                dur = ""

            # Format timestamp
            try:
                dt = datetime.fromisoformat(entry.started_at.replace("Z", "+00:00"))
                today = datetime.now(timezone.utc).date()
                ts = dt.strftime("%H:%M") if dt.date() == today else dt.strftime("%d %b  %H:%M")
            except Exception:
                ts = entry.started_at[:16]

            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, entry)
            row = _CallLogRow(name=name, photo=photo, direction=entry.direction,
                              timestamp=ts, duration=dur)
            from PyQt6.QtCore import QSize
            hint = row.sizeHint()
            item.setSizeHint(QSize(hint.width(), max(hint.height(), _CallLogRow._AVATAR_SIZE + 16)))
            self._calllog_list.addItem(item)
            self._calllog_list.setItemWidget(item, row)

    def _filter_calllog(self, text: str):
        q = text.casefold()
        for i in range(self._calllog_list.count()):
            item = self._calllog_list.item(i)
            entry = item.data(Qt.ItemDataRole.UserRole)
            row_widget = self._calllog_list.itemWidget(item)
            name = (row_widget._name_lbl.text() if row_widget else "").casefold()
            number = (entry.number if entry else "").casefold()
            item.setHidden(bool(q) and q not in name and q not in number)

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
        from ui.themes import VSCODE_DARK
        self._current_theme = VSCODE_DARK
        self._apply_theme_qss(VSCODE_DARK)

    def apply_theme(self, name: str):
        """Public — called by app.py on startup and when the user picks a theme."""
        from ui.themes import get
        t = get(name)
        self._current_theme = t
        self._apply_theme_qss(t)
        self._refresh_inline_styles(t)
        # Sync combo without re-triggering signal
        if hasattr(self, "_theme_combo"):
            self._theme_combo.blockSignals(True)
            idx = self._theme_combo.findText(name)
            if idx >= 0:
                self._theme_combo.setCurrentIndex(idx)
            self._theme_combo.blockSignals(False)
        # Repaint custom widgets
        if hasattr(self, "_spk_dial"):
            self._spk_dial.set_theme(t.dial_bg, t.dial_track)
        if hasattr(self, "_mic_dial"):
            self._mic_dial.set_theme(t.dial_bg, t.dial_track)
        if hasattr(self, "_sync_widget"):
            self._sync_widget.set_accent("#ffffff")

    def _refresh_inline_styles(self, t):
        """Re-apply the handful of styles that can't live in the global QSS."""
        # Status label colour follows the theme; dot colours are fixed (green / amber)
        self._status_label.setStyleSheet("font-size: 12px; color: #ffffff; background: transparent;")

    def _apply_theme_qss(self, t):
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background: {t.bg};
                color: {t.fg};
                font-family: "Segoe UI", "Ubuntu", sans-serif;
                font-size: 13px;
            }}
            #titleBar {{
                background: {t.bg2};
                border-bottom: 1px solid {t.border};
            }}
            #titleLabel {{
                color: {t.fg_dim};
                font-size: 12px;
                background: transparent;
            }}
            #titleBtn {{
                background: transparent;
                color: {t.fg_dim};
                border: none;
                font-size: 14px;
            }}
            #titleBtn:hover {{
                background: {t.bg3};
                color: {t.fg};
            }}
            #titleBtnClose {{
                background: transparent;
                color: {t.fg_dim};
                border: none;
                font-size: 14px;
            }}
            #titleBtnClose:hover {{
                background: #c0392b;
                color: #ffffff;
            }}
            QGroupBox {{
                border: 1px solid {t.border};
                border-radius: 6px;
                margin-top: 8px;
                padding: 4px;
            }}
            QGroupBox::title {{ color: {t.fg_dim}; padding: 0 4px; }}
            QTabWidget {{
                background: {t.bg};
            }}
            QTabWidget::pane {{
                border: none;
                background: {t.bg};
            }}
            QTabWidget::tab-bar {{ alignment: left; }}
            QTabBar {{
                background: {t.bg};
                border-bottom: 1px solid {t.border};
            }}
            QTabBar QToolButton {{
                background: {t.bg};
                border: none;
            }}
            QTabBar::tab {{
                background: {t.bg};
                color: {t.fg_dim};
                padding: 8px 18px;
                border: none;
                border-bottom: 2px solid transparent;
                min-width: 60px;
            }}
            QTabBar::tab:selected {{
                color: {t.fg};
                border-bottom: 2px solid {t.accent_blue};
            }}
            QTabBar::tab:hover:!selected {{
                color: {t.fg};
                background: {t.bg2};
            }}
            QListWidget {{
                background: {t.bg2}; border: 1px solid {t.border};
                border-radius: 4px;
            }}
            QListWidget::item:selected {{ background: {t.bg3}; }}
            #calllogList::item {{ padding: 6px 4px; border-bottom: 1px solid {t.bg2}; }}
            QLineEdit {{
                background: {t.bg2}; color: {t.fg};
                border: 1px solid {t.border}; border-radius: 4px;
                padding: 4px 8px;
            }}
            QPushButton {{
                background: {t.btn_bg}; color: {t.fg};
                border: none; border-radius: 4px; padding: 6px 14px;
            }}
            QPushButton:hover    {{ background: {t.btn_hover}; }}
            QPushButton:pressed  {{ background: {t.btn_pressed}; }}
            QPushButton:disabled {{ background: {t.bg2}; color: {t.fg_dim}; }}
            QComboBox {{
                background: {t.bg2}; color: {t.fg};
                border: 1px solid {t.border};
                border-radius: 6px;
                padding: 5px 10px;
                min-height: 32px;
            }}
            QComboBox:hover {{
                border-color: {t.accent_blue};
            }}
            QComboBox:focus {{
                border: 1.5px solid {t.accent_blue};
            }}
            QComboBox::drop-down {{
                border: none;
                width: 24px;
            }}
            QComboBox::down-arrow {{
                image: none;
                width: 0; height: 0;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid {t.fg_dim};
                margin-right: 8px;
            }}
            QComboBox QAbstractItemView {{
                background: {t.bg2};
                color: {t.fg};
                border: 1px solid {t.border};
                selection-background-color: {t.accent_blue};
                selection-color: {t.bg};
                outline: none;
            }}
            QComboBox QAbstractItemView::item {{
                min-height: 28px;
                padding: 4px 10px;
                background: {t.bg2};
                color: {t.fg};
            }}
            QComboBox QAbstractItemView::item:selected {{
                background: {t.accent_blue};
                color: {t.bg};
            }}
            QSlider::groove:horizontal {{
                background: {t.bg3}; height: 4px; border-radius: 2px;
            }}
            QSlider::sub-page:horizontal {{
                background: {t.accent_blue}; height: 4px; border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: white;
                width: 16px; height: 16px;
                margin: -6px 0; border-radius: 8px;
                border: none;
            }}
            QSlider::handle:horizontal:hover {{
                background: #e8eaed;
            }}
            QStatusBar {{ color: #ffffff; background: #007acc; }}
            QStatusBar QLabel {{ color: #ffffff; background: transparent; }}
            /* ── Call banner ── */
            #callBanner {{
                background: {t.bg_banner};
                border: 1px solid #ea4335;
                border-radius: 6px;
            }}
            #callBannerNumber {{
                font-size: 14px; font-weight: bold;
                color: {t.fg_banner}; background: transparent;
            }}
            #callBannerTimer {{
                font-size: 12px; color: {t.fg_dim}; background: transparent;
            }}
            /* ── Dial pad ── */
            #dialBtn {{
                border: none;
                border-radius: 36px;
                background: rgba(127,127,127,0.08);
            }}
            #dialBtn:hover   {{ background: rgba(127,127,127,0.16); }}
            #dialBtn:pressed {{ background: rgba(127,127,127,0.28); }}
            #dialBtnLabel {{
                font-size: 22px; font-weight: 300;
                color: {t.fg}; background: transparent;
            }}
            #dialBtnSub {{
                font-size: 8px; color: {t.fg_dim}; background: transparent;
                letter-spacing: 1px;
            }}
            #dialDelBtn {{
                font-size: 18px; border: none;
                background: transparent; color: {t.fg_dim};
                border-radius: 22px;
            }}
            #dialDelBtn:hover {{ background: rgba(127,127,127,0.12); }}
            #callBtn {{
                background: {t.accent_green}; color: white;
                border-radius: 26px; font-size: 17px; font-weight: 500;
                border: none;
            }}
            #callBtn:hover   {{ background: {t.accent_green}dd; }}
            #callBtn:pressed {{ background: {t.accent_green}aa; }}
            /* ── In-call page ── */
            #incallPage {{ background: {t.bg}; }}
            #incallAvatar {{
                border-radius: 44px; background: {t.bg3};
                border: 2px solid {t.border};
                font-size: 32px; font-weight: bold; color: {t.fg};
            }}
            #incallName {{
                font-size: 22px; font-weight: bold;
                color: {t.fg}; background: transparent;
            }}
            #incallSub  {{ font-size: 13px; color: {t.fg_dim}; background: transparent; }}
            #incallTimer {{ font-size: 16px; color: {t.fg_dim}; background: transparent; }}
            #incallVolBtn {{
                font-size: 22px; font-weight: bold;
                border-radius: 22px; background: {t.bg2}; color: {t.fg};
            }}
            #incallVolBtn:hover {{ background: {t.bg3}; }}
            #incallVolLbl {{ color: {t.fg_dim}; background: transparent; }}
            #incallDevLabel {{ color: {t.fg_dim}; font-size: 11px; background: transparent; }}
            #incallMuteBtn {{
                background: {t.bg2}; color: {t.fg};
                border-radius: 28px; font-size: 16px; border: 1px solid {t.border};
            }}
            #incallMuteBtn:hover   {{ background: {t.bg3}; }}
            #incallMuteBtn:checked {{
                background: #ea4335; color: white; border: none;
            }}
            #incallEndBtn {{
                background: #ea4335; color: white;
                border-radius: 28px; font-size: 18px; font-weight: bold;
            }}
            #incallEndBtn:hover   {{ background: #c5352a; }}
            #incallEndBtn:pressed {{ background: #a02820; }}
            /* ── Misc ── */
            #hintLabel {{ color: {t.fg_dim}; font-size: 11px; }}
            QScrollArea {{ background: {t.bg}; border: none; }}
            QScrollArea > QWidget > QWidget {{ background: {t.bg}; }}
            /* ── Scrollbars (VS Code style) ── */
            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: {t.fg_dim};
                min-height: 24px;
                border-radius: 4px;
                opacity: 0.4;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {t.fg};
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {{ background: transparent; }}
            QScrollBar:horizontal {{
                background: transparent;
                height: 8px;
                margin: 0;
            }}
            QScrollBar::handle:horizontal {{
                background: {t.fg_dim};
                min-width: 24px;
                border-radius: 4px;
            }}
            QScrollBar::handle:horizontal:hover {{
                background: {t.fg};
            }}
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal {{ width: 0; }}
            QScrollBar::add-page:horizontal,
            QScrollBar::sub-page:horizontal {{ background: transparent; }}
        """)


# ── Qt logging handler (feeds Dev Logs tab) ───────────────────────────────────

import logging as _logging_mod
from PyQt6.QtCore import QObject, pyqtSignal as _pyqtSignal


class _QtLogHandler(_logging_mod.Handler, QObject):
    """Thread-safe logging handler that appends records to a QListWidget."""

    _record_ready = _pyqtSignal(str, int)  # formatted message, levelno

    def __init__(self, list_widget):
        _logging_mod.Handler.__init__(self)
        QObject.__init__(self)
        self._list = list_widget
        self._record_ready.connect(self._append)

    def emit(self, record: _logging_mod.LogRecord):
        try:
            msg = self.format(record)
            self._record_ready.emit(msg, record.levelno)
        except Exception:
            self.handleError(record)

    def _append(self, msg: str, levelno: int):
        from PyQt6.QtWidgets import QListWidgetItem
        from PyQt6.QtGui import QColor
        item = QListWidgetItem(msg)
        if levelno >= _logging_mod.ERROR:
            item.setForeground(QColor("#ea4335"))
        elif levelno >= _logging_mod.WARNING:
            item.setForeground(QColor("#f0a500"))
        self._list.addItem(item)
        self._list.scrollToBottom()


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
        self._level     = 0       # 0-100
        self._label     = label
        self._bg_color  = "#2d2d2f"
        self._trk_color = "#3c4043"
        self.setFixedSize(self.SIZE, self.SIZE + 18)  # +18 for label below

    def set_level(self, level: int):
        level = max(0, min(100, level))
        if level != self._level:
            self._level = level
            self.update()

    def set_theme(self, bg_color: str, track_color: str):
        self._bg_color  = bg_color
        self._trk_color = track_color
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx, cy = self.SIZE // 2, self.SIZE // 2
        r = self.SIZE // 2 - 6

        # ── Background circle ─────────────────────────────────────────────────
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self._bg_color))
        p.drawEllipse(cx - r - 4, cy - r - 4, (r + 4) * 2, (r + 4) * 2)

        # ── Track arc (full sweep, dark) ──────────────────────────────────────
        rect = QRectF(cx - r, cy - r, r * 2, r * 2)
        START_ANGLE = 225    # degrees (7 o'clock), Qt units = degrees * 16
        SWEEP       = 270    # total sweep degrees

        track_pen = QPen(QColor(self._trk_color), 8, Qt.PenStyle.SolidLine,
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


class _SyncWidget(QWidget):
    """
    VS Code-style sync indicator shown in the status bar.

    States:
      idle    — hidden (no text, no icon)
      syncing — spinning two-arrow circle + "Syncing…"
      synced  — static two-arrow circle + "Synced  N"
    """

    _ICON = 16   # icon square size in px

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state   = "idle"   # "idle" | "syncing" | "synced"
        self._count   = 0
        self._angle   = 0        # rotation angle for spin animation
        self._accent  = "#ffffff"
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(30)   # ~33 fps
        self._spin_timer.timeout.connect(self._tick)

        self.setVisible(False)
        self._update_size()

    def set_accent(self, color: str):
        self._accent = color
        self.update()

    # ── Public API ────────────────────────────────────────────────────────────

    def set_idle(self):
        self._spin_timer.stop()
        self._state = "idle"
        self.setVisible(False)

    def set_syncing(self):
        self._state = "syncing"
        self._angle = 0
        self.setVisible(True)
        self._update_size()
        self._spin_timer.start()

    def set_synced(self, total: int):
        self._spin_timer.stop()
        self._state  = "synced"
        self._count  = total
        self._angle  = 0
        self.setVisible(True)
        self._update_size()
        self.update()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _tick(self):
        self._angle = (self._angle + 6) % 360
        self.update()

    def _label_text(self) -> str:
        if self._state == "syncing":
            return " Syncing…"
        if self._state == "synced":
            return f" Synced  {self._count}"
        return ""

    def _update_size(self):
        fm = self.fontMetrics()
        text_w = fm.horizontalAdvance(self._label_text())
        self.setFixedSize(self._ICON + text_w + 6, 20)

    def paintEvent(self, _event):
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx = self._ICON // 2
        cy = self.height() // 2
        r  = self._ICON // 2 - 2

        # Rotate around icon centre for spin effect
        p.save()
        p.translate(cx, cy)
        p.rotate(self._angle if self._state == "syncing" else 0)
        p.translate(-cx, -cy)

        color = QColor(self._accent)
        pen   = QPen(color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)

        rect = QRectF(cx - r, cy - r, r * 2, r * 2)

        # Top arc (↻ upper half) — 30° gap at bottom
        p.drawArc(rect, int(20 * 16), int(140 * 16))
        # Arrowhead at end of top arc
        end_rad = math.radians(20)
        ax = cx + r * math.cos(end_rad)
        ay = cy - r * math.sin(end_rad)
        self._draw_arrowhead(p, ax, ay, math.radians(20 - 90), r * 0.45)

        # Bottom arc (↻ lower half)
        p.drawArc(rect, int(200 * 16), int(140 * 16))
        end_rad2 = math.radians(200)
        ax2 = cx + r * math.cos(end_rad2)
        ay2 = cy - r * math.sin(end_rad2)
        self._draw_arrowhead(p, ax2, ay2, math.radians(200 - 90), r * 0.45)

        p.restore()

        # Label text
        text = self._label_text()
        if text:
            p.setPen(QColor("#ffffff"))
            font = self.font()
            font.setPointSize(9)
            p.setFont(font)
            p.drawText(self._ICON, 0, self.width() - self._ICON, self.height(),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       text)
        p.end()

    @staticmethod
    def _draw_arrowhead(p: QPainter, tip_x: float, tip_y: float,
                        angle_rad: float, size: float):
        import math
        spread = math.radians(28)
        for side in (-spread, spread):
            a = angle_rad + math.pi + side
            p.drawLine(
                int(tip_x), int(tip_y),
                int(tip_x + size * math.cos(a)),
                int(tip_y - size * math.sin(a)),
            )


class _UpdateBadge(QWidget):
    """Status-bar badge that appears when a newer version is available.
    Looks like '↑ Update v1.2.0' in amber; clicking it jumps to Settings."""

    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tag = ""
        self.setVisible(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("A new version is available — click to go to Settings")

    def show_update(self, tag: str):
        self._tag = tag
        fm = self.fontMetrics()
        text_w = fm.horizontalAdvance(self._text())
        self.setFixedSize(text_w + 10, 20)
        self.setVisible(True)
        self.update()

    def _text(self) -> str:
        return f"↑ Update {self._tag}"

    def paintEvent(self, _event):
        if not self._tag:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = self.font()
        font.setPointSize(9)
        p.setFont(font)
        p.setPen(QColor("#ffe066"))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._text())
        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class _CallLogRow(QWidget):
    """Call log row styled like the contact list: avatar + name + direction/time/duration."""

    _DIRECTION_ICON  = {"incoming": "↙", "outgoing": "↗", "missed": "✗"}
    _DIRECTION_COLOR = {"incoming": "#34a853", "outgoing": "#1a73e8", "missed": "#ea4335"}
    _AVATAR_SIZE = 42

    def __init__(self, name: str, photo: bytes | None,
                 direction: str, timestamp: str, duration: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumHeight(self._AVATAR_SIZE + 16)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(12)

        # Avatar — reuse the same helper from contacts_widget
        from ui.contacts_widget import _avatar_pixmap
        avatar = QLabel()
        avatar.setStyleSheet("background: transparent; border: none;")
        avatar.setPixmap(_avatar_pixmap(photo, name, self._AVATAR_SIZE))
        avatar.setFixedSize(self._AVATAR_SIZE, self._AVATAR_SIZE)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(avatar, 0, Qt.AlignmentFlag.AlignVCenter)

        # Text column
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        text_col.setContentsMargins(0, 0, 0, 0)

        self._name_lbl = QLabel(name)
        name_lbl = self._name_lbl
        name_lbl.setStyleSheet("background: transparent; border: none;")
        font = name_lbl.font()
        font.setPointSize(11)
        name_lbl.setFont(font)
        text_col.addWidget(name_lbl)

        icon  = self._DIRECTION_ICON.get(direction, "?")
        color = self._DIRECTION_COLOR.get(direction, "#9aa0a6")
        sub_text = f"{icon}  {timestamp}"
        if duration:
            sub_text += f"  ·  {duration}"
        sub_lbl = QLabel(sub_text)
        sub_lbl.setStyleSheet(f"background: transparent; border: none; color: {color}; font-size: 11px;")
        text_col.addWidget(sub_lbl)

        layout.addLayout(text_col, 1)