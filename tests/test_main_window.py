"""Tests for ui/main_window.py — main window logic."""
import types
from unittest.mock import MagicMock


class TestConnectButton:
    def test_connect_not_emitted_when_path_is_none(self):
        """Connect button must do nothing when BT is off and path is None."""
        from ui.main_window import MainWindow

        emitted = []
        win = types.SimpleNamespace(
            _device_combo=MagicMock(),
            connect_requested=MagicMock(emit=lambda p: emitted.append(p)),
        )
        win._device_combo.currentIndex.return_value = 0
        win._device_combo.itemData.return_value = None

        MainWindow._on_connect_clicked(win)

        assert emitted == []

    def test_connect_emitted_for_valid_path(self):
        from ui.main_window import MainWindow

        emitted = []
        win = types.SimpleNamespace(
            _device_combo=MagicMock(),
            connect_requested=MagicMock(emit=lambda p: emitted.append(p)),
        )
        win._device_combo.currentIndex.return_value = 0
        win._device_combo.itemData.return_value = "/org/bluez/hci0/dev_AA"

        MainWindow._on_connect_clicked(win)

        assert emitted == ["/org/bluez/hci0/dev_AA"]
