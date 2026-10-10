from __future__ import annotations

import os
import shutil
import socket
import tempfile
import time
import uuid

from p13ctl.gui.single_instance import SingleInstance


def _pump(qapp, cond, timeout: float = 2.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end and not cond():
        qapp.processEvents()
        time.sleep(0.01)
    return bool(cond())


def _key() -> str:
    return f"p13ctl-test-{uuid.uuid4().hex}"


def test_second_instance_pings_first(qapp) -> None:
    key = _key()
    first = SingleInstance(key)
    pings: list[int] = []
    first.show_requested.connect(lambda: pings.append(1))
    assert first.acquire() is True

    second = SingleInstance(key)
    assert second.acquire() is False
    assert _pump(qapp, lambda: pings)
    first.close()


def test_minimized_second_launch_does_not_ping(qapp) -> None:
    key = _key()
    first = SingleInstance(key)
    pings: list[int] = []
    first.show_requested.connect(lambda: pings.append(1))
    assert first.acquire() is True

    second = SingleInstance(key)
    assert second.acquire(notify=False) is False
    assert not _pump(qapp, lambda: pings, timeout=0.3)
    first.close()


def test_stale_socket_is_replaced(qapp) -> None:
    key = _key()
    dead = SingleInstance(key)
    assert dead.acquire() is True
    path = dead._server.fullServerName()
    dead.close()
    # Leave a socket file behind with nobody listening, like a crashed instance.
    stale = socket.socket(socket.AF_UNIX)
    stale.bind(path)
    stale.close()

    fresh = SingleInstance(key)
    assert fresh.acquire() is True
    fresh.close()


def test_socket_lives_in_xdg_runtime_dir(qapp, monkeypatch) -> None:
    runtime = tempfile.mkdtemp(dir="/tmp")
    try:
        monkeypatch.setenv("XDG_RUNTIME_DIR", runtime)
        guard = SingleInstance(_key())
        assert guard.acquire() is True
        assert guard._server.fullServerName().startswith(runtime + os.sep)
        guard.close()
    finally:
        shutil.rmtree(runtime, ignore_errors=True)


def test_fallback_without_xdg_runtime_dir(qapp, monkeypatch) -> None:
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    key = _key()
    first = SingleInstance(key)
    assert first.acquire() is True
    second = SingleInstance(key)
    assert second.acquire(notify=False) is False
    first.close()
