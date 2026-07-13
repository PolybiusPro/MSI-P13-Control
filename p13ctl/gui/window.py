"""Main control window for p13ctl GUI."""

from __future__ import annotations

import logging
import subprocess

from PySide6.QtCore import QStandardPaths, QThread
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from p13ctl.device import list_devices
from p13ctl.display.artinchip import ArtinchipDisplay, DisplayError
from p13ctl.display.layout import (
    find_connected_virtual_output,
    load_display_config,
    reset_saved_layout,
    resolve_stream_settings,
    save_display_config,
)
from p13ctl.display.session import stop_active_sessions, wait_for_display_usb

from .service import (
    mirror_is_running,
    mirror_service_installed,
    start_mirror_service,
    stop_mirror_service,
)
from .workers import MirrorWorker, SysmonWorker, TaskWorker

_LOGGER = logging.getLogger(__name__)

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MSI P13 Control")
        self.setMinimumWidth(420)

        self._mirror_worker: MirrorWorker | None = None
        self._sysmon_worker: SysmonWorker | None = None
        self._action_worker: TaskWorker | None = None
        self._workers: list[QThread] = []
        self._closing = False

        self._build_ui()
        self._refresh_status()

        self._poll_timer = self.startTimer(2000)

    def timerEvent(self, event) -> None:  # noqa: N802
        if event.timerId() == self._poll_timer and not self._closing:
            self._refresh_mirror_state()

    def _build_ui(self) -> None:
        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        layout.addWidget(self._device_group(root))
        layout.addWidget(self._mirror_group(root))
        layout.addWidget(self._actions_group(root))
        layout.addStretch()

        self._status = QStatusBar(self)
        self.setStatusBar(self._status)

    def _device_group(self, parent: QWidget) -> QGroupBox:
        box = QGroupBox("Device", parent)
        form = QFormLayout(box)
        self._device_state = QLabel("Checking…", box)
        self._device_serial = QLabel("—", box)
        self._device_model = QLabel("—", box)
        form.addRow("Status", self._device_state)
        form.addRow("Serial", self._device_serial)
        form.addRow("Model", self._device_model)

        refresh = QPushButton("Refresh", box)
        refresh.clicked.connect(self._refresh_status)
        form.addRow(refresh)
        return box

    def _mirror_group(self, parent: QWidget) -> QGroupBox:
        box = QGroupBox("Extended display", parent)
        layout = QVBoxLayout(box)

        self._mirror_status = QLabel("Checking…", box)
        layout.addWidget(self._mirror_status)

        btn_wrap = QWidget(box)
        row = QHBoxLayout(btn_wrap)
        row.setContentsMargins(0, 0, 0, 0)
        self._mirror_start = QPushButton("Start", btn_wrap)
        self._mirror_stop = QPushButton("Stop", btn_wrap)
        self._mirror_start.clicked.connect(self._start_mirror)
        self._mirror_stop.clicked.connect(self._stop_mirror)
        row.addWidget(self._mirror_start)
        row.addWidget(self._mirror_stop)
        row.addStretch()
        layout.addWidget(btn_wrap)

        hint = QLabel(
            "Creates an EVDI virtual monitor as an extended display. "
            "Adjust placement in System Settings; settings are saved when it stops.",
            box,
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        layout.addWidget(hint)
        return box

    def _actions_group(self, parent: QWidget) -> QGroupBox:
        box = QGroupBox("Quick actions", parent)
        row = QHBoxLayout(box)

        test_btn = QPushButton("Test pattern", box)
        test_btn.clicked.connect(self._show_test_pattern)
        row.addWidget(test_btn)

        image_btn = QPushButton("Show image…", box)
        image_btn.clicked.connect(self._show_image)
        row.addWidget(image_btn)

        self._sysmon_btn = QPushButton("System monitor", box)
        self._sysmon_btn.clicked.connect(self._toggle_sysmon)
        row.addWidget(self._sysmon_btn)

        reset_btn = QPushButton("Reset saved layout", box)
        reset_btn.clicked.connect(self._reset_layout)
        row.addWidget(reset_btn)

        row.addStretch()
        return box

    def _worker_alive(self, worker: QThread | None) -> bool:
        if worker is None:
            return False
        try:
            return worker.isRunning()
        except RuntimeError:
            return False

    def _track_worker(self, worker: QThread) -> QThread:
        """Keep a Python reference until the thread finishes (no deleteLater)."""
        self._workers.append(worker)

        def _done() -> None:
            try:
                self._workers.remove(worker)
            except ValueError:
                pass

        worker.finished.connect(_done)
        return worker

    def _stream_values(self) -> dict[str, int]:
        return resolve_stream_settings(
            load_display_config(),
            fps=None,
            quality=None,
            rotate=None,
        )

    def _refresh_status(self) -> None:
        if self._closing:
            return
        devices = list_devices()
        if not devices:
            self._device_state.setText("Not connected")
            self._device_serial.setText("—")
            self._device_model.setText("—")
            self._refresh_mirror_state()
            return

        dev = devices[0]
        self._device_state.setText("Connected")
        self._device_serial.setText(dev.serial or "—")
        self._device_model.setText(dev.product or "MSI MPG CoreLiquid P13")
        self._refresh_mirror_state()

    def _mirror_running(self) -> bool:
        if mirror_service_installed():
            return mirror_is_running()
        return self._worker_alive(self._mirror_worker)

    def _refresh_mirror_state(self) -> None:
        if self._closing:
            return
        running = self._mirror_running()
        if running:
            self._mirror_status.setText("Running")
            self._mirror_start.setEnabled(False)
            self._mirror_stop.setEnabled(True)
        else:
            backend = "systemd service" if mirror_service_installed() else "in-process"
            self._mirror_status.setText(f"Stopped ({backend})")
            self._mirror_start.setEnabled(True)
            self._mirror_stop.setEnabled(False)

        sysmon_running = self._worker_alive(self._sysmon_worker)
        self._sysmon_btn.setText("Stop sysmon" if sysmon_running else "System monitor")

    def _start_mirror(self) -> None:
        if mirror_service_installed():
            try:
                start_mirror_service()
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or exc.stdout or str(exc)).strip()
                self._show_error("Could not start extended display service", detail)
                return
            self._status.showMessage("Extended display started", 3000)
            self._refresh_mirror_state()
            return

        if self._worker_alive(self._mirror_worker):
            return

        stream = self._stream_values()
        self._mirror_worker = MirrorWorker(
            fps=stream["fps"],
            quality=stream["quality"],
            rotate=stream["rotate"],
            parent=self,
        )
        self._mirror_worker.error.connect(self._on_mirror_error)
        self._mirror_worker.stopped.connect(self._on_mirror_stopped)
        self._mirror_worker.start()
        self._status.showMessage("Extended display started", 3000)
        self._refresh_mirror_state()

    def _on_mirror_error(self, msg: str) -> None:
        self._show_error("Extended display failed", msg)

    def _persist_layout_now(self) -> bool:
        """Save current virtual monitor placement while the output is still active."""
        output = find_connected_virtual_output()
        if output is None:
            return False
        try:
            save_display_config(virtual_output=output)
            return True
        except DisplayError as exc:
            _LOGGER.warning("could not save layout: %s", exc)
            return False

    def _stop_mirror(self) -> None:
        if mirror_service_installed() and mirror_is_running():
            if self._persist_layout_now():
                self._status.showMessage("Layout saved", 2000)
            try:
                stop_mirror_service()
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or exc.stdout or str(exc)).strip()
                self._show_error("Could not stop extended display service", detail)
                return
            self._status.showMessage("Extended display stopped", 3000)
            self._refresh_mirror_state()
            return

        if self._worker_alive(self._mirror_worker):
            if self._persist_layout_now():
                self._status.showMessage("Layout saved", 2000)
            assert self._mirror_worker is not None
            self._mirror_worker.request_stop()
            self._mirror_worker.wait(8000)
            self._mirror_worker = None
            self._status.showMessage("Extended display stopped", 3000)
        self._refresh_mirror_state()

    def _on_mirror_stopped(self) -> None:
        self._mirror_worker = None
        self._refresh_mirror_state()

    def _pick_image_file(self) -> str | None:
        start_dir = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.PicturesLocation
        )
        dialog = QFileDialog(self, "Choose image", start_dir)
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        dialog.setNameFilter("Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp)")
        if dialog.exec() != QFileDialog.DialogCode.Accepted:
            return None
        files = dialog.selectedFiles()
        return files[0] if files else None

    def _stop_streaming_workers(self, *, wait_ms: int = 8000) -> None:
        """Stop in-process extended display / sysmon and wait for USB release."""
        stop_active_sessions()
        if self._worker_alive(self._sysmon_worker):
            assert self._sysmon_worker is not None
            self._sysmon_worker.request_stop()
            self._sysmon_worker.wait(wait_ms)
            self._sysmon_worker = None
        if self._worker_alive(self._mirror_worker):
            assert self._mirror_worker is not None
            self._mirror_worker.request_stop()
            self._mirror_worker.wait(wait_ms)
            self._mirror_worker = None

    def _prepare_static_display(self) -> bool:
        """Stop streaming modes so a static image stays on the panel."""
        if self._worker_alive(self._sysmon_worker):
            reply = QMessageBox.question(
                self,
                "Stop system monitor?",
                "Stop the system monitor to show a static image?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return False

        if self._mirror_running():
            reply = QMessageBox.question(
                self,
                "Stop extended display?",
                "The extended display must stop before showing a static image.\n\n"
                "Stop it now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return False
            self._stop_mirror()

        self._stop_streaming_workers()
        try:
            wait_for_display_usb()
        except DisplayError as exc:
            self._show_error("Display busy", str(exc))
            return False
        return True

    def _show_test_pattern(self) -> None:
        if not self._prepare_static_display():
            return
        stream = self._stream_values()

        def _run() -> None:
            with ArtinchipDisplay(rotate=stream["rotate"]) as disp:
                disp.show_test_pattern()

        self._run_task(_run, "Test pattern sent")

    def _show_image(self) -> None:
        path = self._pick_image_file()
        if not path:
            return
        if not self._prepare_static_display():
            return
        stream = self._stream_values()

        def _run() -> None:
            with ArtinchipDisplay(rotate=stream["rotate"]) as disp:
                disp.show_static_file(path)

        self._run_task(_run, f"Image sent: {path}")

    def _toggle_sysmon(self) -> None:
        if self._worker_alive(self._sysmon_worker):
            assert self._sysmon_worker is not None
            self._sysmon_worker.request_stop()
            self._sysmon_worker.wait(8000)
            self._sysmon_worker = None
            self._status.showMessage("System monitor stopped", 3000)
            self._refresh_mirror_state()
            return

        if self._mirror_running():
            reply = QMessageBox.question(
                self,
                "Stop extended display?",
                "System monitor needs exclusive USB access.\nStop the extended display?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            self._stop_mirror()
            try:
                wait_for_display_usb()
            except DisplayError as exc:
                self._show_error("Display busy", str(exc))
                return

        stream = self._stream_values()
        self._sysmon_worker = SysmonWorker(rotate=stream["rotate"], parent=self)
        self._sysmon_worker.error.connect(self._on_sysmon_error)
        self._sysmon_worker.stopped.connect(self._on_sysmon_stopped)
        self._sysmon_worker.start()
        self._status.showMessage("System monitor running", 3000)
        self._refresh_mirror_state()

    def _on_sysmon_error(self, msg: str) -> None:
        self._show_error("System monitor failed", msg)

    def _on_sysmon_stopped(self) -> None:
        self._sysmon_worker = None
        self._refresh_mirror_state()

    def _reset_layout(self) -> None:
        answer = QMessageBox.question(
            self,
            "Reset saved layout",
            "Delete saved display settings and use defaults on next extended display start?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            reset_saved_layout()
        except DisplayError as exc:
            self._show_error("Reset failed", str(exc))
            return
        self._status.showMessage(
            "Saved layout reset — stop and restart extended display, then stop again to save new placement",
            5000,
        )

    def _run_task(self, fn, success_message: str) -> None:
        if self._worker_alive(self._action_worker):
            self._status.showMessage("Another action is still running", 3000)
            return
        worker = TaskWorker(fn, parent=self)
        self._pending_action_message = success_message
        worker.finished_ok.connect(self._on_action_ok)
        worker.finished_err.connect(self._on_action_err)
        worker.finished.connect(self._on_action_finished)
        self._action_worker = worker
        self._track_worker(worker)
        worker.start()

    def _on_action_ok(self, _result: object) -> None:
        if not self._closing:
            self._status.showMessage(getattr(self, "_pending_action_message", "Done"), 5000)

    def _on_action_err(self, msg: str) -> None:
        self._show_error("Action failed", msg)

    def _on_action_finished(self) -> None:
        self._action_worker = None

    def _show_error(self, title: str, message: str) -> None:
        if self._closing:
            return
        _LOGGER.error("%s: %s", title, message)
        QMessageBox.critical(self, title, message)

    def shutdown(self) -> None:
        """Stop timers/workers and disconnect signals before widget teardown."""
        if self._closing:
            return
        self._closing = True
        try:
            self.killTimer(self._poll_timer)
        except Exception:  # noqa: BLE001
            pass
        stop_active_sessions()
        self._stop_streaming_workers(wait_ms=5000)
        for worker in list(self._workers):
            try:
                worker.finished.disconnect()
            except (RuntimeError, TypeError):
                pass
            if self._worker_alive(worker):
                worker.wait(3000)
        self._workers.clear()
        self._mirror_worker = None
        self._sysmon_worker = None
        self._action_worker = None

    def closeEvent(self, event) -> None:  # noqa: N802
        self.shutdown()
        super().closeEvent(event)
