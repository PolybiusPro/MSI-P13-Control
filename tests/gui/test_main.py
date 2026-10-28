from __future__ import annotations

import signal

import pytest

from p13ctl.gui import main as gui_main


class _Recorder:
    def __init__(self) -> None:
        self.calls = 0

    def request_quit(self) -> None:
        self.calls += 1


@pytest.fixture
def restore_signals():
    saved = {
        signal.SIGTERM: signal.getsignal(signal.SIGTERM),
        signal.SIGINT: signal.getsignal(signal.SIGINT),
    }
    yield
    for signum, handler in saved.items():
        signal.signal(signum, handler)


def test_signal_handlers_defer_quit_to_event_loop(qapp, restore_signals) -> None:
    window = _Recorder()

    gui_main._install_signal_handlers(window)

    for signum in (signal.SIGTERM, signal.SIGINT):
        handler = signal.getsignal(signum)
        assert callable(handler)
        before = window.calls
        handler(signum, None)
        assert window.calls == before
        qapp.processEvents()
        assert window.calls == before + 1
