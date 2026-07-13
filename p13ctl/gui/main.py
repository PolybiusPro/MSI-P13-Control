"""Entry point for the p13ctl graphical control panel."""

from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from p13ctl.gui.window import MainWindow

def main(argv: list[str] | None = None) -> int:
    _ = argv
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    app = QApplication(sys.argv)
    app.setApplicationName("p13ctl")
    app.setOrganizationName("p13ctl")
    app.setDesktopFileName("p13ctl-gui")
    app.setQuitOnLastWindowClosed(True)

    try:
        window = MainWindow()
    except Exception as exc:  # noqa: BLE001
        QMessageBox.critical(None, "p13ctl GUI", f"Failed to start: {exc}")
        return 1

    window.show()
    code = app.exec()

    # Tear down widgets before PySide atexit destroyQCoreApplication.
    window.shutdown()
    window.hide()
    window.deleteLater()
    app.processEvents()

    return code

if __name__ == "__main__":
    raise SystemExit(main())
