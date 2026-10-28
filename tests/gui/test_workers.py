"""FaceWorker: stop requests and the bounded retry supervisor."""

from __future__ import annotations

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
