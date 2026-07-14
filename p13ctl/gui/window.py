"""Main control window for p13ctl GUI."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QStandardPaths, Qt, QThread, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from p13ctl.device import list_devices
from p13ctl.display.artinchip import ArtinchipDisplay, DisplayError
from p13ctl.display.layout import (
    apply_content_rotation,
    blank_panel_off,
    find_connected_virtual_output,
    load_display_config,
    reset_saved_layout,
    resolve_stream_settings,
    restore_saved_brightness,
    save_display_config,
    set_panel_brightness,
)
from p13ctl.display.mode import (
    MODE_CLOCK,
    MODE_EXTENDED,
    MODE_IMAGE,
    MODE_OFF,
    MODE_SYSMON,
    MODE_TEST,
    get_saved_mode,
    update_saved_mode,
)
from p13ctl.display.session import stop_active_sessions, wait_for_display_usb
from p13ctl.hid.msi_p13 import P13HidController

from .service import (
    mirror_is_running,
    mirror_service_installed,
    start_mirror_service,
    stop_mirror_service,
)
from .workers import FaceWorker, MirrorWorker, TaskWorker

_LOGGER = logging.getLogger(__name__)

PANEL_SIZE = (480, 480)
ROTATIONS = (0, 90, 180, 270)

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MSI P13 Control")
        self.setMinimumWidth(420)

        self._mirror_worker: MirrorWorker | None = None
        self._sysmon_worker: FaceWorker | None = None
        self._action_worker: TaskWorker | None = None
        self._workers: list[QThread] = []
        self._closing = False
        self._updating_mode = False
        self._updating_rotation = False
        self._updating_brightness = False
        self._updating_clock_style = False
        self._updating_sysmon = False
        self._image_path: str | None = None
        self._active_mode: str | None = MODE_OFF

        # Debounce slider drags so each step doesn't open the HID device.
        self._brightness_timer = QTimer(self)
        self._brightness_timer.setSingleShot(True)
        self._brightness_timer.setInterval(300)
        self._brightness_timer.timeout.connect(self._apply_brightness)

        # Debounce monitor option toggles; each apply restarts the face worker.
        self._sysmon_opts_timer = QTimer(self)
        self._sysmon_opts_timer.setSingleShot(True)
        self._sysmon_opts_timer.setInterval(750)
        self._sysmon_opts_timer.timeout.connect(self._apply_sysmon_opts)

        self._build_ui()
        self._load_saved_mode()
        self._load_saved_rotation()
        self._load_saved_brightness()
        self._refresh_status()

        self._poll_timer = self.startTimer(2000)
        QTimer.singleShot(0, self._maybe_apply_saved_mode)

    def timerEvent(self, event) -> None:  # noqa: N802
        if event.timerId() == self._poll_timer and not self._closing:
            self._refresh_mode_status()

    def _build_ui(self) -> None:
        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        layout.addWidget(self._device_group(root))
        layout.addWidget(self._display_mode_group(root))
        layout.addWidget(self._panel_group(root))

        reset_btn = QPushButton("Reset saved layout", root)
        reset_btn.clicked.connect(self._reset_layout)
        layout.addWidget(reset_btn)
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

    def _display_mode_group(self, parent: QWidget) -> QGroupBox:
        box = QGroupBox("Display Mode", parent)
        layout = QVBoxLayout(box)

        self._mode_combo = QComboBox(box)
        self._mode_combo.addItem("Off", MODE_OFF)
        self._mode_combo.addItem("Extended display", MODE_EXTENDED)
        self._mode_combo.addItem("Test pattern", MODE_TEST)
        self._mode_combo.addItem("Image", MODE_IMAGE)
        self._mode_combo.addItem("System monitor", MODE_SYSMON)
        self._mode_combo.addItem("Clock", MODE_CLOCK)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_combo_changed)
        layout.addWidget(self._mode_combo)

        self._image_btn = QPushButton("Choose image…", box)
        self._image_btn.clicked.connect(self._choose_image)
        self._image_btn.setVisible(False)
        layout.addWidget(self._image_btn)

        self._clock_style_combo = QComboBox(box)
        for style in range(1, 7):
            self._clock_style_combo.addItem(f"Style {style}", style)
        self._clock_style_combo.currentIndexChanged.connect(self._on_clock_style_changed)
        self._clock_style_combo.setVisible(False)
        layout.addWidget(self._clock_style_combo)

        self._sysmon_opts = QWidget(box)
        opts = QVBoxLayout(self._sysmon_opts)
        opts.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(QLabel("Switch every", self._sysmon_opts))
        self._switch_combo = QComboBox(self._sysmon_opts)
        for sec in (5, 10, 15, 20, 30):  # same choices as the Windows app
            self._switch_combo.addItem(f"{sec} s", sec)
        self._switch_combo.setCurrentIndex(1)
        self._switch_combo.currentIndexChanged.connect(self._on_sysmon_opts_changed)
        row.addWidget(self._switch_combo)
        row.addStretch()
        opts.addLayout(row)
        grid = QGridLayout()
        self._stat_checks: list[QCheckBox] = []
        for i, stat in enumerate(self._available_stats()):
            label = " ".join(
                w if w in ("CPU", "GPU", "RAM", "SYS") else w.capitalize()
                for w in stat["title"].split()
            )
            check = QCheckBox(label, self._sysmon_opts)
            check.setProperty("key", stat["key"])
            check.setChecked(True)
            check.toggled.connect(self._on_sysmon_opts_changed)
            grid.addWidget(check, i // 2, i % 2)
            self._stat_checks.append(check)
        opts.addLayout(grid)
        self._sysmon_opts.setVisible(False)
        layout.addWidget(self._sysmon_opts)

        self._mode_status = QLabel("Off", box)
        self._mode_status.setStyleSheet("color: palette(mid);")
        self._mode_status.setWordWrap(True)
        layout.addWidget(self._mode_status)
        return box

    def _panel_group(self, parent: QWidget) -> QGroupBox:
        box = QGroupBox("Panel", parent)
        form = QFormLayout(box)
        self._rotation_combo = QComboBox(box)
        for degrees in ROTATIONS:
            self._rotation_combo.addItem(f"{degrees}°", degrees)
        self._rotation_combo.currentIndexChanged.connect(self._on_rotation_changed)
        form.addRow("Rotation", self._rotation_combo)

        row = QHBoxLayout()
        self._brightness_slider = QSlider(Qt.Orientation.Horizontal, box)
        self._brightness_slider.setRange(0, 100)
        self._brightness_slider.valueChanged.connect(self._on_brightness_changed)
        self._brightness_label = QLabel("100%", box)
        row.addWidget(self._brightness_slider)
        row.addWidget(self._brightness_label)
        form.addRow("Brightness", row)
        return box

    def _selected_mode(self) -> str:
        return str(self._mode_combo.currentData())

    def _selected_rotation(self) -> int:
        return int(self._rotation_combo.currentData())

    def _set_combo_mode(self, mode: str) -> None:
        index = self._mode_combo.findData(mode)
        if index < 0:
            return
        self._updating_mode = True
        self._mode_combo.setCurrentIndex(index)
        self._image_btn.setVisible(mode == MODE_IMAGE)
        self._clock_style_combo.setVisible(mode == MODE_CLOCK)
        self._sysmon_opts.setVisible(mode == MODE_SYSMON)
        self._updating_mode = False

    def _set_combo_rotation(self, degrees: int) -> None:
        index = self._rotation_combo.findData(degrees)
        if index < 0:
            return
        self._updating_rotation = True
        self._rotation_combo.setCurrentIndex(index)
        self._updating_rotation = False

    def _load_saved_rotation(self) -> None:
        config = load_display_config() or {}
        panel = config.get("panel") or {}
        try:
            degrees = int(panel.get("rotation", 0))
        except (TypeError, ValueError):
            degrees = 0
        if degrees not in ROTATIONS:
            degrees = 0
        self._set_combo_rotation(degrees)

    def _on_rotation_changed(self, _index: int) -> None:
        if self._updating_rotation or self._closing:
            return
        degrees = self._selected_rotation()
        self._apply_rotation(degrees)

    def _apply_rotation(self, degrees: int) -> None:
        def _run() -> str:
            with P13HidController() as hid_dev:
                hid_dev.set_rotate(degrees)
            # Release HID before USB display refresh; hidraw is exclusive.
            return apply_content_rotation(degrees)

        self._run_task(_run, f"Rotation set to {degrees}°")

    def _load_saved_brightness(self) -> None:
        config = load_display_config() or {}
        panel = config.get("panel") or {}
        try:
            value = int(panel.get("brightness", 100))
        except (TypeError, ValueError):
            value = 100
        value = max(0, min(100, value))
        self._updating_brightness = True
        self._brightness_slider.setValue(value)
        self._updating_brightness = False
        self._brightness_label.setText(f"{value}%")

    def _on_brightness_changed(self, value: int) -> None:
        self._brightness_label.setText(f"{value}%")
        if self._updating_brightness or self._closing:
            return
        self._brightness_timer.start()

    def _apply_brightness(self) -> None:
        if self._closing:
            return
        if self._worker_alive(self._action_worker):
            # Another HID action is in flight — try again after it finishes.
            self._brightness_timer.start()
            return
        value = self._brightness_slider.value()

        def _run() -> None:
            set_panel_brightness(value, persist=True)

        self._run_task(_run, f"Brightness set to {value}%")

    def _worker_alive(self, worker: QThread | None) -> bool:
        if worker is None:
            return False
        try:
            return worker.isRunning()
        except RuntimeError:
            return False

    def _track_worker(self, worker: QThread) -> QThread:
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
        else:
            dev = devices[0]
            self._device_state.setText("Connected")
            self._device_serial.setText(dev.serial or "—")
            self._device_model.setText(dev.product or "MSI MPG CoreLiquid P13")
        self._sync_combo_from_runtime()
        self._refresh_mode_status()

    def _service_running(self) -> bool:
        return mirror_service_installed() and mirror_is_running()

    def _service_display_mode(self) -> str | None:
        """Mode hosted by p13-display.service (it runs the saved mode), if active."""
        if not self._service_running():
            return None
        name = get_saved_mode()["name"]
        return name if name in (MODE_EXTENDED, MODE_SYSMON, MODE_CLOCK) else None

    def _mirror_running(self) -> bool:
        if self._service_running():
            return self._service_display_mode() == MODE_EXTENDED
        return self._worker_alive(self._mirror_worker)

    def _face_worker_mode(self) -> str:
        worker = self._sysmon_worker
        return MODE_CLOCK if worker is not None and worker.face == "clock" else MODE_SYSMON

    def _sync_combo_from_runtime(self) -> None:
        """If an external service is already running, reflect that in the combo."""
        if self._closing or self._updating_mode:
            return
        service_mode = self._service_display_mode()
        if service_mode is not None and self._active_mode != service_mode:
            self._active_mode = service_mode
            self._set_combo_mode(service_mode)
        elif (
            service_mode is None
            and self._worker_alive(self._sysmon_worker)
            and self._active_mode != self._face_worker_mode()
        ):
            self._active_mode = self._face_worker_mode()
            self._set_combo_mode(self._active_mode)

    def _refresh_mode_status(self) -> None:
        if self._closing:
            return
        mode = self._active_mode or MODE_OFF
        if mode == MODE_EXTENDED and self._mirror_running():
            text = "Extended display running"
        elif mode == MODE_SYSMON and (
            self._service_display_mode() == MODE_SYSMON or self._worker_alive(self._sysmon_worker)
        ):
            text = "System monitor running"
        elif mode == MODE_CLOCK and (
            self._service_display_mode() == MODE_CLOCK or self._worker_alive(self._sysmon_worker)
        ):
            text = f"Clock running (style {self._selected_clock_style()})"
        elif mode == MODE_TEST:
            text = "Test pattern on panel"
        elif mode == MODE_IMAGE:
            if self._image_path:
                text = f"Image on panel ({Path(self._image_path).name})"
            else:
                text = "Solid black — choose an image"
        else:
            text = "Off (brightness 0)"
        self._mode_status.setText(text)
        self._image_btn.setVisible(self._selected_mode() == MODE_IMAGE)
        self._clock_style_combo.setVisible(self._selected_mode() == MODE_CLOCK)
        self._sysmon_opts.setVisible(self._selected_mode() == MODE_SYSMON)

    def _on_mode_combo_changed(self, _index: int) -> None:
        if self._updating_mode or self._closing:
            return
        mode = self._selected_mode()
        self._image_btn.setVisible(mode == MODE_IMAGE)
        self._clock_style_combo.setVisible(mode == MODE_CLOCK)
        self._sysmon_opts.setVisible(mode == MODE_SYSMON)
        if mode == self._active_mode:
            self._refresh_mode_status()
            return
        self._apply_mode(mode)

    def _available_stats(self) -> list[dict]:
        try:
            from p13ctl.display.faces import HwSensors

            return HwSensors().read()
        except Exception as exc:  # noqa: BLE001 — sensors are best-effort in the GUI
            _LOGGER.warning("could not enumerate hardware stats: %s", exc)
            return []

    def _checked_stat_keys(self) -> list[str]:
        """Selected stat keys; empty list means no filter (all stats)."""
        checked = [str(cb.property("key")) for cb in self._stat_checks if cb.isChecked()]
        if not checked or len(checked) == len(self._stat_checks):
            return []
        return checked

    def _selected_switch(self) -> int:
        return int(self._switch_combo.currentData())

    def _on_sysmon_opts_changed(self, *_args) -> None:
        if self._updating_sysmon or self._closing:
            return
        self._sysmon_opts_timer.start()

    def _apply_sysmon_opts(self) -> None:
        if self._closing or self._active_mode != MODE_SYSMON:
            return
        self._persist_mode(MODE_SYSMON, switch=self._selected_switch(), items=self._checked_stat_keys())
        self._apply_mode(MODE_SYSMON)

    def _load_saved_sysmon(self, saved: dict) -> None:
        self._updating_sysmon = True
        index = self._switch_combo.findData(int(saved.get("switch") or 10))
        if index >= 0:
            self._switch_combo.setCurrentIndex(index)
        items = saved.get("items")
        for check in self._stat_checks:
            check.setChecked(items is None or check.property("key") in items)
        self._updating_sysmon = False

    def _selected_clock_style(self) -> int:
        return int(self._clock_style_combo.currentData())

    def _set_clock_style(self, style: int) -> None:
        index = self._clock_style_combo.findData(style)
        if index < 0:
            return
        self._updating_clock_style = True
        self._clock_style_combo.setCurrentIndex(index)
        self._updating_clock_style = False

    def _on_clock_style_changed(self, _index: int) -> None:
        if self._updating_clock_style or self._closing:
            return
        style = self._selected_clock_style()
        self._persist_mode(MODE_CLOCK, style=style)
        if self._active_mode == MODE_CLOCK:
            self._apply_mode(MODE_CLOCK)

    def _load_saved_mode(self) -> None:
        saved = get_saved_mode()
        self._image_path = saved.get("image")
        self._set_clock_style(int(saved.get("style") or 1))
        self._load_saved_sysmon(saved)
        self._set_combo_mode(saved["name"])

    def _persist_mode(
        self,
        name: str,
        *,
        image: str | None = None,
        style: int | None = None,
        switch: int | None = None,
        items: list[str] | None = None,
    ) -> None:
        try:
            update_saved_mode(name=name, image=image, style=style, switch=switch, items=items)
        except DisplayError as exc:
            _LOGGER.warning("could not save display mode: %s", exc)

    def _maybe_apply_saved_mode(self) -> None:
        """Apply the saved mode if nothing is already driving the panel."""
        if self._closing:
            return
        saved = get_saved_mode()["name"]
        service_mode = self._service_display_mode()
        if service_mode is not None:
            self._active_mode = service_mode
            self._set_combo_mode(service_mode)
            self._refresh_mode_status()
            return
        if self._worker_alive(self._sysmon_worker):
            self._active_mode = self._face_worker_mode()
            self._set_combo_mode(self._active_mode)
            self._refresh_mode_status()
            return
        if saved == MODE_OFF or saved == self._active_mode:
            self._active_mode = saved
            self._refresh_mode_status()
            return
        self._apply_mode(saved)

    def _apply_mode(self, mode: str) -> None:
        previous = self._active_mode
        self._stop_current_mode(save_layout=previous == MODE_EXTENDED)
        if mode == MODE_OFF:
            self._blank_display()
            self._active_mode = MODE_OFF
            self._persist_mode(MODE_OFF)
            self._status.showMessage("Display off (brightness 0)", 3000)
            self._refresh_mode_status()
            return
        restore_saved_brightness()
        ok = False
        if mode == MODE_EXTENDED:
            ok = self._start_extended()
        elif mode == MODE_TEST:
            ok = self._start_test_pattern()
        elif mode == MODE_IMAGE:
            ok = self._start_image_mode()
        elif mode == MODE_SYSMON:
            ok = self._start_sysmon()
        elif mode == MODE_CLOCK:
            ok = self._start_clock()
        if not ok:
            self._blank_display()
            self._active_mode = MODE_OFF
            self._persist_mode(MODE_OFF)
            self._set_combo_mode(MODE_OFF)
            self._refresh_mode_status()
            return
        self._active_mode = mode
        self._persist_mode(
            mode,
            image=self._image_path if mode == MODE_IMAGE else None,
            style=self._selected_clock_style() if mode == MODE_CLOCK else None,
            switch=self._selected_switch() if mode == MODE_SYSMON else None,
            items=self._checked_stat_keys() if mode == MODE_SYSMON else None,
        )
        self._refresh_mode_status()

    def _stop_current_mode(self, *, save_layout: bool) -> None:
        if self._service_running() or self._worker_alive(self._mirror_worker):
            self._stop_extended(save_layout=save_layout)
        if self._worker_alive(self._sysmon_worker):
            assert self._sysmon_worker is not None
            self._sysmon_worker.request_stop()
            self._sysmon_worker.wait(8000)
            self._sysmon_worker = None
        if self._worker_alive(self._action_worker):
            assert self._action_worker is not None
            self._action_worker.wait(3000)
        self._active_mode = MODE_OFF

    def _release_usb_for_static(self) -> bool:
        self._stop_streaming_workers()
        try:
            wait_for_display_usb()
        except DisplayError as exc:
            self._show_error("Display busy", str(exc))
            return False
        return True

    def _start_extended(self) -> bool:
        if self._mirror_running():
            return True
        # Persist before starting the login service so it runs the right mode.
        self._persist_mode(MODE_EXTENDED)
        if not self._release_usb_for_static():
            return False
        if mirror_service_installed():
            try:
                start_mirror_service()
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or exc.stdout or str(exc)).strip()
                self._show_error("Could not start extended display", detail)
                return False
            self._status.showMessage("Extended display started", 3000)
            return True

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
        return True

    def _stop_extended(self, *, save_layout: bool) -> None:
        if mirror_service_installed() and mirror_is_running():
            if save_layout and self._persist_layout_now():
                self._status.showMessage("Layout saved", 2000)
            try:
                stop_mirror_service()
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or exc.stdout or str(exc)).strip()
                self._show_error("Could not stop extended display", detail)
                return
        elif self._worker_alive(self._mirror_worker):
            if save_layout and self._persist_layout_now():
                self._status.showMessage("Layout saved", 2000)
            assert self._mirror_worker is not None
            self._mirror_worker.request_stop()
            self._mirror_worker.wait(8000)
            self._mirror_worker = None

    def _on_mirror_error(self, msg: str) -> None:
        self._active_mode = MODE_OFF
        self._persist_mode(MODE_OFF)
        self._show_error("Extended display failed", msg)
        self._set_combo_mode(MODE_OFF)
        self._refresh_mode_status()

    def _on_mirror_stopped(self) -> None:
        self._mirror_worker = None
        if self._active_mode == MODE_EXTENDED and not self._mirror_running():
            # External stop — reflect Off without fighting a user change mid-flight.
            if self._selected_mode() == MODE_EXTENDED:
                self._active_mode = MODE_OFF
                self._set_combo_mode(MODE_OFF)
        self._refresh_mode_status()

    def _persist_layout_now(self) -> bool:
        output = find_connected_virtual_output()
        if output is None:
            return False
        try:
            save_display_config(virtual_output=output)
            return True
        except DisplayError as exc:
            _LOGGER.warning("could not save layout: %s", exc)
            return False

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
        stop_active_sessions()
        if self._worker_alive(self._sysmon_worker):
            assert self._sysmon_worker is not None
            self._sysmon_worker.request_stop()
            self._sysmon_worker.wait(wait_ms)
            self._sysmon_worker = None
        if self._service_running() or self._worker_alive(self._mirror_worker):
            self._stop_extended(save_layout=self._mirror_running() or self._worker_alive(self._mirror_worker))

    def _blank_display(self) -> bool:
        """Off mode: stop streams and set brightness to 0 over HID."""
        self._stop_streaming_workers(wait_ms=5000)
        try:
            blank_panel_off()
            return True
        except DisplayError as exc:
            _LOGGER.warning("could not turn display off: %s", exc)
            return False

    def _show_black(self) -> None:
        """Queue a black frame for Image mode (async)."""
        stream = self._stream_values()

        def _run() -> None:
            with ArtinchipDisplay(rotate=stream["rotate"]) as disp:
                black = Image.new("RGB", PANEL_SIZE, (0, 0, 0))
                disp.send_image(black)

        self._run_task(_run, "Solid black on panel")

    def _start_test_pattern(self) -> bool:
        if not self._release_usb_for_static():
            return False
        stream = self._stream_values()

        def _run() -> None:
            with ArtinchipDisplay(rotate=stream["rotate"]) as disp:
                disp.show_test_pattern()

        self._run_task(_run, "Test pattern sent")
        return True

    def _start_image_mode(self) -> bool:
        """Enter image mode: restore saved image, or solid black until chosen."""
        if not self._release_usb_for_static():
            return False
        stream = self._stream_values()
        path = self._image_path
        if path and Path(path).is_file():

            def _run() -> None:
                with ArtinchipDisplay(rotate=stream["rotate"]) as disp:
                    disp.show_static_file(path)

            self._run_task(_run, f"Image sent: {path}")
            self._status.showMessage(f"Image mode — {Path(path).name}", 4000)
            return True

        self._image_path = None
        self._show_black()
        self._status.showMessage("Image mode — choose an image", 4000)
        return True

    def _choose_image(self) -> None:
        if self._selected_mode() != MODE_IMAGE:
            self._set_combo_mode(MODE_IMAGE)
        path = self._pick_image_file()
        if not path:
            return
        if self._active_mode != MODE_IMAGE:
            if not self._release_usb_for_static():
                return
            self._active_mode = MODE_IMAGE
        stream = self._stream_values()
        self._image_path = path
        self._persist_mode(MODE_IMAGE, image=path)

        def _run() -> None:
            with ArtinchipDisplay(rotate=stream["rotate"]) as disp:
                disp.show_static_file(path)

        self._run_task(_run, f"Image sent: {path}")
        self._refresh_mode_status()

    def _start_sysmon(self) -> bool:
        return self._start_face("sysmon", "System monitor")

    def _start_clock(self) -> bool:
        return self._start_face("clock", "Clock")

    def _start_face(self, face: str, label: str) -> bool:
        message = f"{label} started"
        if not self._release_usb_for_static():
            return False
        if mirror_service_installed():
            # Persist first, then let the login service host the face so it
            # keeps running after the GUI closes (same pattern as extended).
            mode = MODE_CLOCK if face == "clock" else MODE_SYSMON
            self._persist_mode(
                mode,
                style=self._selected_clock_style() if mode == MODE_CLOCK else None,
                switch=self._selected_switch() if mode == MODE_SYSMON else None,
                items=self._checked_stat_keys() if mode == MODE_SYSMON else None,
            )
            try:
                start_mirror_service()
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or exc.stdout or str(exc)).strip()
                self._show_error(f"Could not start {label.lower()}", detail)
                return False
            self._status.showMessage(message, 3000)
            return True
        stream = self._stream_values()
        self._sysmon_worker = FaceWorker(
            rotate=stream["rotate"],
            face=face,
            style=self._selected_clock_style(),
            switch=float(self._selected_switch()),
            items=self._checked_stat_keys() or None,
            parent=self,
        )
        self._sysmon_worker.error.connect(self._on_sysmon_error)
        self._sysmon_worker.stopped.connect(self._on_sysmon_stopped)
        self._sysmon_worker.start()
        self._status.showMessage(message, 3000)
        return True

    def _stale_face_signal(self) -> bool:
        """True when a replaced worker's queued signal arrives after a restart."""
        return self._sysmon_worker is not None and self.sender() is not self._sysmon_worker

    def _on_sysmon_error(self, msg: str) -> None:
        if self._stale_face_signal():
            return
        self._active_mode = MODE_OFF
        self._persist_mode(MODE_OFF)
        self._show_error("System monitor failed", msg)
        self._set_combo_mode(MODE_OFF)
        self._refresh_mode_status()

    def _on_sysmon_stopped(self) -> None:
        if self._stale_face_signal():
            return
        self._sysmon_worker = None
        if self._active_mode in (MODE_SYSMON, MODE_CLOCK) and self._selected_mode() == self._active_mode:
            self._active_mode = MODE_OFF
            self._set_combo_mode(MODE_OFF)
        self._refresh_mode_status()

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
            "Saved layout reset — switch Display Mode away from and back to "
            "Extended display after placing the panel",
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
            self._refresh_mode_status()

    def _on_action_err(self, msg: str) -> None:
        self._show_error("Action failed", msg)
        self._refresh_mode_status()

    def _on_action_finished(self) -> None:
        self._action_worker = None

    def _show_error(self, title: str, message: str) -> None:
        if self._closing:
            return
        _LOGGER.error("%s: %s", title, message)
        QMessageBox.critical(self, title, message)

    def shutdown(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            self.killTimer(self._poll_timer)
        except Exception:  # noqa: BLE001
            pass
        stop_active_sessions()
        # In-process workers die with the GUI; the p13-display service
        # (extended/sysmon/clock) is left running so the panel persists.
        if self._worker_alive(self._sysmon_worker):
            assert self._sysmon_worker is not None
            self._sysmon_worker.request_stop()
            self._sysmon_worker.wait(5000)
        if self._worker_alive(self._mirror_worker):
            assert self._mirror_worker is not None
            self._persist_layout_now()
            self._mirror_worker.request_stop()
            self._mirror_worker.wait(5000)
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
        self._active_mode = MODE_OFF

    def closeEvent(self, event) -> None:  # noqa: N802
        self.shutdown()
        super().closeEvent(event)
