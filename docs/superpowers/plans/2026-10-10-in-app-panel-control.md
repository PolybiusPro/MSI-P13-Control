# In-App Panel Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Qt GUI owns the P13 panel connection; no systemd user services run, closing the window hides it to the tray, and Quit from the tray (or SIGTERM) blanks the panel and exits.

**Architecture:** Strip the service code paths out of the existing `MainWindow` so the in-process `MirrorWorker`/`FaceWorker` are the only path (no headless controller refactor). Add a `QLocalServer` single-instance guard, a tray icon, a `--minimized` autostart entry, and a login-retry rule that never persists "off" on failure.

**Tech Stack:** Python 3.10+, PySide6 (QtWidgets, QtNetwork), pytest with `QT_QPA_PLATFORM=offscreen`, bash installer.

**Spec:** `docs/in-app-panel-design.md`

## Global Constraints

- `evdi-p13.service`, udev rules, dracut config and EVDI units are untouched.
- Autostart entry path: `~/.config/autostart/p13ctl-gui.desktop`, running `p13ctl-gui --minimized`.
- Retry interval at login: 10 seconds (parity with old `RestartSec=10`). Retry stops when the user selects a mode manually.
- The login apply path never persists Display Mode = "off" on failure.
- Quit sequence: stop workers, `wait_for_display_usb()`, `send_black_image()`, exit.
- Errors while the window is hidden surface as tray notifications, not modal dialogs.
- One-shot CLI commands (`display off|test|image|desktop`, `sysmon`, `clock`) stay.
- `--no-boot-display` (install.sh) is replaced by `--no-autostart`.
- Writing style for everything written to files: no em dashes, plain ASCII punctuation, professional commit messages.

## Test and commit conventions (read first)

- Run tests with `.venv/bin/python -m pytest -q` from the repo root. Baseline before any change: 40 passed. (`pytest` was pip-installed into `.venv` during planning; it is not in `pyproject.toml`.)
- The git index already holds the user's unrelated staged work (`p13ctl/display/artinchip.py`, `p13ctl/display/faces.py`, `tests/test_faces.py`, `tools/linux/p13-display.service.in`, `docs/in-app-panel-design.md`). **Never run a bare `git commit`.** Always commit with an explicit pathspec: `git commit -m "msg" -- path1 path2 ...`, so only this task's files are committed. For deleted files use `git rm -f <path>` first (the staged service unit has local edits, so plain `git rm` refuses), then include the path in the pathspec.
- `faces.py`/`artinchip.py` are not touched by this plan.

## Review Focus

- Stale socket from a crashed instance: a new launch must become the primary instance, not hang or exit. (Task 3)
- Second `--minimized` launch (login autostart firing while the app is already running) must not pop the window open. (Task 3)
- Tray unavailable (no StatusNotifier host, or not up yet at login): closing the window must not strand an invisible app. Decide at close time, not at startup. (Task 4)
- Quit triggered twice (tray Quit plus SIGTERM): one black frame, no exception. (Task 4)
- Panel absent at quit (unplugged or never enumerated): quit still exits cleanly, no dialog. (Task 4)
- Login failure must produce one notification, not one every 10 seconds. (Task 6)

---

### Task 1: Remove backend service code, sleepwatch, and the `display mode` CLI

**Files:**
- Delete: `p13ctl/display/service.py`, `p13ctl/display/sleepwatch.py`, `p13ctl/display/shutdown_inhibit.py`, `tests/test_service.py`, `tools/linux/p13-display.service.in`, `tools/linux/p13-panel-off.service.in`
- Modify: `p13ctl/display/mode.py`, `p13ctl/__main__.py`, `tests/test_mode.py`, `tests/test_extended_monitor.py`

**Interfaces:**
- Produces: `p13ctl.display.mode.send_black_image() -> None` (no service stop), still exported and used by `display off` and, in Task 4, by the GUI.
- Removes: `run_saved_display_mode`, `SYSMON_MAX_FPS`, `stop_display_service`, `cmd_display_mode`, `cmd_display_sleepwatch`.

- [ ] **Step 1: Update tests first (they should fail)**

In `tests/test_mode.py`, edit `test_send_black_image_uses_saved_rotation`: delete the `stopped` list, the `monkeypatch.setattr(mode, "stop_display_service", ...)` line, and the `assert stopped == [True]` line. Delete the whole `test_saved_system_monitor_caps_fps_and_uses_stream_quality` test.

In `tests/test_extended_monitor.py`: delete `test_saved_extended_mode_uses_evdi`, and change the import line `from p13ctl.display import mode, virtual_monitor` to `from p13ctl.display import virtual_monitor` (check with grep that `mode` is not used elsewhere in the file; only lines 74 and 87 used it).

- [ ] **Step 2: Run tests to see the failure**

Run: `.venv/bin/python -m pytest tests/test_mode.py -q`
Expected: `test_send_black_image_uses_saved_rotation` FAILS (the real `stop_display_service` runs `systemctl`, or at minimum is not stubbed). If it happens to pass, continue anyway; the removal below is required by the spec.

- [ ] **Step 3: Edit `p13ctl/display/mode.py`**

Remove: `import signal`, `import threading`, `from .service import stop_display_service`, `blank_panel_off` and `restore_saved_brightness` from the `.layout` import list, the `SYSMON_MAX_FPS = 10` constant, the `stop_display_service()` call, and the whole `run_saved_display_mode` function. Result for the tail of the file:

