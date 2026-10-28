"""A stop request must survive every startup phase of a panel session."""

from __future__ import annotations

import threading

import pytest

from p13ctl.display import evdi_bridge, virtual_monitor
from p13ctl.display.artinchip import ArtinchipDisplay, DisplayError
from p13ctl.gui.workers import FaceWorker


def _collect(worker):
    errors: list[str] = []
    stops: list[int] = []
    worker.error.connect(errors.append)
    worker.stopped.connect(lambda: stops.append(1))
    return errors, stops


def test_face_worker_stop_during_connect_reaches_the_face_loop(
    monkeypatch, qapp, fake_panel
) -> None:
    worker = FaceWorker(rotate=0, face="clock")

    def get_params(self):
        worker.request_stop()
        return 480, 480, 0, 60

    seen: list[bool] = []
    monkeypatch.setattr(ArtinchipDisplay, "_get_params", get_params)
    monkeypatch.setattr(
        ArtinchipDisplay, "run_clock", lambda self, **kw: seen.append(self._stop_requested)
    )

    worker.run()

    assert False not in seen


def test_face_worker_reports_unexpected_errors(monkeypatch, qapp, fake_panel) -> None:
    worker = FaceWorker(rotate=0, face="clock")
    errors, stops = _collect(worker)

    def boom(self, **kwargs):
        raise ValueError("bad frame")

    monkeypatch.setattr(ArtinchipDisplay, "run_clock", boom)

    worker.run()

    assert len(errors) == 1 and "bad frame" in errors[0]
    assert stops == [1]


def test_bridge_run_honors_a_stop_requested_before_start(monkeypatch) -> None:
    bridge = evdi_bridge.EvdiBridge()
    bridge.handle = object()
    bridge.display = object()
    monkeypatch.setattr(evdi_bridge.evdi, "get_event_fd", lambda _handle: 0)

    def entered(*args):
        raise AssertionError("stream loop entered after stop")

    monkeypatch.setattr(bridge, "_poll_events", entered)

    bridge.request_stop()
    bridge.run()


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


def _fast_worker(**kwargs) -> FaceWorker:
    worker = FaceWorker(rotate=0, face="clock", **kwargs)
    worker.retry_delays = (0.0, 0.0)
    return worker


def test_face_worker_retries_transient_panel_errors(monkeypatch, qapp, fake_panel) -> None:
    worker = _fast_worker()
    errors, stops = _collect(worker)
    calls: list[int] = []

    def flaky(self, **kwargs):
        calls.append(1)
        if len(calls) < 3:
            raise DisplayError("panel dropped off the bus")

    monkeypatch.setattr(ArtinchipDisplay, "run_clock", flaky)

    worker.run()

    assert len(calls) == 3
    assert errors == []
    assert stops == [1]


def test_face_worker_gives_up_after_the_retry_budget(monkeypatch, qapp, fake_panel) -> None:
    worker = _fast_worker()
    errors, _stops = _collect(worker)
    calls: list[int] = []

    def always_down(self, **kwargs):
        calls.append(1)
        raise DisplayError("panel gone")

    monkeypatch.setattr(ArtinchipDisplay, "run_clock", always_down)

    worker.run()

    assert len(calls) == 3  # first try plus one per retry delay
    assert len(errors) == 1 and "panel gone" in errors[0]


def test_face_worker_does_not_retry_programming_errors(monkeypatch, qapp, fake_panel) -> None:
    worker = _fast_worker()
    errors, _stops = _collect(worker)
    calls: list[int] = []

    def buggy(self, **kwargs):
        calls.append(1)
        raise ValueError("bad frame")

    monkeypatch.setattr(ArtinchipDisplay, "run_clock", buggy)

    worker.run()

    assert len(calls) == 1
    assert len(errors) == 1


def test_face_worker_stop_during_backoff_ends_quietly(monkeypatch, qapp, fake_panel) -> None:
    worker = _fast_worker()
    errors, stops = _collect(worker)
    calls: list[int] = []

    def drop_then_stop(self, **kwargs):
        calls.append(1)
        worker.request_stop()
        raise DisplayError("panel dropped")

    monkeypatch.setattr(ArtinchipDisplay, "run_clock", drop_then_stop)

    worker.run()

    assert len(calls) == 1
    assert errors == []
    assert stops == [1]
