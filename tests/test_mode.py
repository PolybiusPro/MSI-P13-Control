from __future__ import annotations

from pathlib import Path

from p13ctl.display import mode

def test_saved_system_monitor_style_is_validated(monkeypatch) -> None:
    monkeypatch.setattr(
        mode,
        "load_display_config",
        lambda: {"mode": {"name": mode.MODE_SYSMON, "monitor_style": 5}},
    )
    assert mode.get_saved_mode()["monitor_style"] == 5

    monkeypatch.setattr(
        mode,
        "load_display_config",
        lambda: {"mode": {"name": mode.MODE_SYSMON, "monitor_style": 9}},
    )
    assert mode.get_saved_mode()["monitor_style"] == 1

def test_system_monitor_style_is_persisted(monkeypatch) -> None:
    saved: dict = {}
    monkeypatch.setattr(mode, "load_display_config", lambda: {})

    def capture(**kwargs):
        saved.update(kwargs)
        return Path("/tmp/display.json")

    monkeypatch.setattr(mode, "save_display_config", capture)

    mode.update_saved_mode(name=mode.MODE_SYSMON, monitor_style=3)

    assert saved["mode"]["monitor_style"] == 3

def test_dashboard_ignores_saved_metric_selection(monkeypatch) -> None:
    monkeypatch.setattr(
        mode,
        "load_display_config",
        lambda: {
            "mode": {
                "name": mode.MODE_SYSMON,
                "monitor_style": 5,
                "items": ["cpu_temp"],
            }
        },
    )

    assert mode.get_saved_mode()["items"] is None