```python
def _stream() -> dict[str, int]:
    return resolve_stream_settings(
        load_display_config(),
        fps=None,
        quality=None,
        rotate=None,
    )

def send_black_image() -> None:
    """Send a solid black frame without changing the saved mode or brightness."""
    stream = _stream()
    with ArtinchipDisplay(rotate=stream["rotate"]) as disp:
        disp.send_image(Image.new("RGB", PANEL_SIZE, (0, 0, 0)))
```

The `.layout` import becomes:

```python
from .layout import (
    load_display_config,
    resolve_stream_settings,
    save_display_config,
)
```

Update the module docstring to `"""Saved Display Mode persistence for the GUI."""`.

- [ ] **Step 4: Edit `p13ctl/__main__.py`**

Delete `cmd_display_mode` and `cmd_display_sleepwatch` (around lines 130-143), and delete the two subparser blocks (`p_dmode = disp_sub.add_parser("mode", ...)` with its `set_defaults`, and `p_sw = disp_sub.add_parser("sleepwatch", ...)` with its `add_argument` and `set_defaults`).

- [ ] **Step 5: Delete files**

```bash
git rm -f p13ctl/display/service.py p13ctl/display/sleepwatch.py p13ctl/display/shutdown_inhibit.py \
  tests/test_service.py tools/linux/p13-display.service.in tools/linux/p13-panel-off.service.in
```

- [ ] **Step 6: Verify nothing else references the removed names**

Run: `grep -rnE "stop_display_service|run_saved_display_mode|sleepwatch|shutdown_inhibit|SYSMON_MAX_FPS|display\.service" p13ctl tests`
Expected: no output. (`p13ctl/gui/service.py` and `window.py` do not match these names; they are handled in Task 2.)

- [ ] **Step 7: Run the tests**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (the count drops by the deleted tests: 40 minus 2 service tests, minus 2 mode/extended tests = 36).

- [ ] **Step 8: Commit**

```bash
git commit -m "Remove display service helpers, sleepwatch, and display mode CLI" -- \
  p13ctl/display/mode.py p13ctl/__main__.py tests/test_mode.py tests/test_extended_monitor.py \
  p13ctl/display/service.py p13ctl/display/sleepwatch.py p13ctl/display/shutdown_inhibit.py \
  tests/test_service.py tools/linux/p13-display.service.in tools/linux/p13-panel-off.service.in
```

---

### Task 2: Strip service paths from `MainWindow` and add the offscreen test harness

**Files:**
- Delete: `p13ctl/gui/service.py`
- Modify: `p13ctl/gui/window.py`
- Create: `tests/conftest.py`, `tests/test_window.py`

**Interfaces:**
- Produces: `tests/conftest.py` fixture `qapp` (session-scoped `QApplication`, offscreen). `tests/test_window.py` fixture `make_window(saved_mode="sysmon", **kwargs) -> (MainWindow, persisted_names: list[str])` used by Tasks 4 and 6. It stubs every hardware touchpoint on the `p13ctl.gui.window` module and tears the window down.
- `MainWindow._mirror_running() -> bool` is now just `self._worker_alive(self._mirror_worker)`.

- [ ] **Step 1: Create `tests/conftest.py`**

```python
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app
```

- [ ] **Step 2: Create `tests/test_window.py` with the harness and a smoke test**

```python
from __future__ import annotations

import pytest

from p13ctl.gui import window as win

def _saved(name: str) -> dict:
    return {
        "name": name,
        "image": None,
        "style": 1,
        "monitor_style": 1,
        "switch": 10,
        "items": None,
        "background": None,
        "colors": None,
    }

@pytest.fixture
def make_window(monkeypatch, qapp):
    persisted: list[str] = []
    made: list[win.MainWindow] = []

    monkeypatch.setattr(win, "list_devices", lambda: [])
    monkeypatch.setattr(win, "load_display_config", lambda: {})
    monkeypatch.setattr(
        win, "update_saved_mode", lambda **kw: persisted.append(kw["name"])
    )
    monkeypatch.setattr(win, "restore_saved_brightness", lambda: None)
    monkeypatch.setattr(win, "blank_panel_off", lambda: None)
    monkeypatch.setattr(win, "stop_active_sessions", lambda: None)
    monkeypatch.setattr(win.MainWindow, "_available_stats", lambda self: [])

    def _make(saved_mode: str = "sysmon", **kwargs):
        monkeypatch.setattr(win, "get_saved_mode", lambda: _saved(saved_mode))
        window = win.MainWindow(**kwargs)
        made.append(window)
        return window, persisted

    yield _make

    for window in made:
        window.shutdown()
        window.deleteLater()

def test_window_has_no_service_helpers(make_window) -> None:
    window, _persisted = make_window()
    assert not hasattr(win, "mirror_service_installed")
    assert not hasattr(window, "_service_running")
    assert window._mirror_running() is False
```

- [ ] **Step 3: Run it to see it fail**

Run: `.venv/bin/python -m pytest tests/test_window.py -q`
Expected: FAIL (`mirror_service_installed` still exists).

- [ ] **Step 4: Edit `p13ctl/gui/window.py`**

Make these changes (line numbers are from the current file; use grep to re-find them):

1. Imports: delete `import subprocess` (confirm with `grep -n subprocess` that only the service branches used it) and the whole `from .service import (...)` block.
2. Delete `_service_running` and `_service_display_mode`. Replace `_mirror_running`:

