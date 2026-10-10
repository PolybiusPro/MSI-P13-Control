"""System tray icon: Show, current mode, Quit."""

from __future__ import annotations

from PySide6.QtCore import QObject
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMenu, QStyle, QSystemTrayIcon

from .window import MainWindow


class PanelTray(QObject):
    def __init__(self, window: MainWindow, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._window = window
        icon = QIcon.fromTheme("computer")
        if icon.isNull():
            icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip("MSI P13 Control")

        self._menu = QMenu()
        show_action = self._menu.addAction("Show")
        show_action.triggered.connect(window.show_and_raise)
        self._mode_action = self._menu.addAction("Mode: Off")
        self._mode_action.setEnabled(False)
        self._menu.addSeparator()
        quit_action = self._menu.addAction("Quit")
        quit_action.triggered.connect(window.request_quit)
        self._tray.setContextMenu(self._menu)

        self._tray.activated.connect(self._on_activated)
        window.mode_text_changed.connect(self.set_mode_text)
        window.notify.connect(self.notify)
        self._tray.show()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._window.show_and_raise()

    def set_mode_text(self, text: str) -> None:
        self._mode_action.setText(f"Mode: {text}")

    def notify(self, title: str, message: str) -> None:
        self._tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Critical, 8000)
