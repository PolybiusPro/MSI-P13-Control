"""Blank the panel while the desktop displays sleep."""

from __future__ import annotations

import logging
import re
import shutil
import signal
import subprocess
import threading

from .artinchip import DisplayError
from .layout import (
    _strip_ansi,
    blank_panel_off,
    is_evdi_output,
    is_physical_output,
    restore_saved_brightness,
)
from .mode import MODE_OFF, get_saved_mode

_LOGGER = logging.getLogger(__name__)

_DPMS_LINE = re.compile(r"dpms mode for screen (\S+): (\w+)", re.IGNORECASE)

def physical_dpms_asleep() -> bool | None:
    """True if every physical output reports DPMS off; None when unknown."""
    doctor = shutil.which("kscreen-doctor")
    if doctor is None:
        return None
    try:
        result = subprocess.run(
            [doctor, "--dpms", "show"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    states = [
        state.lower()
        for name, state in _DPMS_LINE.findall(_strip_ansi(result.stdout))
        if is_physical_output(name) and not is_evdi_output(name)
    ]
    if not states:
        return None
    return all(state == "off" for state in states)

def _sync_panel(asleep: bool) -> None:
    mode = get_saved_mode()["name"]
    if mode == MODE_OFF:
        # The off mode must stay dark when the desktop wakes.
        return
    _LOGGER.info("panel %s with desktop display power", "sleep" if asleep else "wake")
    try:
        if asleep:
            blank_panel_off()
        else:
            restore_saved_brightness()
    except DisplayError as exc:
        _LOGGER.warning("could not sync panel brightness: %s", exc)

def run_sleep_watch(*, interval: float = 10.0) -> int:
    """Poll desktop DPMS and blank/restore the panel on transitions."""
    stop = threading.Event()
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())

    print("Watching desktop display power (Ctrl+C to stop)...")
    asleep = False
    while not stop.is_set():
        state = physical_dpms_asleep()
        if state is not None and state != asleep:
            asleep = state
            _sync_panel(asleep)
        stop.wait(interval)
    return 0