```python
    def _mirror_running(self) -> bool:
        return self._worker_alive(self._mirror_worker)
```

3. Replace `_sync_combo_from_runtime`:

```python
    def _sync_combo_from_runtime(self) -> None:
        """Reflect an already-running face worker in the combo."""
        if self._closing or self._updating_mode:
            return
        if (
            self._worker_alive(self._sysmon_worker)
            and self._active_mode != self._face_worker_mode()
        ):
            self._active_mode = self._face_worker_mode()
            self._set_combo_mode(self._active_mode)
```

4. In `_refresh_mode_status` replace the sysmon and clock conditions:

```python
        elif mode == MODE_SYSMON and self._worker_alive(self._sysmon_worker):
            text = f"System monitor running (style {self._selected_sysmon_style()})"
        elif mode == MODE_CLOCK and self._worker_alive(self._sysmon_worker):
            text = f"Clock running (style {self._selected_clock_style()})"
```

5. Replace `_maybe_apply_saved_mode`:

```python
    def _maybe_apply_saved_mode(self) -> None:
        """Apply the saved mode at startup."""
        if self._closing:
            return
        saved = get_saved_mode()["name"]
        if saved == MODE_OFF:
            self._active_mode = MODE_OFF
            self._refresh_mode_status()
            return
        self._apply_mode(saved)
```

6. `_stop_current_mode`: the first condition becomes `if self._worker_alive(self._mirror_worker):`.
7. `_start_extended`: delete the `# Persist before starting the login service...` comment and the `self._persist_mode(MODE_EXTENDED)` line under it (the `_apply_mode` success path persists), and delete the entire `if mirror_service_installed(): ... return True` block. The method becomes: `if self._mirror_running(): return True`, `if not self._release_usb_for_static(): return False`, then the existing `stream = ...`/`MirrorWorker` code.
8. `_stop_extended`: keep only the worker branch:

```python
    def _stop_extended(self, *, save_layout: bool) -> None:
        if not self._worker_alive(self._mirror_worker):
            return
        if save_layout and self._persist_layout_now():
            self._status.showMessage("Layout saved", 2000)
        assert self._mirror_worker is not None
        self._mirror_worker.request_stop()
        self._mirror_worker.wait(8000)
        self._mirror_worker = None
```

9. `_stop_streaming_workers`: last two lines become

```python
        if self._worker_alive(self._mirror_worker):
            self._stop_extended(save_layout=True)
```

10. `_start_face`: delete the whole `if mirror_service_installed(): ... return True` block (from the `# Persist first, then let the login service` comment through `return True`). Keep the `FaceWorker` code after it.
11. `shutdown()`: replace the two-line comment about the p13-display service with `# Stop in-process workers before the process exits.`
12. `MirrorWorker` docstring in `p13ctl/gui/workers.py`: change to `"""Run the EVDI extended-display loop."""`.

- [ ] **Step 5: Delete `p13ctl/gui/service.py`**

```bash
git rm -f p13ctl/gui/service.py
```

- [ ] **Step 6: Verify leftovers and run tests**

Run: `grep -rnE "mirror_service_installed|mirror_is_running|start_mirror_service|stop_mirror_service|_service_running|_service_display_mode|p13-display" p13ctl tests`
Expected: no output.
Run: `.venv/bin/python -m pytest -q`
Expected: all pass, including the new smoke test.

- [ ] **Step 7: Commit**

```bash
git commit -m "GUI: remove systemd service paths, in-process workers only" -- \
  p13ctl/gui/window.py p13ctl/gui/workers.py p13ctl/gui/service.py tests/conftest.py tests/test_window.py
```

---

### Task 3: Single-instance guard

**Files:**
- Create: `p13ctl/gui/single_instance.py`, `tests/test_single_instance.py`

**Interfaces:**
- Produces: `SingleInstance(key: str, parent: QObject | None = None)` with
  - `acquire(*, notify: bool = True) -> bool`: `True` if this process is now the primary instance. `False` if another instance exists; when `notify` is true that instance is told to show its window.
  - signal `show_requested()` emitted in the primary instance when a second launch pings it.
  - `close() -> None`.

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

import socket
import time
import uuid

from p13ctl.gui.single_instance import SingleInstance

