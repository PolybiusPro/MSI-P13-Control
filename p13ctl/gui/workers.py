"""Background workers for blocking p13ctl operations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

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

class MirrorWorker(QThread):
    """Run the userspace desktop-mirror loop when no service is active."""

    error = Signal(str)
    stopped = Signal()

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
        self._stop = False
        self._display = None

    def request_stop(self) -> None:
        self._stop = True
        if self._display is not None:
            self._display.request_stop()

    def run(self) -> None:
        from p13ctl.display.artinchip import ArtinchipDisplay, DisplayError

        if self._stop:
            self.stopped.emit()
            return
        try:
            with ArtinchipDisplay(rotate=self.rotate) as disp:
                self._display = disp
                disp.run_desktop(
                    interval=1.0 / max(1, self.fps),
                    quality=self.quality,
                )
        except DisplayError as exc:
            if not self._stop:
                self.error.emit(str(exc))
        finally:
            self._display = None
            self.stopped.emit()

class FaceWorker(QThread):
    """Run a panel face loop (hardware monitor or clock)."""

    error = Signal(str)
    stopped = Signal()

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
        self._stop = False
        self._display = None

    def request_stop(self) -> None:
        self._stop = True
        display = self._display
        if display is not None:
            display.request_stop()

    def run(self) -> None:
        from p13ctl.display.artinchip import ArtinchipDisplay, DisplayError

        if self._stop:
            self.stopped.emit()
            return
        try:
            with ArtinchipDisplay(rotate=self.rotate) as disp:
                self._display = disp
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
        except DisplayError as exc:
            if not self._stop:
                self.error.emit(str(exc))
        finally:
            self._display = None
            self.stopped.emit()
