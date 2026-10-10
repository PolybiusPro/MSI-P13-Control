from __future__ import annotations

from p13ctl.gui.tray import PanelTray


def test_tray_shows_mode_text(make_window) -> None:
    window, _ = make_window()
    tray = PanelTray(window)
    window.mode_text_changed.emit("Clock running (style 1)")
    assert tray._mode_action.text() == "Mode: Clock running (style 1)"
    assert not tray._mode_action.isEnabled()