def _pump(qapp, cond, timeout: float = 2.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end and not cond():
        qapp.processEvents()
        time.sleep(0.01)
    return bool(cond())

def _key() -> str:
    return f"p13ctl-test-{uuid.uuid4().hex}"

def test_second_instance_pings_first(qapp) -> None:
    key = _key()
    first = SingleInstance(key)
    pings: list[int] = []
    first.show_requested.connect(lambda: pings.append(1))
    assert first.acquire() is True

    second = SingleInstance(key)
    assert second.acquire() is False
    assert _pump(qapp, lambda: pings)
    first.close()

def test_minimized_second_launch_does_not_ping(qapp) -> None:
    key = _key()
    first = SingleInstance(key)
    pings: list[int] = []
    first.show_requested.connect(lambda: pings.append(1))
    assert first.acquire() is True

    second = SingleInstance(key)
    assert second.acquire(notify=False) is False
    assert not _pump(qapp, lambda: pings, timeout=0.3)
    first.close()

def test_stale_socket_is_replaced(qapp) -> None:
    key = _key()
    dead = SingleInstance(key)
    assert dead.acquire() is True
    path = dead._server.fullServerName()
    dead.close()
    # Leave a socket file behind with nobody listening, like a crashed instance.
    stale = socket.socket(socket.AF_UNIX)
    stale.bind(path)
    stale.close()

    fresh = SingleInstance(key)
    assert fresh.acquire() is True
    fresh.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_single_instance.py -q`
Expected: FAIL with `ModuleNotFoundError: p13ctl.gui.single_instance`.

- [ ] **Step 3: Implement `p13ctl/gui/single_instance.py`**

```python
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
            probe.disconnectFromServer()
            return False
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
            sock.readyRead.connect(lambda s=sock: self._on_ready(s))
            sock.disconnected.connect(sock.deleteLater)
            if sock.bytesAvailable():
                self._on_ready(sock)

    def _on_ready(self, sock: QLocalSocket) -> None:
        if bytes(sock.readAll()).startswith(_PING):
            self.show_requested.emit()
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_single_instance.py -q`
Expected: 3 passed. If `test_stale_socket_is_replaced` errors on `bind` because `fullServerName()` is relative or the file was already removed, print `path` and adjust the test to bind at that absolute path; the implementation under test does not change.

- [ ] **Step 5: Commit**

```bash
git add p13ctl/gui/single_instance.py tests/test_single_instance.py
git commit -m "GUI: add single-instance guard" -- p13ctl/gui/single_instance.py tests/test_single_instance.py
```

---

### Task 4: Window lifecycle (hide on close, quit path, notifications)

**Files:**
- Modify: `p13ctl/gui/window.py`
- Test: `tests/test_window.py`

**Interfaces:**
- Consumes: `make_window` fixture (Task 2); `wait_for_display_usb` is already imported in `window.py`.
- Produces on `MainWindow`:
  - signals `notify = Signal(str, str)` (title, message) and `mode_text_changed = Signal(str)`
  - `show_and_raise() -> None`
  - `quit_app() -> None` (idempotent: `shutdown()`, `wait_for_display_usb()`, `send_black_image()`; never raises)
  - `request_quit() -> None` (`quit_app()` then `QApplication.quit()`)
  - `closeEvent`: hides when a system tray is available, otherwise quits.

- [ ] **Step 1: Write the failing tests (append to `tests/test_window.py`)**

```python
from PySide6.QtWidgets import QMessageBox, QSystemTrayIcon

from p13ctl.display.artinchip import DisplayError

def test_quit_app_blanks_after_workers_stop(make_window, monkeypatch) -> None:
    window, _ = make_window()
    order: list[str] = []
    monkeypatch.setattr(window, "shutdown", lambda: order.append("shutdown"))
    monkeypatch.setattr(win, "wait_for_display_usb", lambda: order.append("wait"))
    monkeypatch.setattr(win, "send_black_image", lambda: order.append("black"))

    window.quit_app()
    window.quit_app()

    assert order == ["shutdown", "wait", "black"]

def test_quit_app_survives_missing_panel(make_window, monkeypatch) -> None:
    window, _ = make_window()
    sent: list[int] = []

    def _missing() -> None:
        raise DisplayError("Artinchip display 33c3:0e02 not found")

    monkeypatch.setattr(win, "wait_for_display_usb", _missing)
    monkeypatch.setattr(win, "send_black_image", lambda: sent.append(1))
    monkeypatch.setattr(
        QMessageBox, "critical", lambda *a, **k: pytest.fail("modal on quit")
    )

    window.quit_app()

    assert sent == []

def test_close_hides_window_when_tray_available(make_window, monkeypatch) -> None:
    window, _ = make_window()
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))
    quits: list[int] = []
    monkeypatch.setattr(window, "request_quit", lambda: quits.append(1))
    window.show()

    window.close()

    assert not window.isVisible()
    assert not window._closing
    assert quits == []

def test_close_quits_when_no_tray(make_window, monkeypatch) -> None:
    window, _ = make_window()
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: False))
    quits: list[int] = []
    monkeypatch.setattr(window, "request_quit", lambda: quits.append(1))
    window.show()

    window.close()

    assert quits == [1]

def test_error_while_hidden_notifies_instead_of_dialog(make_window, monkeypatch) -> None:
    window, _ = make_window()
    monkeypatch.setattr(
        QMessageBox, "critical", lambda *a, **k: pytest.fail("modal while hidden")
    )
    got: list[tuple[str, str]] = []
    window.notify.connect(lambda title, msg: got.append((title, msg)))

    window._show_error("Boom", "bad")

    assert got == [("Boom", "bad")]

def test_show_and_raise_shows_hidden_window(make_window) -> None:
    window, _ = make_window()
    assert not window.isVisible()
    window.show_and_raise()
    assert window.isVisible()

def test_mode_text_is_published(make_window) -> None:
    window, _ = make_window()
    got: list[str] = []
    window.mode_text_changed.connect(got.append)
    window._refresh_mode_status()
    assert got and got[-1] == window._mode_status.text()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_window.py -q`
Expected: new tests FAIL (`AttributeError` for `quit_app`, `notify`, etc.).

- [ ] **Step 3: Implement in `window.py`**

Imports: add `Signal` to the `PySide6.QtCore` import; add `QApplication` and `QSystemTrayIcon` to the `PySide6.QtWidgets` import list; change `from p13ctl.display.mode import (...)` to also import `send_black_image`.

Class body, directly under `class MainWindow(QMainWindow):`:

```python
    notify = Signal(str, str)
    mode_text_changed = Signal(str)
