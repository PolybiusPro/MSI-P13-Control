"""Helpers for coordinating with the saved display-mode user service."""

from __future__ import annotations

import shutil
import subprocess

from .artinchip import DisplayError

DISPLAY_SERVICE = "p13-display.service"

def stop_display_service(*, timeout: float = 10.0) -> bool:
    """Stop the managed display process if it currently owns the panel."""
    if shutil.which("systemctl") is None:
        return False
    try:
        active = subprocess.run(
            ["systemctl", "--user", "is-active", "--quiet", DISPLAY_SERVICE],
            timeout=3,
        )
        if active.returncode != 0:
            return False
        subprocess.run(
            ["systemctl", "--user", "stop", DISPLAY_SERVICE],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DisplayError(f"could not stop {DISPLAY_SERVICE}: {exc}") from exc
    return True
