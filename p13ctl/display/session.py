"""Track active display sessions so only one client holds the USB interface."""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

import usb.core
import usb.util

from .artinchip import DISPLAY_INTERFACE, PRODUCT_ID, VENDOR_ID, DisplayError

if TYPE_CHECKING:
    from .artinchip import ArtinchipDisplay
    from .evdi_bridge import EvdiBridge

_LOGGER = logging.getLogger(__name__)

_lock = threading.Lock()
_active_bridge: EvdiBridge | None = None
_active_display: ArtinchipDisplay | None = None

def register_bridge(bridge: EvdiBridge) -> None:
    global _active_bridge
    with _lock:
        _active_bridge = bridge

def unregister_bridge(bridge: EvdiBridge) -> None:
    global _active_bridge
    with _lock:
        if _active_bridge is bridge:
            _active_bridge = None

def register_display(display: ArtinchipDisplay) -> None:
    global _active_display
    with _lock:
        _active_display = display

def unregister_display(display: ArtinchipDisplay) -> None:
    global _active_display
    with _lock:
        if _active_display is display:
            _active_display = None

def stop_active_sessions() -> None:
    """Cooperatively stop streaming loops that hold the USB display."""
    with _lock:
        bridge = _active_bridge
        display = _active_display
    if bridge is not None:
        bridge.running = False
    if display is not None:
        display.request_stop()

def wait_for_display_usb(timeout: float = 8.0) -> None:
    """Wait until interface 0 can be claimed (mirror/sysmon fully released)."""
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        stop_active_sessions()
        dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
        if dev is None:
            raise DisplayError("Artinchip display 33c3:0e02 not found")
        try:
            if dev.is_kernel_driver_active(DISPLAY_INTERFACE):
                dev.detach_kernel_driver(DISPLAY_INTERFACE)
            usb.util.claim_interface(dev, DISPLAY_INTERFACE)
            usb.util.release_interface(dev, DISPLAY_INTERFACE)
            return
        except usb.core.USBError as exc:
            last_error = exc
            if getattr(exc, "errno", None) != 16:
                raise DisplayError(f"USB error: {exc}") from exc
            try:
                usb.util.dispose_resources(dev)
            except (TypeError, usb.core.USBError):
                usb.util.dispose_resources()
            time.sleep(0.25)
    detail = str(last_error) if last_error else "timeout"
    raise DisplayError(
        "Display USB interface is busy. Stop the desktop mirror and system monitor, "
        f"then try again ({detail})."
    )