```

In `__init__`, next to `self._closing = False`, add `self._quit_done = False`.

Replace `_show_error`:

```python
    def _show_error(self, title: str, message: str) -> None:
        if self._closing:
            return
        _LOGGER.error("%s: %s", title, message)
        if self.isVisible():
            QMessageBox.critical(self, title, message)
        else:
            self.notify.emit(title, message)
```

In `_refresh_mode_status`, after `self._mode_status.setText(text)` add `self.mode_text_changed.emit(text)`.

Replace `closeEvent` and add the new methods:

```python
    def show_and_raise(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_app(self) -> None:
        """Stop the workers, blank the panel, and leave the app ready to exit."""
        if self._quit_done:
            return
        self._quit_done = True
        self.shutdown()
        try:
            wait_for_display_usb()
            send_black_image()
        except DisplayError as exc:
            _LOGGER.warning("could not blank panel on quit: %s", exc)

    def request_quit(self) -> None:
        self.quit_app()
        QApplication.quit()

    def closeEvent(self, event) -> None:  # noqa: N802
        if QSystemTrayIcon.isSystemTrayAvailable():
            event.ignore()
            self.hide()
            return
        # No tray to restore the window from: closing the window quits the app.
        self.request_quit()
        super().closeEvent(event)
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git commit -m "GUI: hide on close, add quit path with black frame, notify when hidden" -- \
  p13ctl/gui/window.py tests/test_window.py
```

---

### Task 5: Tray icon and `main()` wiring

**Files:**
- Create: `p13ctl/gui/tray.py`, `tests/test_tray.py`
- Modify: `p13ctl/gui/main.py`

**Interfaces:**
- Consumes: `MainWindow.show_and_raise`, `request_quit`, `notify`, `mode_text_changed` (Task 4); `SingleInstance` (Task 3). `MainWindow(autostart=...)` is added in Task 6; for this task `main.py` passes `autostart=args.minimized` only after Task 6 lands, so in this task construct `MainWindow()` and add the keyword in Task 6 Step 3.
- Produces: `PanelTray(window: MainWindow, parent=None)` with `set_mode_text(text: str)` and `notify(title: str, message: str)`.

- [ ] **Step 1: Write the failing test `tests/test_tray.py`**

```python
from __future__ import annotations

from p13ctl.gui.tray import PanelTray
from tests.test_window import make_window  # noqa: F401  (fixture reuse)

def test_tray_shows_mode_text(make_window) -> None:  # noqa: F811
    window, _ = make_window()
    tray = PanelTray(window)
    window.mode_text_changed.emit("Clock running (style 1)")
    assert tray._mode_action.text() == "Mode: Clock running (style 1)"
    assert not tray._mode_action.isEnabled()
```

If importing from `tests.test_window` fails because `tests` is not a package, move the `make_window` fixture and `_saved` helper into `tests/conftest.py` instead (they use `from p13ctl.gui import window as win`, which is safe to import there) and delete them from `test_window.py`. Do that move now rather than relying on the import.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_tray.py -q`
Expected: FAIL, no module `p13ctl.gui.tray`.

- [ ] **Step 3: Implement `p13ctl/gui/tray.py`**

```python
"""System tray icon: Show, current mode, Quit."""

from __future__ import annotations

from PySide6.QtCore import QObject
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMenu, QStyle, QSystemTrayIcon

from .window import MainWindow

class PanelTray(QObject):
    def __init__(self, window: MainWindow, parent: QObject | None = None) -> None:
        super().__init__(parent)
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

        self._tray.activated.connect(
            lambda reason: window.show_and_raise()
            if reason == QSystemTrayIcon.ActivationReason.Trigger
            else None
        )
        window.mode_text_changed.connect(self.set_mode_text)
        window.notify.connect(self.notify)
        self._tray.show()

    def set_mode_text(self, text: str) -> None:
        self._mode_action.setText(f"Mode: {text}")

    def notify(self, title: str, message: str) -> None:
        self._tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Critical, 8000)
```

- [ ] **Step 4: Rewrite `p13ctl/gui/main.py`**

```python
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
        window = MainWindow()
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error("Failed to start: %s", exc)
        if not args.minimized:
            QMessageBox.critical(None, "p13ctl GUI", f"Failed to start: {exc}")
        guard.close()
        return 1

    tray = PanelTray(window)
    guard.show_requested.connect(window.show_and_raise)

    # SIGTERM from the session manager runs the same quit path as the tray.
    # The no-op timer wakes the Qt loop so Python gets to run the handler.
    signal.signal(signal.SIGTERM, lambda *_: window.request_quit())
    signal.signal(signal.SIGINT, lambda *_: window.request_quit())
    pump = QTimer()
    pump.timeout.connect(lambda: None)
    pump.start(250)

    if not args.minimized:
        window.show()
    code = app.exec()

    # Tear down widgets before PySide atexit destroyQCoreApplication.
    window.quit_app()
    guard.close()
    del tray
    window.hide()
    window.deleteLater()
    app.processEvents()

    return code

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run tests and an offscreen smoke launch**

Run: `.venv/bin/python -m pytest -q` (expect all pass).
Run: `QT_QPA_PLATFORM=offscreen timeout -s TERM 4 .venv/bin/p13ctl-gui --minimized; echo "exit=$?"`
Expected: process exits by itself within 4 seconds via the SIGTERM path (exit 0, no traceback; "panel not found" warnings are fine without hardware). Exit 124 means the SIGTERM handler did not stop the app: fix before continuing.
Run a second time with two launches: start one in the background, then run it again; the second must return immediately with exit 0.

- [ ] **Step 6: Commit**

```bash
git add p13ctl/gui/tray.py tests/test_tray.py
git commit -m "GUI: add tray icon, minimized start, SIGTERM quit" -- \
  p13ctl/gui/tray.py p13ctl/gui/main.py tests/test_tray.py tests/conftest.py tests/test_window.py
```

---

### Task 6: Login behaviour (never persist "off", retry every 10 seconds)

**Files:**
- Modify: `p13ctl/gui/window.py`, `p13ctl/gui/main.py`
- Test: `tests/test_window.py`

**Interfaces:**
- Consumes: `make_window(saved_mode, **kwargs)`.
- Produces: `MainWindow(autostart: bool = False)`; `_apply_mode(mode, *, login=False)`; attributes `_login_mode: str | None`, `_login_timer: QTimer` (single shot, 10000 ms), `_login_notified: bool`; `_cancel_login_retry() -> None`.

Rule: `_apply_mode(login=True)` is used only by the autostart apply path and the retry timer. While `_login_mode` is set, any failure (sync `_start_*` returning False, or async `error` signals, or a failed `TaskWorker` action) does not persist "off", does not blank the panel, and re-arms the timer. A manual `_apply_mode(login=False)` clears the retry. Only the first failure of a streak produces a notification.

- [ ] **Step 1: Write the failing tests (append to `tests/test_window.py`)**

```python
def test_login_sync_failure_does_not_persist_off(make_window, monkeypatch) -> None:
    window, persisted = make_window("sysmon", autostart=True)
    monkeypatch.setattr(window, "_start_sysmon", lambda: False)

    window._maybe_apply_saved_mode()

    assert "off" not in persisted
    assert window._login_timer.isActive()
    assert window._login_timer.interval() == 10_000

def test_login_async_error_retries_without_persisting_off(make_window, monkeypatch) -> None:
    window, persisted = make_window("sysmon", autostart=True)
    starts: list[int] = []
    monkeypatch.setattr(window, "_start_sysmon", lambda: starts.append(1) or True)
    window._maybe_apply_saved_mode()

    window._on_sysmon_error("Artinchip display not found")

    assert "off" not in persisted
    assert window._login_timer.isActive()
    window._login_timer.timeout.emit()
    assert starts == [1, 1]

def test_login_failure_notifies_once(make_window, monkeypatch) -> None:
    window, _ = make_window("sysmon", autostart=True)
    monkeypatch.setattr(window, "_start_sysmon", lambda: False)
    got: list[tuple[str, str]] = []
    window.notify.connect(lambda t, m: got.append((t, m)))
    window._maybe_apply_saved_mode()

    window._show_error("Panel unavailable", "not found")
    window._show_error("Panel unavailable", "not found")
    window._show_error("Panel unavailable", "not found")

    assert len(got) == 1

def test_manual_selection_cancels_login_retry(make_window, monkeypatch) -> None:
    window, _ = make_window("sysmon", autostart=True)
    monkeypatch.setattr(window, "_start_sysmon", lambda: False)
    monkeypatch.setattr(window, "_start_test_pattern", lambda: True)
    window._maybe_apply_saved_mode()
    assert window._login_timer.isActive()

    window._apply_mode("test")

    assert not window._login_timer.isActive()
    assert window._login_mode is None

def test_normal_launch_failure_still_persists_off(make_window, monkeypatch) -> None:
    window, persisted = make_window("sysmon")
    monkeypatch.setattr(window, "_start_sysmon", lambda: False)

    window._maybe_apply_saved_mode()

    assert "off" in persisted
    assert not window._login_timer.isActive()

def test_login_action_error_retries(make_window, monkeypatch) -> None:
    window, persisted = make_window("test", autostart=True)
    monkeypatch.setattr(window, "_start_test_pattern", lambda: True)
    window._maybe_apply_saved_mode()

    window._on_action_err("USB busy")

    assert "off" not in persisted
    assert window._login_timer.isActive()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_window.py -q`
Expected: new tests FAIL (`autostart` keyword unknown).

- [ ] **Step 3: Implement in `window.py`**

`__init__` signature and state (set before `_build_ui`; the timer is created next to the other timers):

```python
    def __init__(self, *, autostart: bool = False) -> None:
        super().__init__()
        ...
        self._autostart = autostart
        self._login_mode: str | None = None
        self._login_notified = False
```

```python
        # Login: retry a failed mode start until the panel is available.
        self._login_timer = QTimer(self)
        self._login_timer.setSingleShot(True)
        self._login_timer.setInterval(10_000)
        self._login_timer.timeout.connect(self._retry_login_mode)
```

`_maybe_apply_saved_mode` last line becomes `self._apply_mode(saved, login=self._autostart)`.

Replace the head and failure branch of `_apply_mode`:

```python
    def _apply_mode(self, mode: str, *, login: bool = False) -> None:
        if login:
            self._login_mode = mode
        else:
            self._cancel_login_retry()
        previous = self._active_mode
        self._stop_current_mode(save_layout=previous == MODE_EXTENDED)
        ...  # MODE_OFF and start dispatch unchanged
        if not ok:
            if self._login_mode is not None:
                self._login_failed()
                return
            self._blank_display()
            ... # unchanged
```

New helpers:

```python
    def _cancel_login_retry(self) -> None:
        self._login_timer.stop()
        self._login_mode = None
        self._login_notified = False

    def _login_failed(self) -> None:
        """Keep the saved mode and try again; the panel may not have enumerated yet."""
        self._active_mode = MODE_OFF
        self._refresh_mode_status()
        self._login_timer.start()

    def _retry_login_mode(self) -> None:
        if self._closing or self._login_mode is None:
            return
        self._apply_mode(self._login_mode, login=True)
```

Failure handlers: at the top of `_on_mirror_error`, `_on_sysmon_error` (after the stale-signal check) and `_on_action_err`, add the login branch:

```python
        if self._login_mode is not None:
            self._show_error("Extended monitor failed", msg)  # title differs per handler
            self._login_failed()
            return
```

Use the existing title of each handler ("Extended monitor failed", "System monitor failed", "Action failed").

`_show_error`: de-duplicate during a login streak, before the visible/hidden split:

```python
        if self._login_mode is not None:
            if self._login_notified:
                return
            self._login_notified = True
```

`_refresh_mode_status`: add the first branch so the user sees why nothing is happening:

```python
        if self._login_mode is not None and mode == MODE_OFF:
            text = "Waiting for panel, retrying every 10 seconds"
        elif mode == MODE_EXTENDED and self._mirror_running():
```

(turn the former leading `if` into `elif`).

`shutdown()`: call `self._login_timer.stop()` next to the `killTimer` block.

In `main.py` change construction to `window = MainWindow(autostart=args.minimized)`.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass. Also re-run the Task 5 smoke launch.

- [ ] **Step 5: Commit**

```bash
git commit -m "GUI: retry saved mode at login without persisting off" -- \
  p13ctl/gui/window.py p13ctl/gui/main.py tests/test_window.py
```

---

### Task 7: Installer, uninstaller, autostart entry

**Files:**
- Create: `tools/linux/p13ctl-gui-autostart.desktop.in`
- Modify: `install.sh`, `uninstall.sh`

**Interfaces:**
- Produces: install flag `--no-autostart`, uninstall flag `--keep-autostart`, function `remove_legacy_services` in both scripts.

- [ ] **Step 1: Create the autostart template**

```
[Desktop Entry]
Type=Application
Name=MSI P13 Control
Comment=Drive the MSI P13 panel from the system tray at login
Exec=@P13CTL_GUI_BIN@ --minimized
Icon=computer
Terminal=false
X-GNOME-Autostart-enabled=true
```

- [ ] **Step 2: Edit `install.sh`**

1. Rename variable `DO_BOOT_DISPLAY` to `DO_AUTOSTART` everywhere. In the arg parser delete the `--no-boot-display` case, add `--no-autostart) DO_AUTOSTART=0; shift ;;`, and change `--no-desktop` to `WITH_DESKTOP=0; shift` (autostart no longer depends on capture dependencies). In `usage()` replace the `--no-boot-display` line with `  --no-autostart    Do not start the GUI in the tray at login` and change "and user services" in the description to "and the GUI login autostart".
2. Replace `install_boot_display` with:

```bash
remove_legacy_services() {
  local unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
  local name found=0
  for name in p13-display.service p13-panel-off.service; do
    if [[ -f "$unit_dir/$name" ]]; then
      found=1
      echo "==> Removing legacy $name"
      systemctl --user disable --now "$name" 2>/dev/null || true
      rm -f "$unit_dir/$name"
    fi
  done
  if [[ "$found" == "1" ]]; then
    systemctl --user daemon-reload 2>/dev/null || true
  fi
}

