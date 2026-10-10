from __future__ import annotations

import pytest
import usb.core
from PySide6.QtWidgets import QMessageBox, QSystemTrayIcon

from p13ctl.display.artinchip import DisplayError
from p13ctl.gui import window as win


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
