from __future__ import annotations

import copy
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from p13ctl.display import config
from p13ctl.display.artinchip import DisplayError
from p13ctl.gui import window as win

_FULL_CONFIG = {
    "version": 3,
    "virtual": {
        "name": "DVI-I-1",
        "enabled": True,
        "mode": "480x480@60",
        "position": "1920,300",
        "rotation": None,
        "scale": 1.5,
        "anchor": "eDP-1",
        "offset": {"x": 1920, "y": 300},
    },
    "panel": {"brightness": 80, "rotation": 90},
    "stream": {"fps": 30, "quality": 75, "rotate": 0},
    "mode": {
        "name": "sysmon",
        "items": ["cpu_temp", "gpu_temp"],
        "colors": {"clock": "#ffffff", "label": "#00ff00"},
        "style": 3,
        "background": "/home/user/Pictures/my wallpaper.png",
    },
}


@pytest.fixture
def full_config() -> dict:
    """A config with every known section filled in."""
    return copy.deepcopy(_FULL_CONFIG)


@pytest.fixture
def paths(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """Point the config file layer at a temp dir: (display.ini, legacy json)."""
    ini = tmp_path / "p13ctl" / "display.ini"
    legacy = tmp_path / "p13ctl" / "display-layout.json"
    monkeypatch.setattr(config, "LAYOUT_PATH", ini)
    monkeypatch.setattr(config, "LEGACY_JSON_PATH", legacy)
    return ini, legacy


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