install_autostart() {
  echo "==> Enabling P13 GUI at login"
  local autostart_dir="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
  local gui_bin="$VENV/bin/p13ctl-gui"
  if [[ ! -x "$gui_bin" ]]; then
    echo "    warning: $gui_bin not found, skipping login autostart"
    return 0
  fi
  mkdir -p "$autostart_dir"
  sed -e "s|@P13CTL_GUI_BIN@|$gui_bin|g" \
    "$LINUX/p13ctl-gui-autostart.desktop.in" > "$autostart_dir/p13ctl-gui.desktop"
  echo "    Added $autostart_dir/p13ctl-gui.desktop"
}
```

3. Replace the block `if [[ "$DO_PYTHON" == "1" && "$DO_BOOT_DISPLAY" == "1" && "$WITH_DESKTOP" == "1" ]]; then install_boot_display; fi` with:

```bash
if [[ "$DO_PYTHON" == "1" ]]; then
  remove_legacy_services
fi

if [[ "$DO_PYTHON" == "1" && "$WITH_GUI" == "1" && "$DO_AUTOSTART" == "1" ]]; then
  install_autostart
fi
```

4. Final message: replace the "Login: p13-display.service ..." and "Logout/restart: p13-panel-off.service ..." lines with:

```
Login: p13ctl-gui starts in the system tray and applies the saved Display Mode.
Closing the window hides it; Quit from the tray menu stops the panel.
```

- [ ] **Step 3: Edit `uninstall.sh`**

Rename `DO_BOOT_DISPLAY` to `DO_AUTOSTART`; replace the `--keep-boot-display` flag and its usage text with `--keep-autostart  Leave the GUI login autostart entry in place`. Replace `remove_boot_display` with:

```bash
remove_legacy_services() {
  local unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
  local name found=0
  for name in p13-display.service p13-panel-off.service; do
    if [[ -f "$unit_dir/$name" ]]; then
      found=1
      echo "==> Removing legacy $name"
      systemctl --user disable --now "$name" 2>/dev/null || true
      rm -f "$unit_dir/$name"
    fi
  done
  if [[ "$found" == "1" ]]; then
    systemctl --user daemon-reload 2>/dev/null || true
  fi
}

