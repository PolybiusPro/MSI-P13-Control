"""HID control channel for the MSI P13."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .msi_p13 import HidError, P13HidController, enable_host_display

__all__ = ["P13HidController", "HidError", "enable_host_display"]

def __getattr__(name: str) -> Any:
    if name in __all__:
        from . import msi_p13

        return getattr(msi_p13, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
