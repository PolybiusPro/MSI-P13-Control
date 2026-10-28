"""EvdiBridge stop handling."""

from __future__ import annotations

from p13ctl.display import evdi_bridge


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