remove_autostart() {
  local entry="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/p13ctl-gui.desktop"
  if [[ -f "$entry" ]]; then
    echo "==> Removing GUI login autostart"
    rm -f "$entry"
    echo "    removed $entry"
  fi
}
```

Call `remove_legacy_services` unconditionally in the uninstall section and `remove_autostart` under `if [[ "$DO_AUTOSTART" == "1" ]]`. In the summary, replace the p13-display line with `$( [[ "$DO_AUTOSTART" == "1" ]] && echo "  - p13ctl-gui login autostart entry" )`.

- [ ] **Step 4: Verify**

Run: `bash -n install.sh && bash -n uninstall.sh` (expect no output).
Run: `command -v shellcheck && shellcheck install.sh uninstall.sh` (only if installed; fix new warnings you introduced).
Run: `grep -nE "BOOT_DISPLAY|boot-display|p13-display|p13-panel-off|install_boot_display" install.sh uninstall.sh`
Expected: only the two `p13-display.service p13-panel-off.service` names inside `remove_legacy_services` in each script.
Dry check of the sed template: `sed -e "s|@P13CTL_GUI_BIN@|/x/p13ctl-gui|g" tools/linux/p13ctl-gui-autostart.desktop.in` shows `Exec=/x/p13ctl-gui --minimized`.

- [ ] **Step 5: Commit**

```bash
git add tools/linux/p13ctl-gui-autostart.desktop.in
git commit -m "Installer: GUI login autostart, remove legacy user services" -- \
  tools/linux/p13ctl-gui-autostart.desktop.in install.sh uninstall.sh
