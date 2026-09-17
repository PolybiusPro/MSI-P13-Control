from __future__ import annotations

from pathlib import Path

from PIL import Image

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

def test_send_black_image_uses_saved_rotation(monkeypatch) -> None:
    sent: list[Image.Image] = []
    stopped: list[bool] = []
    monkeypatch.setattr(mode, "stop_display_service", lambda: stopped.append(True))
    monkeypatch.setattr(
        mode,
        "_stream",
        lambda: {"fps": 60, "quality": 75, "rotate": 270},
    )

    class FakeDisplay:
        def __init__(self, *, rotate: int) -> None:
            assert rotate == 270

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def send_image(self, image: Image.Image) -> None:
            sent.append(image)

    monkeypatch.setattr(mode, "ArtinchipDisplay", FakeDisplay)

    mode.send_black_image()

    assert stopped == [True]
    assert len(sent) == 1
    assert sent[0].size == mode.PANEL_SIZE
    assert sent[0].getextrema() == ((0, 0), (0, 0), (0, 0))

def test_saved_system_monitor_caps_fps_and_uses_stream_quality(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        mode,
        "get_saved_mode",
        lambda: {
            "name": mode.MODE_SYSMON,
            "monitor_style": 5,
            "switch": 5,
            "items": None,
            "background": None,
            "colors": None,
        },
    )
    monkeypatch.setattr(
        mode,
        "_stream",
        lambda: {"fps": 60, "quality": 72, "rotate": 0},
    )
    monkeypatch.setattr(mode, "restore_saved_brightness", lambda: None)
    monkeypatch.setattr(mode.threading, "current_thread", lambda: object())

    class FakeDisplay:
        def __init__(self, *, rotate: int) -> None:
            assert rotate == 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def run_sysmon(self, **kwargs) -> None:
            calls.append(kwargs)

    monkeypatch.setattr(mode, "ArtinchipDisplay", FakeDisplay)

    assert mode.run_saved_display_mode() == 0
    assert calls == [
        {
            "style": 5,
            "switch": 5,
            "fps": mode.SYSMON_MAX_FPS,
            "quality": 72,
            "items": None,
            "background": None,
            "colors": None,
        }
    ]
