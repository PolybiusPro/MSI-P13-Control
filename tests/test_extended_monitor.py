from __future__ import annotations

from types import SimpleNamespace

from p13ctl import __main__ as cli
from p13ctl.display import virtual_monitor

def _desktop_args(**overrides):
    values = {
        "capture": False,
        "interval": None,
        "monitor": 0,
        "crop": "center",
        "no_configure": False,
        "no_save_layout": False,
        "reset_layout": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)

def test_desktop_command_uses_evdi_by_default(monkeypatch) -> None:
    called = {}
    monkeypatch.setattr(
        cli,
        "_stream_settings",
        lambda _args: {"fps": 60, "quality": 75, "rotate": 90},
    )
    monkeypatch.setattr(
        virtual_monitor,
        "run_virtual_monitor",
        lambda **kwargs: called.update(kwargs),
    )

    assert cli.cmd_display_desktop(_desktop_args()) == 0
    assert called == {
        "fps": 60,
        "quality": 75,
        "rotate": 90,
        "configure": True,
        "save_layout": True,
        "reset_layout": False,
    }

def test_desktop_capture_remains_an_explicit_fallback(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        cli,
        "_stream_settings",
        lambda _args: {"fps": 20, "quality": 70, "rotate": 0},
    )

    class FakeDisplay:
        def __init__(self, *, rotate):
            assert rotate == 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def run_desktop(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(cli, "ArtinchipDisplay", FakeDisplay)

    assert cli.cmd_display_desktop(_desktop_args(capture=True)) == 0
    assert calls == [
        {"interval": 0.05, "monitor": 0, "crop": "center", "quality": 70}
    ]
