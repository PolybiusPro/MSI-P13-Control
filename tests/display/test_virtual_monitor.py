"""Virtual monitor startup and shutdown."""

from __future__ import annotations

import threading

import pytest

from p13ctl.display import evdi_bridge, virtual_monitor


@pytest.mark.parametrize("stop_during", ["setup", "configure"])
def test_virtual_monitor_skips_the_stream_when_stopped_during_startup(
    monkeypatch, stop_during
) -> None:
    stop = threading.Event()
    streamed: list[int] = []

    def setup(self) -> None:
        if stop_during == "setup":
            stop.set()

    def configure(**kwargs):
        if stop_during == "configure":
            stop.set()
        return "DVI-I-1", False

    monkeypatch.setattr(virtual_monitor, "ensure_evdi_loaded", lambda: None)
    monkeypatch.setattr(virtual_monitor, "load_display_config", lambda: None)
    monkeypatch.setattr(virtual_monitor, "configure_virtual_output", configure)
    monkeypatch.setattr(evdi_bridge.EvdiBridge, "setup_evdi", setup)
    monkeypatch.setattr(evdi_bridge.EvdiBridge, "run", lambda self: streamed.append(1))
    monkeypatch.setattr(evdi_bridge.EvdiBridge, "shutdown", lambda self: None)

    virtual_monitor.run_virtual_monitor(configure=True, save_layout=False, stop=stop)

    assert streamed == []
