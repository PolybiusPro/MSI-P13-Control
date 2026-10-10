"""Entry point for the p13ctl graphical control panel."""

from __future__ import annotations

import argparse
import logging
import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from p13ctl.gui.single_instance import SingleInstance
from p13ctl.gui.tray import PanelTray
from p13ctl.gui.window import MainWindow

_LOGGER = logging.getLogger(__name__)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="p13ctl-gui")
    parser.add_argument(
        "--minimized",
        action="store_true",
        help="start hidden in the system tray (used by login autostart)",
    )
    args, _unknown = parser.parse_known_args(argv)
    return args


def _noop() -> None:
    """Timer target: waking the Qt loop lets Python run its signal handlers."""


def _install_signal_handlers(window: MainWindow) -> None:
    """Route SIGTERM/SIGINT to the quit path, deferred to the Qt event loop.

    Running the quit path inside the signal handler would nest it in whatever
    Python code was interrupted (a mode switch, a USB wait), which could then
    resume and start a worker after the panel was blanked.
    """

    def _handler(_signum: int, _frame: object) -> None:
        QTimer.singleShot(0, window.request_quit)

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    app = QApplication(sys.argv)
    app.setApplicationName("p13ctl")
    app.setOrganizationName("p13ctl")
    app.setDesktopFileName("p13ctl-gui")
    app.setQuitOnLastWindowClosed(False)

    guard = SingleInstance("p13ctl-gui")
    if not guard.acquire(notify=not args.minimized):
        return 0

    try:
        window = MainWindow(autostart=args.minimized)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error("Failed to start: %s", exc)
        if not args.minimized:
            QMessageBox.critical(None, "p13ctl GUI", f"Failed to start: {exc}")
        guard.close()
        return 1

    tray = PanelTray(window)
    guard.show_requested.connect(window.show_and_raise)

    # SIGTERM from the session manager runs the same quit path as the tray.
    _install_signal_handlers(window)
    pump = QTimer()
    pump.timeout.connect(_noop)
    pump.start(250)

    if not args.minimized:
        window.show()
    code = app.exec()

    # Tear down widgets before PySide atexit destroyQCoreApplication.
    window.quit_app()
    guard.close()
    pump.stop()
    del tray
    window.hide()
    window.deleteLater()
    app.processEvents()

    return code


if __name__ == "__main__":
    raise SystemExit(main())
