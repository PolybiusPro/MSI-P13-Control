from __future__ import annotations

import pytest
import usb.core
from PySide6.QtWidgets import QMessageBox, QSystemTrayIcon

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


def test_quit_app_survives_usb_error(make_window, monkeypatch) -> None:
    window, _ = make_window()
    monkeypatch.setattr(win, "wait_for_display_usb", lambda: None)

    def _write_failed() -> None:
        raise usb.core.USBError("write failed")

    monkeypatch.setattr(win, "send_black_image", _write_failed)

    window.quit_app()


def test_request_quit_still_quits_on_unexpected_error(make_window, monkeypatch) -> None:
    window, _ = make_window()
    monkeypatch.setattr(win, "wait_for_display_usb", lambda: None)

    def _broken() -> None:
        raise OSError("boom")

    monkeypatch.setattr(win, "send_black_image", _broken)
    quits: list[int] = []
    monkeypatch.setattr(win.QApplication, "quit", staticmethod(lambda: quits.append(1)))

    window.request_quit()

    assert quits == [1]


def test_request_quit_quits_even_if_quit_app_raises(make_window, monkeypatch) -> None:
    window, _ = make_window()

    def _raises() -> None:
        raise RuntimeError("shutdown failed")

    monkeypatch.setattr(window, "quit_app", _raises)
    quits: list[int] = []
    monkeypatch.setattr(win.QApplication, "quit", staticmethod(lambda: quits.append(1)))

    with pytest.raises(RuntimeError):
        window.request_quit()

    assert quits == [1]
