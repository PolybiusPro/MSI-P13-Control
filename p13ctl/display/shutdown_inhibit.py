"""Hold restart/shutdown until the panel is blanked.

Uses systemd-logind inhibitor locks (https://systemd.io/INHIBITOR_LOCKS/),
the same mechanism Electron apps use for pre-shutdown cleanup: take a
"shutdown" delay lock at startup, blank the panel when logind announces
PrepareForShutdown(true), then release the lock so shutdown proceeds.
logind waits for the release up to InhibitDelayMaxSec (default 5s).

Talks sd-bus through ctypes so no D-Bus python package is needed.
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading
from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)

_LOGIN1_DEST = b"org.freedesktop.login1"
_LOGIN1_PATH = b"/org/freedesktop/login1"
_LOGIN1_IFACE = b"org.freedesktop.login1.Manager"

_MESSAGE_HANDLER = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
)

class ShutdownInhibitor:
    """Delay-inhibit shutdown and run a callback when shutdown begins."""

    def __init__(
        self,
        on_shutdown: Callable[[], None],
        *,
        who: str = "p13ctl",
        why: str = "Turn off P13 panel",
        system_bus: bool = True,
        sender: str | None = "org.freedesktop.login1",
    ) -> None:
        self._on_shutdown = on_shutdown
        self._who = who.encode()
        self._why = why.encode()
        self._system_bus = system_bus
        self._sender = sender.encode() if sender else None
        self._lib: ctypes.CDLL | None = None
        self._fd = -1
        self._fd_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # ctypes callback must outlive the sd-bus match slot.
        self._handler = _MESSAGE_HANDLER(self._on_signal)

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name="shutdown-inhibit", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._release()

    def _release(self) -> None:
        with self._fd_lock:
            if self._fd >= 0:
                os.close(self._fd)
                self._fd = -1
                _LOGGER.info("released shutdown inhibitor lock")

    def _on_signal(self, msg: int, _userdata, _ret_error) -> int:
        assert self._lib is not None
        starting = ctypes.c_int(0)
        self._lib.sd_bus_message_read_basic(
            ctypes.c_void_p(msg), ord("b"), ctypes.byref(starting)
        )
        if starting.value:
            _LOGGER.info("PrepareForShutdown received; blanking panel")
            try:
                self._on_shutdown()
            except Exception:  # noqa: BLE001 - never break the bus loop
                _LOGGER.exception("shutdown callback failed")
            self._release()
        return 0

    def _run(self) -> None:
        try:
            lib = ctypes.CDLL("libsystemd.so.0")
        except OSError:
            _LOGGER.warning("libsystemd not found; shutdown inhibitor disabled")
            return
        self._lib = lib
        lib.sd_bus_process.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        lib.sd_bus_wait.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
        lib.sd_bus_message_read_basic.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char,
            ctypes.c_void_p,
        ]
        lib.sd_bus_message_unref.argtypes = [ctypes.c_void_p]
        lib.sd_bus_flush_close_unref.argtypes = [ctypes.c_void_p]
        lib.sd_bus_match_signal.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
            _MESSAGE_HANDLER,
            ctypes.c_void_p,
        ]

        bus = ctypes.c_void_p()
        opener = lib.sd_bus_open_system if self._system_bus else lib.sd_bus_open_user
        opener.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        if opener(ctypes.byref(bus)) < 0:
            _LOGGER.warning("could not connect to bus; shutdown inhibitor disabled")
            return

        try:
            self._acquire_lock(lib, bus)
            ret = lib.sd_bus_match_signal(
                bus,
                None,
                self._sender,
                _LOGIN1_PATH,
                _LOGIN1_IFACE,
                b"PrepareForShutdown",
                self._handler,
                None,
            )
            if ret < 0:
                _LOGGER.warning("could not match PrepareForShutdown (%d)", ret)
                return
            _LOGGER.info("watching logind for shutdown")
            while not self._stop.is_set():
                ret = lib.sd_bus_process(bus, None)
                if ret < 0:
                    _LOGGER.warning("sd_bus_process failed (%d)", ret)
                    return
                if ret > 0:
                    continue
                lib.sd_bus_wait(bus, ctypes.c_uint64(1_000_000))
        finally:
            lib.sd_bus_flush_close_unref(bus)
            self._release()

    def _acquire_lock(self, lib: ctypes.CDLL, bus: ctypes.c_void_p) -> None:
        reply = ctypes.c_void_p()
        ret = lib.sd_bus_call_method(
            bus,
            _LOGIN1_DEST,
            _LOGIN1_PATH,
            _LOGIN1_IFACE,
            b"Inhibit",
            None,
            ctypes.byref(reply),
            b"ssss",
            b"shutdown",
            self._who,
            self._why,
            b"delay",
        )
        if ret < 0:
            # Racy without the lock, but still worth listening (logind may
            # be missing or the polkit rule denied us).
            _LOGGER.warning("could not take shutdown delay lock (%d)", ret)
            return
        fd = ctypes.c_int(-1)
        lib.sd_bus_message_read_basic(reply, ord("h"), ctypes.byref(fd))
        with self._fd_lock:
            self._fd = os.dup(fd.value)  # reply message owns the original fd
        lib.sd_bus_message_unref(reply)
        _LOGGER.info("holding shutdown delay lock (released after brightness 0)")
