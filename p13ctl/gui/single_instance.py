"""Single-instance guard: only one process may hold the USB display interface."""

from __future__ import annotations

import logging
import os

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

_LOGGER = logging.getLogger(__name__)

_PING = b"show"


class SingleInstance(QObject):
    """First process listens on a local socket; later launches ping it and exit."""

    show_requested = Signal()

    def __init__(self, key: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
        if runtime_dir and os.path.isdir(runtime_dir):
            # Per-user directory: other users cannot pre-create the socket.
            self._name = os.path.join(runtime_dir, f"{key}.sock")
        else:
            self._name = f"{key}-{os.getuid()}"
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_connection)

    def acquire(self, *, notify: bool = True) -> bool:
        probe = QLocalSocket(self)
        probe.connectToServer(self._name)
        if probe.waitForConnected(500):
            if notify:
                probe.write(_PING)
                probe.waitForBytesWritten(500)
            probe.abort()
            probe.deleteLater()
            return False
        probe.abort()
        probe.deleteLater()
        # Nobody answered: any socket file left behind is stale.
        QLocalServer.removeServer(self._name)
        if not self._server.listen(self._name):
            _LOGGER.warning(
                "single-instance socket unavailable: %s", self._server.errorString()
            )
        return True

    def close(self) -> None:
        self._server.close()

    def _on_connection(self) -> None:
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            sock.readyRead.connect(self._on_ready_signal)
            sock.disconnected.connect(self._on_disconnected)
            if sock.bytesAvailable():
                self._on_ready(sock)

    def _on_ready_signal(self) -> None:
        sender = self.sender()
        if isinstance(sender, QLocalSocket):
            self._on_ready(sender)

    def _on_ready(self, sock: QLocalSocket) -> None:
        if bytes(sock.readAll()).startswith(_PING):
            self.show_requested.emit()

    def _on_disconnected(self) -> None:
        sender = self.sender()
        if sender is not None:
            sender.deleteLater()
