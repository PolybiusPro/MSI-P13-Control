from __future__ import annotations

import pytest

from p13ctl.display.artinchip import DisplayError
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


class _NoPanelDisplay:
    def __init__(self, *args, **kwargs) -> None:
        raise DisplayError("no panel in tests")


def _no_panel(*args, **kwargs):
    raise DisplayError("no panel in tests")


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


def test_window_has_no_service_helpers(make_window) -> None:
    window, _persisted = make_window()
    assert not hasattr(win, "mirror_service_installed")
    assert not hasattr(window, "_service_running")
    assert window._mirror_running() is False