```

---

### Task 8: Docs and final verification

**Files:**
- Modify: `README.md`, `docs/architecture.md`

- [ ] **Step 1: Edit `README.md`**

- Feature table row `Boot auto-start`: change to `| Login auto-start | **Implemented** | \`p13ctl-gui --minimized\` starts in the tray and applies the saved Display Mode |`.
- Replace the paragraph starting "`install.sh` installs EVDI ..." (line 44) with: "`install.sh` installs EVDI, system dependencies, udev access rules, and `p13ctl` into a local virtualenv, and adds an XDG autostart entry that starts `p13ctl-gui --minimized` at login. The GUI owns the panel: closing the window hides it to the system tray and the panel keeps running, and **Quit** in the tray menu blanks the panel and exits. Older installs that used `p13-display.service` and `p13-panel-off.service` are cleaned up automatically."
- Code block: replace `./install.sh --no-boot-display   # skip login autostart` with `./install.sh --no-autostart       # skip login autostart` and delete the `systemctl --user status p13-display.service` line.
- Add a short "Behaviour notes" list under it: the panel is only driven while the app runs (headless and `--no-gui` installs have no boot autostart); sysmon, clock, image and test modes no longer blank when the desktop displays sleep (extended mode still follows DPMS through EVDI); there is no logind shutdown delay lock, so blanking at shutdown depends on the app handling SIGTERM in time, otherwise the panel keeps its last frame; one-shot CLI commands report "USB busy" while the app is running.
- Project layout line 87: change "Protocol, display-mode, rendering, and service tests" to "Protocol, display-mode, rendering, and GUI tests".

- [ ] **Step 2: Edit `docs/architecture.md`**

Add before "## Out of scope":

```markdown
## Application lifecycle

`p13ctl-gui` owns the USB display interface; no systemd user services run.

- Login: an XDG autostart entry runs `p13ctl-gui --minimized`. The app starts in the tray and applies the saved Display Mode, retrying every 10 seconds until the panel enumerates. A failed login start never overwrites the saved mode with "off".
- Single instance: a `QLocalServer` guard. A second launch asks the first to show its window and exits.
- Close hides the window to the tray. Quit (tray menu) or SIGTERM stops the workers, waits for the USB interface, sends a black frame, and exits.
- Errors while the window is hidden appear as tray notifications.
```

- [ ] **Step 3: Final verification**

Run: `.venv/bin/python -m pytest -q` (all pass).
Run: `grep -rnE "p13-display|p13-panel-off|sleepwatch|boot-display|display mode\b" README.md docs/architecture.md p13ctl tests tools install.sh uninstall.sh` and confirm the only hits are the legacy cleanup lists in the two scripts.
Run the offscreen smoke launch from Task 5 Step 5 once more.
Confirm `git status --short` shows the user's untouched staged files (`artinchip.py`, `faces.py`, `tests/test_faces.py`, spec doc) still staged and uncommitted.
Not verifiable without hardware (state this in the final report): tray behaviour on KDE Wayland, the real black frame on logout and shutdown, hot-plug at login, the actual autostart firing.

- [ ] **Step 4: Commit**

```bash
git commit -m "docs: describe in-app panel control and login autostart" -- README.md docs/architecture.md
```
