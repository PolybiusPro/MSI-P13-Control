from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from p13ctl.display.artinchip import DisplayError
from p13ctl.gui import window as win


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


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


class _NoPanelDisplay:
    def __init__(self, *args, **kwargs) -> None:
        raise DisplayError("no panel in tests")


def _no_panel(*args, **kwargs):
    raise DisplayError("no panel in tests")


class _FakeUsbDevice:
    def is_kernel_driver_active(self, _interface) -> bool:
        return False

    def detach_kernel_driver(self, _interface) -> None:
        pass


@pytest.fixture
def fake_panel(monkeypatch):
    """Let ArtinchipDisplay.connect() succeed without touching USB or HID."""
    import usb.util

    import p13ctl.hid.msi_p13 as hid
    from p13ctl.display.artinchip import ArtinchipDisplay

    monkeypatch.setattr(
        ArtinchipDisplay, "find", staticmethod(lambda: _FakeUsbDevice())
    )
    monkeypatch.setattr(usb.util, "claim_interface", lambda _dev, _iface: None)
    monkeypatch.setattr(usb.util, "release_interface", lambda _dev, _iface: None)
    monkeypatch.setattr(
        ArtinchipDisplay, "_get_params", lambda self: (480, 480, 0, 60)
    )
    monkeypatch.setattr(ArtinchipDisplay, "_authenticate", lambda self: True)
    monkeypatch.setattr(hid, "enable_host_display", lambda: None)


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
    # A real panel may be attached: no test may reach hardware.
    monkeypatch.setattr(win, "wait_for_display_usb", _no_panel)
    monkeypatch.setattr(win, "ArtinchipDisplay", _NoPanelDisplay)

    def _make(saved_mode: str = "sysmon", **kwargs):
        monkeypatch.setattr(win, "get_saved_mode", lambda: _saved(saved_mode))
        window = win.MainWindow(**kwargs)
        made.append(window)
        return window, persisted

    yield _make

    for window in made:
        window.shutdown()
        window.deleteLater()
