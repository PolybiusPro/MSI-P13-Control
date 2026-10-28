"""Background workers for blocking p13ctl operations."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import usb.core
from PySide6.QtCore import QObject, QThread, Signal

from p13ctl.display.artinchip import DisplayError

_LOGGER = logging.getLogger(__name__)

# Pauses before each retry of a failed face session; the list length is the budget.
_RETRY_DELAYS_S = (1.0, 2.0, 4.0, 8.0)
# A session that ran this long before failing gets a fresh retry budget.
_HEALTHY_SESSION_S = 30.0

class TaskWorker(QThread):
    """Run a callable off the UI thread."""

    finished_ok = Signal(object)
    finished_err = Signal(str)

    def __init__(
        self,
        fn: Callable[..., Any],
        *args: Any,
        parent: QObject | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(parent)
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:  # noqa: BLE001
            self.finished_err.emit(str(exc))
        else:
            self.finished_ok.emit(result)

class _PanelWorker(QThread):
    """A thread that streams to the panel until its stop event is set.

    The event exists from construction and is handed to everything the thread
    connects, so a stop requested at any point (even mid-connect) is kept.
    """

    error = Signal(str)
    stopped = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._stop = threading.Event()

    def request_stop(self) -> None:
        self._stop.set()

    def _report(self, exc: Exception) -> None:
        """Surface a failure unless it is just the fallout of stopping."""
        _LOGGER.warning("%s failed: %s", type(self).__name__, exc, exc_info=exc)
        if not self._stop.is_set():
            self.error.emit(str(exc))

class MirrorWorker(_PanelWorker):
    """Run the EVDI extended-display loop."""

    def __init__(
        self,
        *,
        fps: int,
        quality: int,
        rotate: int,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.fps = fps
        self.quality = quality
        self.rotate = rotate

    def request_stop(self) -> None:
        super().request_stop()
        from p13ctl.display.session import stop_active_sessions

        stop_active_sessions()

    def run(self) -> None:
        from p13ctl.display.session import stop_active_sessions
        from p13ctl.display.virtual_monitor import run_virtual_monitor

        if self._stop.is_set():
            self.stopped.emit()
            return
        try:
            run_virtual_monitor(
                fps=self.fps,
                quality=self.quality,
                rotate=self.rotate,
                configure=True,
                save_layout=True,
                reset_layout=False,
                stop=self._stop,
            )
        except Exception as exc:  # noqa: BLE001
            self._report(exc)
        finally:
            stop_active_sessions()
            self.stopped.emit()

class FaceWorker(_PanelWorker):
    """Run a panel face loop (hardware monitor or clock)."""

    def __init__(
        self,
        *,
        rotate: int,
        face: str = "sysmon",
        style: int = 1,
        switch: float = 10.0,
        items: list[str] | None = None,
        background: str | None = None,
        colors: dict | None = None,
        interval: float = 1.0,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.rotate = rotate
        self.face = face
        self.style = style
        self.switch = switch
        self.items = items
        self.background = background
        self.colors = colors
        self.interval = interval
        self.retry_delays = _RETRY_DELAYS_S

    def run(self) -> None:
        try:
            self._run_with_retries()
        finally:
            self.stopped.emit()

    def _run_with_retries(self) -> None:
        """Rerun the face after a transient panel failure, with growing pauses.

        Only panel/USB errors are retried; anything else is a bug and is
        reported at once. A session that streamed for a while earns a fresh
        retry budget.
        """
        delays = iter(self.retry_delays)
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self._run_face()
                return
            except (DisplayError, usb.core.USBError) as exc:
                if time.monotonic() - started >= _HEALTHY_SESSION_S:
                    delays = iter(self.retry_delays)
                delay = next(delays, None)
                if delay is None or self._stop.is_set():
                    self._report(exc)
                    return
                _LOGGER.warning("panel session failed (%s); retrying in %.0fs", exc, delay)
                if self._stop.wait(delay):
                    return
            except Exception as exc:  # noqa: BLE001
                self._report(exc)
                return

    def _run_face(self) -> None:
        from p13ctl.display.artinchip import ArtinchipDisplay

        with ArtinchipDisplay(rotate=self.rotate, stop=self._stop) as disp:
            if self.face == "clock":
                disp.run_clock(
                    style=self.style,
                    background=self.background,
                    colors=self.colors,
                )
            else:
                disp.run_sysmon(
                    style=self.style,
                    interval=self.interval,
                    switch=self.switch,
                    items=self.items,
                    background=self.background,
                    colors=self.colors,
                )
